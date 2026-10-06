import io
import base64
import json
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.deps import get_current_membership
from api.routes.skills import router
from core.db.base import Base
from core.db.session import get_db
from core.db.models import User, Workspace, WorkspaceMember, Agent


def test_skill_import_versions_bindings_and_access():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(email='api-skill@example.test', name='User', password_hash='test')
        workspace = Workspace(name='Skills', slug='skills-api')
        other = Workspace(name='Other', slug='skills-other')
        db.add_all([user, workspace, other])
        db.flush()
        member = WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role='admin')
        agent = Agent(workspace_id=workspace.id, created_by=user.id, name='Skill agent')
        db.add_all([member, agent])
        db.commit()
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_membership] = lambda: member
        client = TestClient(app)
        source = '---\nname: weekly-report\ndescription: Generate reports\n---\nSECRET_BODY_SENTINEL'
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as package:
            package.writestr('weekly-report/SKILL.md', source)
            package.writestr('weekly-report/references/format.md', 'Reference text')
        created = client.post('/api/skills', json={'name': 'Weekly report', 'package_base64': base64.b64encode(buffer.getvalue()).decode()})
        assert created.status_code == 200, created.text
        skill = created.json()['skill']
        sid, vid = skill['id'], skill['current_version_id']
        assert 'SECRET_BODY_SENTINEL' not in client.get('/api/skills').text
        assert client.put(f'/api/agents/{agent.id}/skill-bindings', json={'items': [{'skill_id': sid, 'version_id': vid}]}).status_code == 200
        assert client.get(f'/api/skills/{sid}/versions/{vid}').json()['version']['instructions'] == 'SECRET_BODY_SENTINEL'
        asset = client.get(f'/api/skills/{sid}/versions/{vid}/files/references/format.md')
        assert base64.b64decode(asset.json()['content_base64']) == b'Reference text'
        changed = client.patch(f'/api/skills/{sid}', json={'instructions': source.replace('SECRET_BODY_SENTINEL', 'NEW_BODY')})
        assert changed.status_code == 200
        assert changed.json()['skill']['current_version_id'] != vid
        assert client.get(f'/api/agents/{agent.id}/skill-bindings').json()['items'][0]['version_id'] == vid
        assert client.delete(f'/api/skills/{sid}').status_code == 409
        forbidden = WorkspaceMember(workspace_id=other.id, user_id=user.id, role='admin')
        app.dependency_overrides[get_current_membership] = lambda: forbidden
        assert client.get('/api/skills').json()['items'] == []
        assert client.get(f'/api/skills/{sid}/versions/{vid}').status_code == 404
        app.dependency_overrides[get_current_membership] = lambda: member
        assert client.put(f'/api/agents/{agent.id}/skill-bindings', json={'items': []}).status_code == 200
        assert client.delete(f'/api/skills/{sid}').status_code == 200
        assert client.get('/api/skills').json()['items'] == []
        assert 'SECRET_BODY_SENTINEL' not in json.dumps(created.json())
    engine.dispose()
