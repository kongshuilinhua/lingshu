"""灵枢 Agent 平台 —— 聊天、会话、运行、反馈路由。"""

from datetime import datetime, timezone
from collections.abc import Iterable

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from api.access import (
    require_agent_read_access,
    require_session_access,
    require_workspace_agent,
)
from api.deps import get_current_membership, get_current_user
from api.schemas import ChatRequest, FeedbackRequest, SessionUpdateRequest
from api.serializers import message_payload, session_payload
from core.db.models import (
    Agent,
    Feedback,
    Message,
    Run,
    RunStep,
    Session as ChatSession,
    SessionMemory,
    User,
    WorkspaceMember,
)
from core.db.session import get_db
from core.runtime.workflow import WorkflowRunner, compact_session_memory_task
from core.security.permissions import can_manage
from core.tasks.dispatch import dispatch

import json
import logging
import re

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

PUBLIC_CHAT_ERRORS = (
    "Selected model does not support document input",
    "Upload not found or not accessible",
    "Stored API key is invalid",
    "Secure API key encryption is not configured",
    "当前智能体还没有发布版本",
    "发布版本不存在",
    "mode must be draft or published",
    "Model call failed",
    "Model returned an empty answer",
    "Chat model API key is not configured",
    "Embedding API key is not configured",
    "Rerank API key is not configured",
)


def sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _sanitize_public_error(message: str) -> str:
    cleaned = re.sub(r"(?i)(sk-[A-Za-z0-9_-]+|api[_-]?key\s*[:=]\s*\S+|secret\s*[:=]\s*\S+)", "[secret]", str(message))
    return cleaned.replace("\n", " ").replace("\r", " ").strip()[:500]


def safe_stream_error(exc: Exception) -> dict:
    message = str(exc)
    if any(public_error in message for public_error in PUBLIC_CHAT_ERRORS):
        return {"message": _sanitize_public_error(message), "error_code": _error_code(message)}
    return {"message": "智能体运行失败，请检查模型、知识库或附件配置后重试。", "error_code": _error_code(message)}


