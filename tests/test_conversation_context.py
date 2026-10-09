from types import SimpleNamespace
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from core.db.base import Base
from core.db.models import Agent, Message, Session as ChatSession, SessionMemory, User, Workspace
from core.runtime.conversation import conversation_context
from core.runtime.workflow import WorkflowRunner


@pytest.fixture
def dialogue():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(email='context@example.test', name='Owner', password_hash='fixture')
        workspace = Workspace(name='Context', slug='context')
        db.add_all([user, workspace])
        db.flush()
        agent = Agent(workspace_id=workspace.id, created_by=user.id, name='Context Agent')
        db.add(agent)
        db.flush()
        session = ChatSession(workspace_id=workspace.id, agent_id=agent.id, user_id=user.id, title='Context')
        db.add(session)
        db.commit()
        runtime = SimpleNamespace(id=agent.id, workspace_id=workspace.id, system_prompt='System policy.',
                                  settings={'memory': {'enabled': False, 'max_messages': 12}}, skill_bindings=[])
        yield db, runtime, session
    engine.dispose()


def add(db, session, role, content):
    message = Message(session_id=session.id, role=role, content=content, sources=[])
    db.add(message)
    db.commit()
    return message


def test_recent_dialogue_survives_disabled_summary_and_keeps_roles(dialogue):
    db, runtime, session = dialogue
    add(db, session, 'user', 'My project is CONTEXT_PROJECT.')
    add(db, session, 'assistant', 'I will use CONTEXT_PROJECT.')
    current = add(db, session, 'user', 'Continue that task.')
    add(db, session, 'user', 'FUTURE_MESSAGE must not be included')
    db.add(SessionMemory(session_id=session.id, summary='DISABLED_SUMMARY', message_count=2))
    db.commit()
    context = {'input': current.content, **conversation_context(db, runtime, session, current.content, current.id)}
    messages = WorkflowRunner(db)._llm_messages(runtime, context)
    assert [item['role'] for item in messages] == ['system', 'user', 'assistant', 'user']
    assert messages[1]['content'] == 'My project is CONTEXT_PROJECT.'
    assert messages[-1]['content'] == 'Continue that task.'
    assert 'CONTEXT_PROJECT' not in messages[0]['content']
    assert 'DISABLED_SUMMARY' not in str(messages)
    assert 'FUTURE_MESSAGE' not in str(messages)
    assert context['history_message_count'] == 2


def test_history_does_not_cross_user_agent_or_message_boundary(dialogue):
    db, runtime, session = dialogue
    other = ChatSession(workspace_id=session.workspace_id, agent_id=session.agent_id, user_id=session.user_id + 1, title='Other user')
    db.add(other)
    db.commit()
    foreign = add(db, other, 'user', 'OTHER_USER_PRIVATE')
    current = add(db, session, 'user', 'Current request')
    context = conversation_context(db, runtime, session, current.content, current.id)
    assert context['conversation_history'] == []
    with pytest.raises(ValueError, match='当前消息'):
        conversation_context(db, runtime, session, current.content, foreign.id)
    fake = SimpleNamespace(id=other.id, user_id=session.user_id)
    with pytest.raises(ValueError, match='当前会话'):
        conversation_context(db, runtime, fake, 'Request')
    runtime.id += 100
    with pytest.raises(ValueError, match='当前会话'):
        conversation_context(db, runtime, session, 'Request')


def test_recent_window_and_enabled_summary_are_not_duplicated(dialogue):
    db, runtime, session = dialogue
    for index in range(4):
        add(db, session, 'user', f'U{index}')
        add(db, session, 'assistant', f'A{index}')
    runtime.settings['memory'] = {'enabled': True, 'max_messages': 4}
    db.add(SessionMemory(session_id=session.id, summary='{"summary":"OLDER_FACT","turns":[{"user":"U3","assistant":"A3"}]}', message_count=8))
    db.commit()
    context = {'input': 'New task', **conversation_context(db, runtime, session, 'New task')}
    messages = WorkflowRunner(db)._llm_messages(runtime, context)
    assert [item['content'] for item in messages[1:-1]] == ['U2', 'A2', 'U3', 'A3']
    assert 'OLDER_FACT' in messages[0]['content']
    assert 'U3' not in messages[0]['content']


def test_large_history_is_bounded_and_current_request_is_untouched(dialogue):
    db, runtime, session = dialogue
    add(db, session, 'user', 'large ' * 10000)
    add(db, session, 'assistant', 'answer ' * 10000)
    current = add(db, session, 'user', 'LATEST_REQUEST')
    context = conversation_context(db, runtime, session, current.content, current.id)
    assert len(str(context['conversation_history'])) < 40000
    assert all(item['role'] in {'user', 'assistant'} for item in context['conversation_history'])
    assert [item['role'] for item in context['conversation_history']] == ['user', 'assistant']


def test_later_summary_cannot_leak_into_an_earlier_request(dialogue):
    db, runtime, session = dialogue
    add(db, session, 'user', 'Earlier question')
    add(db, session, 'assistant', 'Earlier answer')
    current = add(db, session, 'user', 'Current request')
    runtime.settings['memory']['enabled'] = True
    db.add(SessionMemory(session_id=session.id, summary='LATER_SUMMARY_PRIVATE', message_count=20,
                         updated_at=current.created_at + timedelta(seconds=10)))
    db.commit()
    context = conversation_context(db, runtime, session, current.content, current.id)
    assert context['memory_summary'] == ''
    assert len(context['conversation_history']) == 2
