"""统一错误响应：三类错误与未捕获异常都应返回同一种形状。"""

import asyncio
import json

from fastapi import HTTPException


def test_validation_error_is_unified(client):
    response = client.post("/api/auth/register", json={"email": "not-an-email", "name": "x", "password": "1"})
    assert response.status_code == 422
    body = response.json()
    assert body["error_code"] == "VAL_001"
    # 原始 detail 保留（前端用它定位字段），同时给出人能读的一句话
    assert isinstance(body["detail"], list)
    assert "password" in body["message"] or "email" in body["message"]
    assert body["request_id"] and body["request_id"] != "-"


def test_http_exception_keeps_detail_and_adds_fields(client, auth_headers):
    response = client.get("/api/agents/999999", headers=auth_headers)
    assert response.status_code == 404
    body = response.json()
    # 既有契约：detail 原样保留，测试与前端都在读它
    assert body["detail"] == "Agent not found"
    # 新增：统一的 message / error_code / request_id
    assert body["message"] == "Agent not found"
    assert body["error_code"] == "HTTP_404"
    assert body["request_id"]


def test_dict_detail_keeps_structure():
    """detail 是 {"message": ...} 字典时原样保留，同时抬出 message 与 error_code。"""
    from api.main import http_exception_handler

    exc = HTTPException(status_code=409, detail={"message": "该文档正在索引中，请稍候。", "error_code": "doc_indexing"})
    response = asyncio.run(http_exception_handler(object(), exc))
    body = json.loads(response.body)
    assert response.status_code == 409
    assert body["detail"] == {"message": "该文档正在索引中，请稍候。", "error_code": "doc_indexing"}
    assert body["message"] == "该文档正在索引中，请稍候。"
    assert body["error_code"] == "doc_indexing"


def test_unhandled_exception_returns_json_500(client):
    """兜底处理器已注册到 app，且响应不含堆栈与原始异常文本。"""
    from fastapi import FastAPI

    import api.main as main

    handler = main.app.exception_handlers.get(Exception)
    assert handler is not None, "未注册兜底异常处理器"

    class Boom(Exception):
        pass

    class StubRequest:
        method = "GET"

        class url:
            path = "/api/boom"

    response = asyncio.run(handler(StubRequest(), Boom("boom")))
    assert isinstance(main.app, FastAPI)
    assert response.status_code == 500
    body = json.loads(response.body)
    assert body["error_code"] == "SYS_001"
    assert body["message"] == "内部服务异常，请稍后重试。"
    assert "boom" not in response.body.decode()
