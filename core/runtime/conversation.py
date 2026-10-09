"""Session-scoped recent dialogue, independent of optional summary compression."""
from sqlalchemy.orm import Session

from core.db.models import Message, Session as ChatSession, SessionMemory
from core.services.agents import normalize_memory
from core.services.memory_summary import parse_memory, count_str_tokens, soft_truncate


def conversation_context(db: Session, agent, chat_session, user_message: str, current_message_id=None) -> dict:
    session = db.query(ChatSession).filter(
        ChatSession.id == chat_session.id, ChatSession.workspace_id == agent.workspace_id,
        ChatSession.agent_id == agent.id, ChatSession.user_id == chat_session.user_id,
    ).first()
    if not session:
        raise ValueError('当前会话与智能体或用户不匹配。')
    policy = normalize_memory(agent.settings.get('memory'))
    query = db.query(Message).filter(Message.session_id == session.id, Message.role.in_(['user', 'assistant']))
    if current_message_id is not None:
        current = db.get(Message, current_message_id)
        if not current or current.session_id != session.id or current.role != 'user' or current.content != user_message:
            raise ValueError('当前消息与会话不匹配。')
        query = query.filter(Message.id < current_message_id)
    rows = query.order_by(Message.id.desc()).limit(policy['max_messages']).all()
    history = [{'role': row.role, 'content': row.content} for row in reversed(rows)]
    memory = db.query(SessionMemory).filter(SessionMemory.session_id == session.id).first() if policy['enabled'] else None
    if memory and current_message_id is not None and memory.updated_at.replace(tzinfo=None) > current.created_at.replace(tzinfo=None):
        memory = None
    summary, cached_turns = parse_memory(memory.summary if memory else '')
    if memory and not summary and not cached_turns and not memory.summary.strip().startswith(('{', '[')):
        summary = memory.summary
    if not history and memory:
        history = [message for turn in cached_turns for message in
                   ({'role': 'user', 'content': turn['user']}, {'role': 'assistant', 'content': turn['assistant']})][-policy['max_messages']:]
    # Newest dialogue wins; keep a bounded window and never start with an
    # orphaned assistant turn (native Messages requires a user first).
    recent = []
    remaining = 8192
    for message in reversed(history):
        text = soft_truncate(str(message['content']), min(4096, max(1, remaining - 32)))
        size = count_str_tokens(text)
        if size > remaining:
            break
        recent.append({'role': message['role'], 'content': text})
        remaining -= size
        if remaining <= 0:
            break
    recent.reverse()
    while recent and recent[0]['role'] != 'user':
        recent.pop(0)
    return {'conversation_history': recent, 'memory_summary': summary,
            'memory_enabled': policy['enabled'], 'history_message_count': len(recent)}
