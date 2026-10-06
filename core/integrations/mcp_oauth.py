"""Persistent OAuth token storage and browser callback handoff for MCP."""

from __future__ import annotations

import asyncio
import json
import threading
import time
import urllib.parse
from datetime import datetime, timezone

from mcp.client.auth import AuthorizationCodeResult
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from core.db.models import McpOAuthAttempt, McpServer
from core.db.session import SessionLocal
from core.config import get_settings
from core.security.api_keys import decrypt_api_key, encrypt_api_key
from core.security.outbound_http import resolve_public_https


def oauth_redirect_url() -> str:
    settings = get_settings()
    if settings.mcp_oauth_redirect_url:
        return settings.mcp_oauth_redirect_url
    if settings.deployment_mode == "development":
        return "http://127.0.0.1:8000/api/mcp/oauth/callback"
    raise ValueError("请配置 MCP_OAUTH_REDIRECT_URL，地址应为后端的 /api/mcp/oauth/callback。")


class DatabaseTokenStorage:
    """One workspace service credential, encrypted at rest."""

    def __init__(self, server_id: int):
        self.server_id = server_id

    def _read(self) -> dict:
        with SessionLocal() as db:
            server = db.get(McpServer, self.server_id)
            if not server or not server.encrypted_auth:
                return {}
            return json.loads(decrypt_api_key(server.encrypted_auth))

    def _write(self, key: str, value: dict) -> None:
        with SessionLocal() as db:
            server = db.get(McpServer, self.server_id)
            if not server:
                raise ValueError("MCP server no longer exists")
            state = json.loads(decrypt_api_key(server.encrypted_auth)) if server.encrypted_auth else {}
            state[key] = value
            server.encrypted_auth = encrypt_api_key(json.dumps(state))
            db.commit()

    async def get_tokens(self) -> OAuthToken | None:
        data = await asyncio.to_thread(self._read)
        return OAuthToken.model_validate(data["tokens"]) if data.get("tokens") else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        await asyncio.to_thread(self._write, "tokens", tokens.model_dump(mode="json"))

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        data = await asyncio.to_thread(self._read)
        if data.get("client_info"):
            return OAuthClientInformationFull.model_validate(data["client_info"])
        registered = data.get("oauth_client")
        if registered:
            return OAuthClientInformationFull(
                client_id=registered["client_id"], client_secret=registered.get("client_secret") or None,
                redirect_uris=[oauth_redirect_url()], grant_types=["authorization_code", "refresh_token"],
                response_types=["code"], token_endpoint_auth_method=registered.get("method") or "none",
            )
        return None

    async def set_client_info(self, info: OAuthClientInformationFull) -> None:
        await asyncio.to_thread(self._write, "client_info", info.model_dump(mode="json"))


class DatabaseOAuthBridge:
    """A callback can land on any API worker; the SDK waits on shared MySQL state."""

    def __init__(self, server_id: int):
        self.server_id = server_id
        self._ready = threading.Event()
        self.authorization_url = ""
        self.state = ""

    async def redirect_handler(self, authorization_url: str) -> None:
        await asyncio.to_thread(resolve_public_https, authorization_url)
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(authorization_url).query)
        state = (params.get("state") or [""])[0]
        if not state:
            raise ValueError("OAuth authorization URL has no state")

        def save() -> None:
            with SessionLocal() as db:
                db.add(McpOAuthAttempt(
                    server_id=self.server_id, state=state, authorization_url=authorization_url,
                ))
                db.commit()

        await asyncio.to_thread(save)
        self.authorization_url = authorization_url
        self.state = state
        self._ready.set()

    def wait_for_authorization_url(self, timeout: float = 15) -> str:
        if not self._ready.wait(timeout):
            raise TimeoutError("MCP OAuth authorization did not start")
        return self.authorization_url

    async def callback_handler(self) -> AuthorizationCodeResult:
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            def read():
                with SessionLocal() as db:
                    attempt = db.query(McpOAuthAttempt).filter(McpOAuthAttempt.state == self.state).first()
                    if not attempt or attempt.status != "callback_received":
                        return None
                    code = decrypt_api_key(attempt.encrypted_code)
                    issuer = attempt.issuer or None
                    db.delete(attempt)
                    db.commit()
                    return code, issuer

            result = await asyncio.to_thread(read)
            if result:
                return AuthorizationCodeResult(code=result[0], state=self.state, iss=result[1])
            await asyncio.sleep(0.5)
        raise TimeoutError("MCP OAuth callback timed out")


def receive_oauth_callback(state: str, code: str, issuer: str | None) -> None:
    if not state or not code:
        raise ValueError("OAuth callback is incomplete")
    with SessionLocal() as db:
        attempt = db.query(McpOAuthAttempt).filter(McpOAuthAttempt.state == state).first()
        if not attempt or attempt.status != "pending":
            raise ValueError("OAuth authorization was not found")
        created = attempt.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - created).total_seconds() > 300:
            db.delete(attempt)
            db.commit()
            raise ValueError("OAuth authorization expired")
        attempt.encrypted_code = encrypt_api_key(code)
        attempt.issuer = issuer or ""
        attempt.status = "callback_received"
        db.commit()
