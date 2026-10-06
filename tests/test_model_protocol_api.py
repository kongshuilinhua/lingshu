import io
import json

import pytest

from core.config import get_settings


@pytest.fixture(autouse=True)
def encryption_for_protocol_tests(monkeypatch):
    monkeypatch.setenv('API_KEY_ENCRYPTION_KEY', 'protocol-test-encryption-only')
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize('provider,expected', [('anthropic', 'anthropic'),
    ('openai-compatible', 'openai-compatible'), ('openai', 'openai-compatible')])
def test_register_protocol_bind_and_publish(client, auth_headers, provider, expected):
    created = client.post('/api/user-models', headers=auth_headers, json={
        'display_name': 'Protocol test', 'provider': provider, 'base_url': 'https://example.com/v1',
        'chat_model': 'protocol-test-model', 'api_key': 'test-only-protocol-key',
    })
    assert created.status_code == 200, created.text
    config = created.json()['model_config']
    assert config['provider'] == expected
    assert config['has_api_key'] is True and 'test-only-protocol-key' not in created.text
    agent = client.post('/api/agents', headers=auth_headers,
        json={'name': 'Protocol agent', 'user_model_config_id': config['id']}).json()['agent']
    published = client.post(f"/api/agents/{agent['id']}/publish", headers=auth_headers)
    assert published.status_code == 200, published.text
    assert published.json()['version']['snapshot']['user_model_config']['provider'] == expected
    from core.db.models import Agent, UserModelConfig
    from core.db.session import SessionLocal
    from core.runtime.workflow import WorkflowRunner
    with SessionLocal() as db:
        stored = db.get(UserModelConfig, config['id'])
        assert stored.encrypted_api_key.startswith('fernet:')
        runtime = WorkflowRunner(db)._runtime_agent(db.get(Agent, agent['id']), 'published', stored.user_id)
        assert runtime.runtime_config['provider'] == expected
    patched = client.patch(f"/api/user-models/{config['id']}", headers=auth_headers,
        json={'provider': 'anthropic' if expected != 'anthropic' else 'openai-compatible'})
    assert patched.status_code == 200, patched.text
    assert patched.json()['model_config']['provider'] != expected


def test_protocol_probe_route_preserves_native_request(client, auth_headers, monkeypatch):
    captured = []

    class Response(io.BytesIO):
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.close()

    def opened(request, **kwargs):
        captured.append(request)
        return Response(json.dumps({'data': [{'id': 'claude-test'}], 'has_more': False}).encode())

    monkeypatch.setattr('core.services.user_models.open_public_https', opened)
    result = client.post('/api/user-models/probe-models', headers=auth_headers,
        json={'provider': 'anthropic', 'base_url': 'https://example.com', 'api_key': 'test-only-protocol-key'})
    assert result.status_code == 200 and result.json()['models'] == ['claude-test']
    assert captured[0].full_url == 'https://example.com/v1/models'
    assert captured[0].get_header('X-api-key') == 'test-only-protocol-key'
    assert 'test-only-protocol-key' not in result.text


def test_protocol_validation_keeps_private_targets_blocked(client, auth_headers):
    base = {'display_name': 'Bad', 'chat_model': 'model', 'api_key': 'test-only-key'}
    assert client.post('/api/user-models', headers=auth_headers,
        json={**base, 'provider': 'unsupported', 'base_url': 'https://example.com/v1'}).status_code == 422
    assert client.post('/api/user-models', headers=auth_headers,
        json={**base, 'provider': 'anthropic', 'base_url': 'https://127.0.0.1/v1'}).status_code == 400


def test_anthropic_system_model_is_routed_to_native_protocol(client, auth_headers):
    created = client.post('/api/admin/models', headers=auth_headers,
        json={'model_name': 'claude-system-test', 'display_name': 'Claude system', 'provider': 'anthropic'})
    assert created.status_code == 200, created.text
    model_id = created.json()['model']['id']
    agent = client.post('/api/agents', headers=auth_headers,
        json={'name': 'System Claude', 'model_id': model_id}).json()['agent']
    from core.db.models import Agent
    from core.db.session import SessionLocal
    from core.runtime.workflow import WorkflowRunner
    with SessionLocal() as db:
        stored = db.get(Agent, agent['id'])
        runtime = WorkflowRunner(db)._runtime_agent(stored, 'draft', stored.created_by)
        assert runtime.runtime_config == {'provider': 'anthropic', 'chat_model': 'claude-system-test'}
