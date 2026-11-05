import json
import os
import uuid

import pytest

from core.db.models import KnowledgeBase, KnowledgeDocument, User, Workspace
from core.db.session import SessionLocal, init_db


def test_knowledge_document_segment_config():
    if not os.getenv("TEST_DATABASE_URL"):
        pytest.skip("TEST_DATABASE_URL is required for DB-backed model tests.")

    init_db()
    db = SessionLocal()
    suffix = uuid.uuid4().hex[:12]
    user = workspace = None
    try:
        # 自建 FK 父链（user → workspace → knowledge_base），用例自洽、不依赖测试执行顺序
        user = User(email=f"seg-{suffix}@example.com", name="seg", password_hash="x")
        db.add(user)
        db.flush()
        workspace = Workspace(name=f"seg-{suffix}", slug=f"seg-{suffix}")
        db.add(workspace)
        db.flush()
        kb = KnowledgeBase(workspace_id=workspace.id, name="seg-kb", created_by=user.id)
        db.add(kb)
        db.flush()

        doc = KnowledgeDocument(
            knowledge_base_id=kb.id,
            filename="test.md",
            title="Test Doc",
            content_type="text/markdown",
            source_type="text",
            text="# Title\nHello",
            segment_config={
                "parse_mode": "precise",
                "segment_mode": "hierarchy",
                "hierarchy_level": 3
            }
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        assert doc.segment_config["hierarchy_level"] == 3
    finally:
        # 清理：删 workspace 触发 DB 级联（kb→document），再删 user
        db.rollback()
        try:
            if workspace is not None and workspace.id is not None:
                db.query(Workspace).filter(Workspace.id == workspace.id).delete()
            if user is not None and user.id is not None:
                db.query(User).filter(User.id == user.id).delete()
            db.commit()
        except Exception:
            db.rollback()
        db.close()


def test_calculator_ast_security():
    from core.services.tools import _exec_calculator
    
    # 安全的常规数学表达式
    res1 = _exec_calculator({"expression": "2 + 3 * 4"})
    assert "result" in json.loads(res1["content"])
    assert json.loads(res1["content"])["result"] == 14
    
    # 恶意的或不合法的 Python AST 语法节点（列表推导式、魔术方法等）
    res2 = _exec_calculator({"expression": "[x.__class__ for x in [1]]"})
    assert "error" in json.loads(res2["content"])
    
    # 恶意的代码注入（导入系统模块执行命令）
    res3 = _exec_calculator({"expression": "import os; os.system('echo 1')"})
    assert "error" in json.loads(res3["content"])


def test_builtin_tools_implementation():
    from core.services.tools import (
        _exec_password_generator,
        _exec_uuid_generator,
        _exec_character_counter,
        _exec_diff_checker,
        _exec_currency_converter,
        _exec_qr_generator,
    )
    
    # Test password generator
    pw_res = _exec_password_generator({"length": 16})
    pw_data = json.loads(pw_res["content"])
    assert len(pw_data["password"]) == 16
    
    # Test UUID generator
    uuid_res = _exec_uuid_generator({"count": 5})
    uuid_data = json.loads(uuid_res["content"])
    assert len(uuid_data["uuids"]) == 5
    
    # Test character counter
    char_res = _exec_character_counter({"text": "Hello World"})
    char_data = json.loads(char_res["content"])
    assert char_data["characters"] == 11
    assert char_data["words"] == 2
    
    # Test diff checker
    diff_res = _exec_diff_checker({"text1": "Line 1\nLine 2", "text2": "Line 1\nLine 3"})
    diff_data = json.loads(diff_res["content"])
    assert "diff" in diff_data
    
    # Test currency converter
    curr_res = _exec_currency_converter({"from_currency": "USD", "to_currency": "CNY", "amount": 100})
    curr_data = json.loads(curr_res["content"])
    assert curr_data["from"] == "USD"
    assert curr_data["to"] == "CNY"
    assert curr_data["amount"] == 100
    assert "result" in curr_data
    
    # Test QR generator
    qr_res = _exec_qr_generator({"text": "https://example.com"})
    qr_data = json.loads(qr_res["content"])
    assert "qr_code_url" in qr_data

