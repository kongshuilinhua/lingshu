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
from api.rate_limit import redis_rate_limit
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
            elif event["event"] == "token_reset":
                # 工具循环里模型先说了一段话、随后决定调工具：前端把已显示的正文收进过程说明
                yield sse_event("token_reset", {"content": event.get("content", "")})
            elif event["event"] == "runtime":
                # 节点执行中途实时推送的运行事件（如工具循环里的 tool_call / search_status）
                runtime_event = event["data"]
                yield sse_event(runtime_event.get("event", "tool_call"), runtime_event.get("data", {}))
            elif event["event"] == "step":
                step = event["step"]
                if not step.get("events_streamed"):
                    for runtime_event in step.get("events", []):
                        yield sse_event(runtime_event.get("event", "tool_call"), runtime_event.get("data", {}))
                yield sse_event("run_step", step)
            elif event["event"] == "complete":
                run = event["run"]
                answer = event["answer"]
                sources = event["sources"]
            elif event["event"] == "paused":
                # HumanApproval 暂停：不能再往下走 done——此时没有 final answer，
                # run 也还在 paused 状态，前端应停下来等审批。
                yield sse_event("paused", event.get("data", {}))
                return
            elif event["event"] == "memory_compaction":
                yield sse_event("memory_compaction", event["data"])
        if sources:
            yield sse_event("sources", {"items": sources})
        assistant = Message(session_id=session.id, role="assistant", content=answer, sources=sources, run_id=run.id if run is not None else None)
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


def stream_resume_events(db: Session, run: Run, approved: bool) -> Iterable[str]:
    """从 HumanApproval 暂停点继续执行，并把结果作为一条新的助手消息落库。"""
    session = db.get(ChatSession, run.session_id)
    agent = db.get(Agent, run.agent_id)
    if not session or not agent:
        yield sse_event("error", {"message": "会话或智能体不存在"})
        return
    state = dict(run.resume_state or {})
    user_message = str((state.get("context") or {}).get("input") or "")
    runner = WorkflowRunner(db)
    answer = ""
    sources = []
    rejected = False
    try:
        for event in runner.resume_events(
            run=run, agent=agent, chat_session=session,
            user_message=user_message, approved=approved,
        ):
            if event["event"] == "token":
                yield sse_event("token", {"content": event.get("content", "")})
            elif event["event"] == "thinking_token":
                yield sse_event("thinking_token", {"content": event.get("content", "")})
            elif event["event"] == "token_reset":
                yield sse_event("token_reset", {"content": event.get("content", "")})
            elif event["event"] == "runtime":
                runtime_event = event["data"]
                yield sse_event(runtime_event.get("event", "tool_call"), runtime_event.get("data", {}))
            elif event["event"] == "step":
                step = event["step"]
                if not step.get("events_streamed"):
                    for runtime_event in step.get("events", []):
                        yield sse_event(runtime_event.get("event", "tool_call"), runtime_event.get("data", {}))
                yield sse_event("run_step", step)
            elif event["event"] == "complete":
                answer = event["answer"]
                sources = event["sources"]
            elif event["event"] == "rejected":
                rejected = True
            elif event["event"] == "paused":
                # 恢复路径上再次遇到审批点（链路里有两个 HumanApproval）
                yield sse_event("paused", event.get("data", {}))
                return
            elif event["event"] == "memory_compaction":
                yield sse_event("memory_compaction", event["data"])
        if rejected:
            yield sse_event("rejected", {"run_id": run.id, "session_id": session.id})
            return
        if sources:
            yield sse_event("sources", {"items": sources})
        assistant = Message(session_id=session.id, role="assistant", content=answer, sources=sources, run_id=run.id if run is not None else None)
        db.add(assistant)
        db.commit()
        db.refresh(assistant)
        yield sse_event("done", {"session_id": session.id, "message_id": assistant.id, "run_id": run.id, "content": answer})
    except Exception as exc:
        db.rollback()
        try:
            run.status = "failed"
            run.completed_at = datetime.now(timezone.utc)
            db.commit()
        except Exception:
            db.rollback()
        logger.exception("Agent chat resume failed")
        yield sse_event("error", safe_stream_error(exc))


# ── Chat Stream ──