def _error_code(message: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", message.lower()).strip("_")
    if "model_call_failed" in normalized or "gateway" in normalized:
        return "model_provider_error"
    if "model" in normalized and "image" in normalized:
        return "model_capability_error"
    if "model" in normalized and "document" in normalized:
        return "model_capability_error"
    if "upload" in normalized:
        return "attachment_error"
    if "publish" in normalized or "发布" in message:
        return "agent_version_error"
    if "api_key" in normalized or "secret" in normalized:
        return "secret_config_error"
    return "agent_runtime_error"


def get_or_create_session(db: Session, agent: Agent, user_id: int, session_id: int | None, title_seed: str, is_debug: bool = False) -> ChatSession:
    if session_id:
        session = db.get(ChatSession, session_id)
        if session and session.agent_id == agent.id and session.workspace_id == agent.workspace_id and session.user_id == user_id and session.is_debug == is_debug:
            return session
    session = ChatSession(workspace_id=agent.workspace_id, agent_id=agent.id, user_id=user_id, title=title_seed[:60] or "新对话", is_debug=is_debug)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def stream_chat_events(db: Session, agent: Agent, user_id: int, request: ChatRequest, background_tasks: BackgroundTasks) -> Iterable[str]:
    run = None
    try:
        session = get_or_create_session(db, agent, user_id, request.session_id, request.message, is_debug=getattr(request, "is_debug", False))
        user_message = Message(session_id=session.id, role="user", content=request.message, sources=[])
        db.add(user_message)
        db.commit()
        runner = WorkflowRunner(db)
        answer = ""
        sources = []
        for event in runner.run_events(
            agent=agent, chat_session=session, user_message=request.message, mode=request.mode,
            variables=request.variables, rag_enabled=request.rag_enabled,
            rag_options=request.rag_options.model_dump(exclude_none=True) if request.rag_options else None,
            thinking_enabled=request.thinking_enabled, search_enabled=request.search_enabled,
            model_override=request.model_override.model_dump() if request.model_override else None,
            attachments=request.attachments,
            async_memory=True,
            current_message_id=user_message.id,
        ):
            if event["event"] == "token":
                yield sse_event("token", {"content": event.get("content", "")})
            elif event["event"] == "thinking_token":
                # 🎯 思考轨迹独立通道：只透传给前端实时渲染，不参与 answer 拼装，也绝不写入 Message
                yield sse_event("thinking_token", {"content": event.get("content", "")})
            elif event["event"] == "step":
                step = event["step"]
                for runtime_event in step.get("events", []):
                    yield sse_event(runtime_event.get("event", "tool_call"), runtime_event.get("data", {}))
                yield sse_event("run_step", step)
            elif event["event"] == "complete":
                run = event["run"]
                answer = event["answer"]
                sources = event["sources"]
            elif event["event"] == "memory_compaction":
                yield sse_event("memory_compaction", event["data"])
        if sources:
            yield sse_event("sources", {"items": sources})
        assistant = Message(session_id=session.id, role="assistant", content=answer, sources=sources)
        db.add(assistant)
        db.commit()
        db.refresh(assistant)
        yield sse_event("done", {"session_id": session.id, "message_id": assistant.id, "run_id": run.id, "content": answer})

        if runner.runtime and getattr(runner.runtime, "settings", {}).get("memory", {}).get("enabled"):
            dispatch(
                background_tasks,
                compact_session_memory_task,
                task_name="lingshu.compact_session_memory",
                session_id=session.id,
                user_message=request.message,
                answer=answer,
                max_messages=int(runner.runtime.settings.get("memory", {}).get("max_messages", 12)),
                runtime_config=runner.runtime.runtime_config,
            )
    except Exception as exc:
        if run is not None:
            try:
                run.status = "failed"
                run.completed_at = datetime.now(timezone.utc)
                db.commit()
            except Exception:
                db.rollback()
        logger.exception("Agent chat stream failed")
        yield sse_event("error", safe_stream_error(exc))


# ── Chat Stream ──

@router.post("/api/agents/{agent_id}/chat/stream")
def chat_stream(agent_id: int, request: ChatRequest, background_tasks: BackgroundTasks, membership: WorkspaceMember = Depends(get_current_membership), current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    agent = require_workspace_agent(db, membership.workspace_id, agent_id)
    require_agent_read_access(agent, membership)
    if request.model_override:
        try:
            WorkflowRunner(db).resolve_chat_model(request.model_override.model_dump(), current_user.id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="所选模型不可用或无权访问，请重新选择。") from exc
    return StreamingResponse(stream_chat_events(db, agent, current_user.id, request, background_tasks), media_type="text/event-stream")


# ── Sessions ──

@router.get("/api/agents/{agent_id}/sessions")
def list_agent_sessions(agent_id: int, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    agent = require_workspace_agent(db, membership.workspace_id, agent_id)
    require_agent_read_access(agent, membership)
    query = db.query(ChatSession).filter(ChatSession.workspace_id == membership.workspace_id, ChatSession.agent_id == agent.id, ChatSession.is_debug.is_(False))
    if not can_manage(membership.role):
        query = query.filter(ChatSession.user_id == membership.user_id)
    sessions = query.order_by(ChatSession.updated_at.desc()).all()
    return {"items": [session_payload(session, db) for session in sessions]}


@router.get("/api/sessions/{session_id}")
def get_session(session_id: int, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    session = db.get(ChatSession, session_id)
    if not session or session.workspace_id != membership.workspace_id:
        raise HTTPException(status_code=404, detail="Session not found")
    require_session_access(session, membership)
    messages = db.query(Message).filter(Message.session_id == session.id).order_by(Message.id.asc()).all()
    return {"session": session_payload(session, db), "messages": [message_payload(message) for message in messages]}


@router.patch("/api/sessions/{session_id}")
def patch_session(session_id: int, request: SessionUpdateRequest, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    session = db.get(ChatSession, session_id)
    if not session or session.workspace_id != membership.workspace_id:
        raise HTTPException(status_code=404, detail="Session not found")
    require_session_access(session, membership)
    session.title = request.title.strip()
    db.commit()
    db.refresh(session)
    return {"session": session_payload(session, db)}


@router.delete("/api/sessions/{session_id}")
def delete_session(session_id: int, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    session = db.get(ChatSession, session_id)
    if not session or session.workspace_id != membership.workspace_id:
        raise HTTPException(status_code=404, detail="Session not found")
    require_session_access(session, membership)
    message_ids = [row.id for row in db.query(Message.id).filter(Message.session_id == session.id).all()]
    run_ids = [row.id for row in db.query(Run.id).filter(Run.session_id == session.id).all()]
    if message_ids:
        db.query(Feedback).filter(Feedback.message_id.in_(message_ids)).delete(synchronize_session=False)
    if run_ids:
        db.query(RunStep).filter(RunStep.run_id.in_(run_ids)).delete(synchronize_session=False)
    db.query(SessionMemory).filter(SessionMemory.session_id == session.id).delete(synchronize_session=False)
    db.query(Message).filter(Message.session_id == session.id).delete(synchronize_session=False)
    db.query(Run).filter(Run.session_id == session.id).delete(synchronize_session=False)
    db.delete(session)
    db.commit()
    return {"deleted": True}


# ── Runs ──

@router.get("/api/runs/{run_id}")
def get_run(run_id: int, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    run = db.get(Run, run_id)
    if not run or run.workspace_id != membership.workspace_id:
        raise HTTPException(status_code=404, detail="Run not found")
    session = db.get(ChatSession, run.session_id)
    if session:
        require_session_access(session, membership)
    return {"run": {"id": run.id, "status": run.status, "agent_id": run.agent_id, "session_id": run.session_id}}


@router.get("/api/runs/{run_id}/steps")
def get_run_steps(run_id: int, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    run = db.get(Run, run_id)
    if not run or run.workspace_id != membership.workspace_id:
        raise HTTPException(status_code=404, detail="Run not found")
    session = db.get(ChatSession, run.session_id)
    if session:
        require_session_access(session, membership)
    steps = db.query(RunStep).filter(RunStep.run_id == run.id).order_by(RunStep.id.asc()).all()
    return {"items": [{"id": step.id, "node_id": step.node_id, "node_type": step.node_type, "status": step.status, "output": step.output} for step in steps]}


# ── Feedback ──

@router.post("/api/messages/{message_id}/feedback")
def create_feedback(message_id: int, request: FeedbackRequest, current_user: User = Depends(get_current_user), membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    message = db.get(Message, message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    session = db.get(ChatSession, message.session_id)
    if not session or session.workspace_id != membership.workspace_id:
        raise HTTPException(status_code=404, detail="Message not found")
    require_session_access(session, membership)
    feedback = Feedback(message_id=message.id, user_id=current_user.id, rating=request.rating, comment=request.comment)
    db.add(feedback)
    db.commit()
    db.refresh(feedback)
    return {"feedback": {"id": feedback.id, "rating": feedback.rating, "comment": feedback.comment}}