@router.post("/api/agents/{agent_id}/chat/stream", dependencies=[Depends(redis_rate_limit(30, 60, scope="chat-stream"))])
def chat_stream(agent_id: int, request: ChatRequest, background_tasks: BackgroundTasks, membership: WorkspaceMember = Depends(get_current_membership), current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    agent = require_workspace_agent(db, membership.workspace_id, agent_id)
    require_agent_read_access(agent, membership)
    if request.model_override:
        try:
            WorkflowRunner(db).resolve_chat_model(request.model_override.model_dump(), current_user.id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="所选模型不可用或无权访问，请重新选择。") from exc
    return StreamingResponse(stream_chat_events(db, agent, current_user.id, request, background_tasks), media_type="text/event-stream")


@router.post("/api/runs/{run_id}/approve")
def approve_run(run_id: int, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    """放行暂停在 HumanApproval 节点的运行，从下一跳继续执行（SSE 流式返回后续输出）。"""
    run = db.get(Run, run_id)
    if not run or run.workspace_id != membership.workspace_id:
        raise HTTPException(status_code=404, detail="Run not found")
    session = db.get(ChatSession, run.session_id)
    if session:
        require_session_access(session, membership)
    if run.status != "paused":
        raise HTTPException(status_code=409, detail="该运行不在暂停中，无法审批。")
    return StreamingResponse(stream_resume_events(db, run, True), media_type="text/event-stream")


@router.post("/api/runs/{run_id}/reject")
def reject_run(run_id: int, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    """驳回暂停中的运行：不执行后续节点，Run 置 rejected。"""
    run = db.get(Run, run_id)
    if not run or run.workspace_id != membership.workspace_id:
        raise HTTPException(status_code=404, detail="Run not found")
    session = db.get(ChatSession, run.session_id)
    if session:
        require_session_access(session, membership)
    if run.status != "paused":
        raise HTTPException(status_code=409, detail="该运行不在暂停中，无法审批。")
    return StreamingResponse(stream_resume_events(db, run, False), media_type="text/event-stream")


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
    return {
        "run": {
            "id": run.id,
            "status": run.status,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        },
        "items": [
            {
                "id": step.id,
                "node_id": step.node_id,
                "node_type": step.node_type,
                "status": step.status,
                "latency_ms": step.latency_ms,
                "summary": step_summary(step.node_type, step.output or {}),
                "output": step.output,
            }
            for step in steps
        ],
    }


def step_summary(node_type: str, output: dict) -> dict:
    """
    把节点原始输出压成「运行详情」需要看的几项。

    原始 output 里有父块全文、完整草稿等大字段，直接给前端既笨重又难读；
    这里按节点类型挑出排障时真正关心的信息：检索命中了什么、调了哪些工具、用了哪个模型。
    """
    if node_type == "Knowledge":
        status = output.get("rag_status") or {}
        return {
            "reason": status.get("reason"),
            "retrieval_mode": status.get("retrieval_mode"),
            "query": status.get("query"),
            "matched": status.get("matched_chunks"),
            "dense": (status.get("dense") or {}).get("matched"),
            "bm25": (status.get("bm25") or {}).get("matched"),
            "rerank_applied": (status.get("rerank") or {}).get("applied"),
            "cache_hit": (status.get("cache") or {}).get("hit"),
            "refused": bool(output.get("rag_refusal")),
            "sources": [
                {
                    "title": item.get("title"),
                    "snippet": (item.get("snippet") or "")[:160],
                    "score": item.get("score"),
                    "channel": item.get("retrieval_channel"),
                }
                for item in (output.get("sources") or [])[:8]
            ],
        }
    if node_type == "Tool":
        stats = output.get("tool_stats") or {}
        return {
            "total_calls": stats.get("total_calls", 0),
            "tools_used": stats.get("tools_used") or [],
            "calls": [
                {
                    "tool": item.get("tool_name"),
                    "status": item.get("status"),
                    "error_code": item.get("error_code"),
                    "arguments": item.get("arguments"),
                }
                for item in (output.get("tool_trace") or [])[:20]
            ],
        }
    if node_type == "LLM":
        return {
            "model": output.get("model"),
            "thinking_type": output.get("thinking_type"),
            "history_message_count": output.get("history_message_count"),
            "used_profile_memory": output.get("used_profile_memory"),
            "attachment_count": output.get("attachment_count"),
            "draft_chars": len(output.get("draft") or ""),
        }
    if node_type == "Answer":
        return {"citation_count": output.get("citation_count"), "answer_chars": len(output.get("answer") or "")}
    if node_type == "HumanApproval":
        return {"prompt": output.get("approval_prompt")}
    if node_type == "Start":
        return {key: output.get(key) for key in ("rag_enabled", "search_enabled", "attachment_count") if key in output}
    return {}


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
