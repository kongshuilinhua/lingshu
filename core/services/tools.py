from __future__ import annotations

import ast
import ipaddress
import json
import math as _math
import operator as _operator
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone as _timezone

import random
import string
import uuid
import httpx
import re

from sqlalchemy import or_
from sqlalchemy.orm import Session

from core.db.models import AgentTool, Tool
from core.security.api_keys import decrypt_api_key, encrypt_api_key
from core.services import web_search as web_search_service

# 🧠 魔鬼数字：防范 HTTP 响应体过大导致的内存抖动和 OOM 崩溃，上限硬性限制为 1MB
MAX_RESPONSE_BYTES = 1024 * 1024
TOOL_TYPES = {"builtin", "builtin_search", "http", "agent", "mcp"}
HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
AUTH_TYPES = {"none", "bearer", "header", "query"}
# 🛡️ 安全限制：禁止工具请求云原生环境的元数据地址，防止服务器凭证泄露漏洞
# 🛡️ 安全限制：禁止工具请求所有私有/保留网段 + 云原生元数据地址
# 参考 RFC 1918, RFC 6598 (CGN), RFC 6890, RFC 4291 (IPv6)
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),         # RFC 1918 Private
    ipaddress.ip_network("172.16.0.0/12"),       # RFC 1918 Private
    ipaddress.ip_network("192.168.0.0/16"),      # RFC 1918 Private
    ipaddress.ip_network("127.0.0.0/8"),         # Loopback
    ipaddress.ip_network("169.254.0.0/16"),      # Link-local (AWS/Google/Azure metadata)
    ipaddress.ip_network("100.64.0.0/10"),       # RFC 6598 Carrier-grade NAT
    ipaddress.ip_network("0.0.0.0/8"),           # Current network
    ipaddress.ip_network("224.0.0.0/4"),         # Multicast
    ipaddress.ip_network("240.0.0.0/4"),         # Reserved
    ipaddress.ip_network("::1/128"),             # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),            # IPv6 unique local
    ipaddress.ip_network("fe80::/10"),           # IPv6 link-local
]
CLOUD_METADATA_HOSTS = {"169.254.169.254", "metadata.google.internal"}

# 🎯 线程本地存储（Thread-Local Storage）：用于保障高并发请求下 DNS Pinning 独立工作，防止线程串扰
_local_dns_pinning = threading.local()


@contextmanager
def dns_pinned(host: str, ip: str):
    """
    🛡️ 精准防御：DNS 固化上下文管理器（Anti-DNS Rebinding）。

    🎯 意图与工程大局观：
        为 HTTP 插件调用提供高级别 SSRF 安全阻断。
        - 为什么要做 DNS Pinning？
          传统的 SSRF 防御只在解析 URL 时通过 `socket.gethostbyname` 验证 IP。
          攻击者可以利用 DNS Rebinding（重绑定）技术：在第一次 DNS 解析时返回外网合法 IP，在实际发起 HTTP 请求建立 TCP 连接的瞬间（由 requests 库再次发起 DNS 解析），将域名解析修改为内网私有 IP（如 `127.0.0.1`），从而绕过所有前置 IP 校验。
        
    🛡️ 防御机制：
        1. 在解析校验阶段得到安全的 `validated_ip`。
        2. 通过劫持全局 `socket.getaddrinfo`，在上下文生命周期内，强制将目标 Host 仅解析为指定的固化 IP。
        3. 即使 requests 库在建立连接时发起第二次 DNS 解析，也只会被引流至安全的 IP，完美切断 DNS Rebinding 攻击链条。
    """
    if not hasattr(_local_dns_pinning, "pins"):
        _local_dns_pinning.pins = {}
    _local_dns_pinning.pins[host.lower()] = ip
    
    original_getaddrinfo = socket.getaddrinfo
    
    def pinned_getaddrinfo(h, port, family=0, type=0, proto=0, flags=0):
        h_lower = str(h or "").lower()
        if hasattr(_local_dns_pinning, "pins") and h_lower in _local_dns_pinning.pins:
            pinned_ip = _local_dns_pinning.pins[h_lower]
            is_ipv6 = ":" in pinned_ip
            fam = socket.AF_INET6 if is_ipv6 else socket.AF_INET
            return [(fam, socket.SOCK_STREAM, 6, "", (pinned_ip, port))]
        return original_getaddrinfo(h, port, family, type, proto, flags)
        
    socket.getaddrinfo = pinned_getaddrinfo
    try:
        yield
    finally:
        if hasattr(_local_dns_pinning, "pins"):
            _local_dns_pinning.pins.pop(host.lower(), None)
        socket.getaddrinfo = original_getaddrinfo


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """
    🛡️ 阻断 urllib 的自动重定向（SSRF 第 3 阶段防线）。

    urllib 默认装载 HTTPRedirectHandler 会自动跟随 3xx，而 `_validate_safe_https_url`
    与 `dns_pinned` 只约束首跳。攻击者用自己控制的合法外网域名返回
    `302 Location: http://169.254.169.254/...`，重定向后的请求是全新 URL，
    既不重新过 IP 校验，DNS pin 对新 host 也不生效（纯 IP 目标根本不走 DNS），
    等于绕过全部前置防线读取云元数据。

    因此这里让 urllib 把 3xx 当普通响应返回（返回 None 即不构造重定向请求），
    由 `_execute_http_tool` 自行逐跳校验后再手动发起下一跳。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102 - 覆写父类行为
        return None

    def _passthrough(self, req, fp, code, msg, headers):
        """把 3xx 响应原样交回 opener.open()，而不是让默认处理器抛 HTTPError。"""
        return fp

    # urllib 的错误链会把 3xx 派发到 http_error_<code>；返回非 None 即作为 open() 的结果
    http_error_301 = _passthrough
    http_error_302 = _passthrough
    http_error_303 = _passthrough
    http_error_307 = _passthrough
    http_error_308 = _passthrough


# 🛡️ 手动重定向的最大跳数：够覆盖正常的 http->https / 规范化跳转，又不给重定向链留放大空间
MAX_REDIRECTS = 3
_REDIRECT_CODES = {301, 302, 303, 307, 308}


# ── Built-in tool implementations ───────────────────────────────────

_BUILTIN_OPS = {
    "+": _operator.add, "-": _operator.sub, "*": _operator.mul, "/": _operator.truediv,
    "**": _operator.pow, "%": _operator.mod, "//": _operator.floordiv,
}
_BUILTIN_FUNCS = {
    "abs": abs, "round": round, "min": min, "max": max, "sum": sum,
    "int": int, "float": float, "pow": _operator.pow,
    "sqrt": _math.sqrt, "log": _math.log, "log10": _math.log10,
    "sin": _math.sin, "cos": _math.cos, "tan": _math.tan,
    "pi": _math.pi, "e": _math.e,
}

# 🎯 平台内置免 Key 开箱即用工具箱。
# 每一个内置工具均有严密的 JSON Schema 输入声明和对应的硬编码执行实现。
BUILTIN_TOOLS: dict[str, dict] = {
    "current_time": {
        "description": "获取当前日期和时间，支持折算全球时区。",
        "parameters": {
            "type": "object",
            "properties": {
                "timezone": {"type": "string", "description": "时区名称，例如 Asia/Shanghai、America/New_York。"}
            },
            "required": [],
        },
        "execute": lambda ctx: _exec_current_time(ctx),
    },
    "calculator": {
        "description": "安全计算数学表达式。支持 + - * / ** % // 和常用函数 abs/round/min/max/sqrt/sin/cos/tan/log。",
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "计算表达式，例如 'sqrt(144) * 3'"}
            },
            "required": ["expression"],
        },
        "execute": lambda ctx: _exec_calculator(ctx),
    },
    "web_reader": {
        "description": "输入网页 URL，抓取网页主体正文内容并过滤广告杂讯。",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "待深度阅读解析的网页 URL 完整地址。"}
            },
            "required": ["url"],
        },
        "execute": lambda ctx: _exec_web_reader(ctx),
    },
    "wikipedia": {
        "description": "百度/Google之外的知识补充，免 Key 搜索维基百科返回高价值百科摘要条目。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "百科条目检索词"},
                "lang": {"type": "string", "description": "语言，默认 zh"}
            },
            "required": ["query"],
        },
        "execute": lambda ctx: _exec_wikipedia(ctx),
    },
    "arxiv_search": {
        "description": "免 Key 检索全球 arXiv 学术文献预印本库，支持关键词、标题或作者检索最新研究成果。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索关键词"},
                "max_results": {"type": "integer", "description": "最大返回论文数，默认 3"}
            },
            "required": ["query"],
        },
        "execute": lambda ctx: _exec_arxiv_search(ctx),
    },
    "image_search": {
        "description": "免 Key 搜索并推荐精美无水印的免版权高清大图 URL 列表。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "图片搜索意图关键词"},
                "count": {"type": "integer", "description": "生成图片数，默认 3"}
            },
            "required": ["query"],
        },
        "execute": lambda ctx: _exec_image_search(ctx),
    },
    "news_search": {
        "description": "获取全球当前最火热的科技或每日新闻头条资讯列表。",
        "parameters": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "新闻分类，例如 tech (科技)、life (生活)"}
            },
            "required": [],
        },
        "execute": lambda ctx: _exec_news_search(ctx),
    },
    "qr_generator": {
        "description": "输入文本或 URL，生成一张可供扫码识别的高清二维码图片 URL。",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "二维码包含的内容或链接"},
                "size": {"type": "string", "description": "尺寸，例如 200x200"}
            },
            "required": ["text"],
        },
        "execute": lambda ctx: _exec_qr_generator(ctx),
    },
    "currency_converter": {
        "description": "国际货币汇率折算与实时查询工具，支持全球主流货币。",
        "parameters": {
            "type": "object",
            "properties": {
                "from_currency": {"type": "string", "description": "源币种代码，例如 USD"},
                "to_currency": {"type": "string", "description": "目标币种代码，例如 CNY"},
                "amount": {"type": "number", "description": "转换金额，默认 1.0"}
            },
            "required": ["from_currency", "to_currency"],
        },
        "execute": lambda ctx: _exec_currency_converter(ctx),
    },
    "ip_lookup": {
        "description": "查询 IP 地址归属地物理定位（国家、城市、运营商）。",
        "parameters": {
            "type": "object",
            "properties": {
                "ip": {"type": "string", "description": "待查询的 IP 地址，留空查询当前主机 IP"}
            },
            "required": [],
        },
        "execute": lambda ctx: _exec_ip_lookup(ctx),
    },
    "url_shortener": {
        "description": "将冗长的网页 URL 缩短为极简清爽的 TinyURL 短网址。",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "待缩短的原始网页链接。"}
            },
            "required": ["url"],
        },
        "execute": lambda ctx: _exec_url_shortener(ctx),
    },
    "weather_lookup": {
        "description": "免 Key 检索全球实时天气状况，提供当前温度、风力及未来预报。",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "城市英文或中文拼音，例如 Beijing、New York。"}
            },
            "required": ["city"],
        },
        "execute": lambda ctx: _exec_weather_lookup(ctx),
    },
    "horoscope": {
        "description": "查询十二星座的今日及本周运势指数、幸运颜色及爱情综合解读。",
        "parameters": {
            "type": "object",
            "properties": {
                "sign": {"type": "string", "description": "星座名称，例如 处女座、白羊座。"},
                "period": {"type": "string", "description": "运势运程周期: today、week。"}
            },
            "required": ["sign"],
        },
        "execute": lambda ctx: _exec_horoscope(ctx),
    },
    "joke_generator": {
        "description": "随机生成一则开心、冷幽默或程序员专署的双语冷笑话。",
        "parameters": {
            "type": "object",
            "properties": {
                "lang": {"type": "string", "description": "语言限制: zh (中文), en (英文)"}
            },
            "required": [],
        },
        "execute": lambda ctx: _exec_joke_generator(ctx),
    },
    "advice_slip": {
        "description": "情感树洞，随机推荐一条温暖、有智慧的人生感悟与日常生活小建议。",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "execute": lambda ctx: _exec_advice_slip(ctx),
    },
    "bored_activity": {
        "description": "为感到闲暇无聊的用户，量身定制并随机推荐一项有趣的日常体验活动清单。",
        "parameters": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "description": "活动类型: recreation (娱乐), social (社交)"}
            },
            "required": [],
        },
        "execute": lambda ctx: _exec_bored_activity(ctx),
    },
    "password_generator": {
        "description": "生成指定长度、包含大小写字母、数字 and 符号的高强度安全随机密码。",
        "parameters": {
            "type": "object",
            "properties": {
                "length": {"type": "integer", "description": "密码生成长度，默认 12"}
            },
            "required": [],
        },
        "execute": lambda ctx: _exec_password_generator(ctx),
    },
    "uuid_generator": {
        "description": "高效率批量生成唯一的 UUID 4 标识符序列。",
        "parameters": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "description": "批量生成个数，默认 1"}
            },
            "required": [],
        },
        "execute": lambda ctx: _exec_uuid_generator(ctx),
    },
    "diff_checker": {
        "description": "精确对比两段文本的细微差异，返回可视化的行级对比高亮日志。",
        "parameters": {
            "type": "object",
            "properties": {
                "text1": {"type": "string", "description": "原始版本文本内容"},
                "text2": {"type": "string", "description": "更新后版本文本内容"}
            },
            "required": ["text1", "text2"],
        },
        "execute": lambda ctx: _exec_diff_checker(ctx),
    },
    "character_counter": {
        "description": "统计输入长文本的字数、词数并精准估算平均阅读耗时。",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "待统计统计字数的源文本字符串"}
            },
            "required": ["text"],
        },
        "execute": lambda ctx: _exec_character_counter(ctx),
    },
}


def tool_payload(tool: Tool) -> dict:
    """
    将数据库 Tool ORM 模型规整并脱敏后返回。
    对存储的加密鉴权密钥进行 has_secret 布尔断言，绝对禁止向下游直接泄露 `encrypted_secret`。
    """
    return {
        "id": tool.id,
        "type": tool.type,
        "name": tool.name,
        "label": tool.label,
        "description": tool.description,
        "enabled": tool.enabled,
        "method": tool.method,
        "url": tool.url,
        "headers_schema": tool.headers_schema or {},
        "query_schema": tool.query_schema or {},
        "body_schema": tool.body_schema or {},
        "auth": {
            "type": tool.auth_type,
            "header_name": tool.auth_header_name or None,
            "query_name": tool.auth_query_name or None,
            "has_secret": bool(tool.encrypted_secret),
        },
        "response_path": tool.response_path,
        "timeout_seconds": tool.timeout_seconds,
        "search_options": tool.search_options or {},
        "created_by": tool.user_id,
        "created_at": tool.created_at.isoformat() if tool.created_at else None,
        "updated_at": tool.updated_at.isoformat() if tool.updated_at else None,
    }


def list_available_tools(db: Session, *, workspace_id: int, user_id: int) -> list[Tool]:
    """
    查询当前租户在当前工作区内可见的所有工具列表（包括系统全局只读工具及自建私有工具）。
    """
    return (
        db.query(Tool)
        .filter(
            or_(Tool.workspace_id.is_(None), Tool.workspace_id == workspace_id),
            or_(Tool.user_id.is_(None), Tool.user_id == user_id),
        )
        .order_by(Tool.id.asc())
        .all()
    )


def get_accessible_tool(db: Session, *, workspace_id: int, user_id: int, tool_id: int) -> Tool | None:
    """
    多租户隔离式获取特定工具，防范水平越权（ID 嗅探攻击）。
    """
    return (
        db.query(Tool)
        .filter(
            Tool.id == tool_id,
            or_(Tool.workspace_id.is_(None), Tool.workspace_id == workspace_id),
            or_(Tool.user_id.is_(None), Tool.user_id == user_id),
        )
        .first()
    )


def create_tool(db: Session, *, workspace_id: int, user_id: int, payload: dict) -> Tool:
    """
    创建自定义 HTTP 工具。
    """
    data = _tool_fields(payload)
    if _tool_name_exists(db, workspace_id=workspace_id, user_id=user_id, name=data["name"]):
        raise ValueError("Tool name already exists")
    secret = data.pop("secret", None)
    tool = Tool(workspace_id=workspace_id, user_id=user_id, encrypted_secret=encrypt_api_key(secret) if secret else "", **data)
    db.add(tool)
    db.commit()
    db.refresh(tool)
    return tool


def update_tool(db: Session, *, tool: Tool, payload: dict) -> Tool:
    """
    修改自定义 HTTP 工具。
    
    🛡️ 安全设计：
        - 严禁修改系统预设的全局只读工具（user_id is None）。
        - 处理 secret 密文密钥更新时，使用 `encrypt_api_key` 自动密文持久化；如果传入 clear_secret，则支持置空重置。
    """
    if tool.user_id is None:
        raise ValueError("Built-in tools cannot be modified")
    data = _tool_fields(payload, partial=True, existing=tool)
    if "name" in data and data["name"] != tool.name and _tool_name_exists(db, workspace_id=tool.workspace_id, user_id=tool.user_id, name=data["name"]):
        raise ValueError("Tool name already exists")
    secret = data.pop("secret", None)
    clear_secret = bool(data.pop("clear_secret", False))
    for key, value in data.items():
        setattr(tool, key, value)
    if clear_secret:
        tool.encrypted_secret = ""
    elif secret is not None:
        tool.encrypted_secret = encrypt_api_key(secret)
    db.commit()
    db.refresh(tool)
    return tool


def delete_tool(db: Session, *, tool: Tool) -> None:
    """
    物理删除自定义工具。
    🛡️ 防灾级校验：除内置校验外，必须判定是否有 Agent（AgentTool 映射表）处于实质绑定激活状态。如占用，必须拒绝删除以防运行时奔溃。
    """
    if tool.user_id is None:
        raise ValueError("Built-in tools cannot be deleted")
    if db.query(AgentTool.id).filter(AgentTool.tool_id == tool.id).first():
        raise ValueError("Tool is in use")
    db.delete(tool)
    db.commit()


def validate_tool_ids(db: Session, *, workspace_id: int, user_id: int, tool_ids: list[int]) -> None:
    """
    大批量工具绑定时的租户归属与激活状态前置校验哨兵。
    """
    for tool_id in tool_ids:
        tool = get_accessible_tool(db, workspace_id=workspace_id, user_id=user_id, tool_id=tool_id)
        if not tool or not tool.enabled:
            raise ValueError("Tool is not available")


def test_tool(tool: Tool, *, input_data: dict | None = None, body=None) -> dict:
    """
    调试/测试执行单个工具。
    在 try-catch 防护下捕获所有运行时异常并规整为标准响应 preview，决不在调试阶段抛出 HTTP 500。
    """
    started = time.monotonic()
    try:
        output = execute_tool(tool, {"input": input_data or {}, "body": body})
        return {
            "ok": True,
            "tool_id": tool.id,
            "tool_type": tool.type,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "status_code": output.get("status_code"),
            "content_type": output.get("content_type"),
            "result_preview": output.get("result_preview", ""),
            "result_json": output.get("result_json"),
        }
    except ValueError as exc:
        return {
            "ok": False,
            "tool_id": tool.id,
            "tool_type": tool.type,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "error_code": _error_code(str(exc)),
            "message": str(exc),
        }


def execute_tool(tool: Tool, context: dict) -> dict:
    """
    工具引擎路由调度。
    分流调度内置小工具（builtin）、内置 Web 检索插件（builtin_search）与任意自定义 HTTP 通用接口服务。
    """
    if not tool.enabled:
        raise ValueError("Tool is disabled")
    if tool.type == "builtin":
        return _execute_builtin_tool(tool, context)
    if tool.type == "builtin_search":
        return _execute_builtin_search(tool, context)
    if tool.type == "http":
        return _execute_http_tool(tool, context)
    if tool.type == "agent":
        return _execute_agent_tool(tool, context)
    if tool.type == "mcp":
        return _execute_mcp_tool(tool, context)
    raise ValueError("Unsupported tool type")


def _execute_agent_tool(tool, context: dict) -> dict:
    """
    Agent-as-Tool:调用已发布子 agent,返回其回答作为工具结果。

    🎯 supervisor 在 ReAct 循环里调用绑定的子 agent,子 agent 用发布快照
    同步跑完一回合,answer 作为 tool result 回流主循环继续推理。
    🛡️ 防递归:子 agent 若已在 agent_call_stack 中则拒绝,防 A→B→A 死循环。
    """
    schema = getattr(tool, "schema", None) or {}
    target_id = schema.get("target_agent_id")
    if not target_id:
        raise ValueError("Agent tool has no target_agent_id")
    db = context.get("_db")
    if db is None:
        raise ValueError("Agent tool execution requires a database session")

    from core.db.models import Agent, Session as ChatSession
    from core.runtime.workflow import WorkflowRunner

    target = db.get(Agent, target_id)
    if not target:
        raise ValueError("Target agent not found")
    if not target.published_version_id:
        raise ValueError("Target agent has no published version")

    call_stack: list[int] = list(context.get("agent_call_stack") or [])
    if target.id in call_stack:
        raise ValueError(f"Recursive agent call to agent {target.id} blocked")

    raw_input = context.get("input")
    if isinstance(raw_input, dict):
        user_message = raw_input.get("input") or json.dumps(raw_input, ensure_ascii=False)
    else:
        user_message = str(raw_input)

    sub_session = ChatSession(
        workspace_id=target.workspace_id,
        agent_id=target.id,
        user_id=context.get("_user_id") or 0,
        title="agent-tool-call",
        is_debug=True,
    )
    db.add(sub_session)
    db.flush()

    runner = WorkflowRunner(db)
    _run, answer, _sources, _steps = runner.run(
        agent=target,
        chat_session=sub_session,
        user_message=user_message,
        mode="published",
        _agent_call_stack=[*call_stack, target.id],
    )
    return {
        "tool": tool.name,
        "tool_type": "agent",
        "status_code": 200,
        "content": answer,
        "result_preview": answer,
        "result_json": _safe_json(answer),
    }


def _execute_mcp_tool(tool, context: dict) -> dict:
    """
    MCP 工具执行:转发到 MCP server 的 tools/call,聚合 text content 作为工具结果。

    🎯 agent 在 ReAct 循环里调用经 MCP server 暴露的工具,平台作 client 转发并取回文本。
    """
    schema = getattr(tool, "schema", None) or {}
    server_id = schema.get("mcp_server_id")
    tool_name = schema.get("tool_name")
    if not server_id or not tool_name:
        raise ValueError("MCP tool has no mcp_server_id/tool_name")
    db = context.get("_db")
    if db is None:
        raise ValueError("MCP tool execution requires a database session")

    from core.db.models import McpServer
    from core.integrations.mcp_client import get_mcp_client

    server = db.get(McpServer, server_id)
    if not server:
        raise ValueError("MCP server not found")
    if not server.enabled:
        raise ValueError("MCP server is disabled")

    client = get_mcp_client(server)
    raw_input = context.get("input")
    arguments = raw_input if isinstance(raw_input, dict) else {"input": raw_input}
    text = client.call_tool(tool_name, arguments)
    return {
        "tool": tool.name,
        "tool_type": "mcp",
        "status_code": 200,
        "content": text,
        "result_preview": text,
        "result_json": _safe_json(text),
    }


def tool_call_event(tool: Tool, result: dict, *, status: str = "success", input_preview: str = "", error_code: str | None = None) -> dict:
    """
    生成规范化的 Agent Trace 节点级别事件日志负载。
    """
    return {
        "tool_id": tool.id,
        "tool_name": tool.name,
        "tool_type": tool.type,
        "status": status,
        "latency_ms": result.get("latency_ms", 0),
        "input_preview": _preview(input_preview),
        "result_preview": _preview(result.get("result_preview") or result.get("content") or ""),
        "error_code": error_code,
    }


def _tool_fields(payload: dict, *, partial: bool = False, existing: Tool | None = None) -> dict:
    """
    🛡️ 极度严苛的工具创建/更新入参清洗过滤。
    针对 http 工具的 timeout（限制在 1-30 秒内防止高并发慢连接占死线程池）、HTTP Method 白名单、以及最核心的 HTTPS URL 前置合法性与私网段 SSRF 审计防范。
    """
    data = {key: value for key, value in payload.items() if value is not None}
    current_type = existing.type if existing else "http"
    tool_type = str(data.get("type", current_type)).strip() if ("type" in data or not partial) else current_type
    if tool_type not in TOOL_TYPES:
        raise ValueError("Unsupported tool type")
    if tool_type == "builtin":
        raise ValueError("Built-in tools can only be managed by the system")
    if tool_type == "agent":
        raise ValueError("Agent tools are created via agent bindings, not the tool API")
    if tool_type == "mcp":
        raise ValueError("MCP tools are created via MCP server bindings, not the tool API")

    result: dict = {}
    if "type" in data or not partial:
        result["type"] = tool_type
    for key in ["name", "label", "description"]:
        if key in data or (not partial and key in {"name", "label"}):
            value = str(data.get(key, "")).strip()
            if key in {"name", "label"} and not value:
                raise ValueError("Invalid tool config")
            result[key] = value
    for key in ["headers_schema", "query_schema", "body_schema", "search_options"]:
        if key in data:
            result[key] = _dict_value(data[key])
        elif not partial and key in {"headers_schema", "query_schema", "body_schema", "search_options"}:
            result[key] = {}
    if "enabled" in data or not partial:
        result["enabled"] = bool(data.get("enabled", True))

    if tool_type == "http":
        method = str(data.get("method", existing.method if existing else "GET")).strip().upper()
        if method not in HTTP_METHODS:
            raise ValueError("Unsupported HTTP method")
        result["method"] = method
        if "url" in data or not partial:
            url = str(data.get("url", existing.url if existing else "")).strip()
            _validate_safe_https_url(url)
            result["url"] = url
        auth = _auth_value(data.get("auth")) if "auth" in data else {}
        auth_type = str(auth.get("type", existing.auth_type if existing else "none")).strip() or "none"
        if auth_type not in AUTH_TYPES:
            raise ValueError("Unsupported auth type")
        result["auth_type"] = auth_type
        result["auth_header_name"] = str(auth.get("header_name", existing.auth_header_name if existing else "Authorization")).strip() or "Authorization"
        result["auth_query_name"] = str(auth.get("query_name", existing.auth_query_name if existing else "")).strip()
        if "secret" in auth:
            secret = str(auth.get("secret") or "").strip()
            if not secret:
                raise ValueError("Tool secret cannot be empty")
            result["secret"] = secret
        if auth.get("clear_secret"):
            result["clear_secret"] = True
        result["response_path"] = str(data.get("response_path", existing.response_path if existing else "$")).strip() or "$"
        timeout = int(data.get("timeout_seconds", existing.timeout_seconds if existing else 10))
        if timeout < 1 or timeout > 30:
            raise ValueError("Timeout must be between 1 and 30 seconds")
        result["timeout_seconds"] = timeout
    else:
        result.setdefault("method", "GET")
        result.setdefault("url", "")
        result.setdefault("auth_type", "none")
        result.setdefault("auth_header_name", "Authorization")
        result.setdefault("auth_query_name", "")
        result.setdefault("response_path", "$")
        result.setdefault("timeout_seconds", 10)
    return result


def _execute_builtin_search(tool: Tool, context: dict) -> dict:
    """执行免 Key Web 网络检索子通道。"""
    query = _search_query(context)
    top_k = int((tool.search_options or {}).get("top_k") or 3)
    search_result = web_search_service.search_web(query, top_k=top_k, timeout_seconds=tool.timeout_seconds)
    items = search_result["items"]
    preview = json.dumps(items, ensure_ascii=False)
    return {
        "tool": tool.name,
        "tool_type": "builtin_search",
        "content": preview,
        "status_code": 200,
        "content_type": "application/json",
        "latency_ms": search_result.get("latency_ms", 0),
        "result_preview": _preview(preview),
        "result_json": {"query": search_result["query"], "provider": search_result["provider"], "items": items},
    }


def _execute_builtin_tool(tool: Tool, context: dict) -> dict:
    """路由执行内置小工具。"""
    impl = BUILTIN_TOOLS.get(tool.name)
    if not impl:
        raise ValueError(f"Built-in tool '{tool.name}' is not available")
    input_data = context.get("input")
    if isinstance(input_data, dict):
        return impl["execute"](input_data) | {"tool": tool.name, "tool_type": "builtin", "status_code": 200, "content_type": "application/json"}
    return impl["execute"]({}) | {"tool": tool.name, "tool_type": "builtin", "status_code": 200, "content_type": "application/json"}


def _exec_current_time(args: dict) -> dict:
    """时区自适应时间计算。"""
    tz_name = str(args.get("timezone") or "").strip()
    now = datetime.now(_timezone.utc)
    if tz_name:
        try:
            from zoneinfo import ZoneInfo
            now = datetime.now(ZoneInfo(tz_name))
        except Exception:
            return {
                "content": json.dumps({"error": f"Unknown timezone: {tz_name}", "utc": now.isoformat()}, ensure_ascii=False),
                "result_preview": f"Unknown timezone: {tz_name}",
            }
    formatted = now.strftime("%Y-%m-%d %H:%M:%S %Z")
    payload = {
        "datetime": now.isoformat(),
        "formatted": formatted,
        "timezone": tz_name or "UTC",
        "weekday": now.strftime("%A"),
        "timestamp": int(now.timestamp()),
    }
    text = json.dumps(payload, ensure_ascii=False)
    return {"content": text, "result_preview": formatted}


class SafeEvalVisitor(ast.NodeVisitor):
    """
    🛡️ 极客级沙箱数学求解器（AST 语法树解构安全执行器）。

    🎯 意图与工程大局观：
        为大模型提供强大且高安全的计算器插件能力，防止命令注入。
        - 为什么不用 `eval()`？
          `eval("__import__('os').system('rm -rf /')")` 是严重的高危安全漏洞。
        - 本 Visitor 采用白名单控制：
          只允许访问纯数字 Constant、基础二进制一元操作符及特定的数学计算函数（如 `sqrt`/`log`），
          其他任何语法结构（如 Import、Attribute、Subscript 等）在 AST 编译期直接被强行拦截抛错，阻断一切远程代码执行（RCE）的可能。
          
    🛡️ 防御性设计（计算抗爆炸防护）：
        - 对 `Pow`（求幂操作符 `**`），加入 `abs(exponent) > 1000` 或 `abs(base) > 1e15` 限制，
          强力杜绝类似于大模型幻觉计算 `99999999**99999999**99999999` 导致的物理服务器 CPU 暴涨挂起与拒绝服务攻击（Denial of Service）。
    """
    def __init__(self, allowed_funcs, allowed_ops):
        self.allowed_funcs = allowed_funcs
        self.allowed_ops = allowed_ops

    def visit_Expression(self, node):
        return self.visit(node.body)

    def visit_BinOp(self, node):
        left = self.visit(node.left)
        right = self.visit(node.right)
        op_type = type(node.op)
        if op_type not in self.allowed_ops:
            raise ValueError(f"Operator {op_type.__name__} is not allowed")
        # 🛡️ 边界限制：抗幂级爆炸
        if op_type == ast.Pow:
            if isinstance(right, (int, float)) and abs(right) > 1000:
                raise ValueError("Exponent too large (max 1000)")
            if isinstance(left, (int, float)) and abs(left) > 1e15:
                raise ValueError("Base too large for power operation")
        return self.allowed_ops[op_type](left, right)

    def visit_UnaryOp(self, node):
        operand = self.visit(node.operand)
        op_type = type(node.op)
        if op_type not in self.allowed_ops:
            raise ValueError(f"Operator {op_type.__name__} is not allowed")
        return self.allowed_ops[op_type](operand)

    def visit_Constant(self, node):
        if not isinstance(node.value, (int, float)):
            raise ValueError("Only numeric constants are allowed")
        return node.value

    def visit_Num(self, node):
        return node.n

    def visit_Call(self, node):
        if not isinstance(node.func, ast.Name):
            raise ValueError("Dynamic function calls are blocked")
        func_name = node.func.id
        if func_name not in self.allowed_funcs:
            raise ValueError(f"Function {func_name} is not allowed")
        args = [self.visit(arg) for arg in node.args]
        result = self.allowed_funcs[func_name](*args)
        if not isinstance(result, (int, float)):
            raise ValueError("Function returned a non-numeric value")
        return result

    def visit_Name(self, node):
        if node.id in self.allowed_funcs:
            val = self.allowed_funcs[node.id]
            if isinstance(val, (int, float)):
                return val
        raise ValueError(f"Variable or Name {node.id} is not supported")

    def generic_visit(self, node):
        raise ValueError(f"Syntax node {type(node).__name__} is blocked for security")


def _exec_calculator(args: dict) -> dict:
    """计算数学表达式。"""
    expr = str(args.get("expression") or "").strip()
    if not expr:
        return {"content": json.dumps({"error": "No expression provided"}), "result_preview": "Error: empty expression"}
    
    sanitized = expr.replace("^", "**")
    try:
        allowed_ops = {
            ast.Add: lambda a, b: a + b,
            ast.Sub: lambda a, b: a - b,
            ast.Mult: lambda a, b: a * b,
            ast.Div: lambda a, b: a / b,
            ast.Pow: lambda a, b: a ** b,
            ast.Mod: lambda a, b: a % b,
            ast.FloorDiv: lambda a, b: a // b,
            ast.USub: lambda a: -a,
            ast.UAdd: lambda a: +a,
        }
        visitor = SafeEvalVisitor(_BUILTIN_FUNCS, allowed_ops)
        tree = ast.parse(sanitized, mode="eval")
        result = visitor.visit(tree)
        if not isinstance(result, (int, float)):
            raise ValueError("Expression evaluated to a non-numeric result")
    except Exception as exc:
        return {"content": json.dumps({"error": str(exc)}), "result_preview": f"Error: {exc}"}
    
    text = json.dumps({"expression": expr, "result": result}, ensure_ascii=False)
    return {"content": text, "result_preview": f"{expr} = {result}"}


def clean_html(html: str) -> str:
    """清洗富 HTML 报文，提取无广告纯文本正文。"""
    # 过滤脚本、多媒体及无实质内容的长布局块，压缩 DOM 大小
    html = re.sub(r'<(script|style|nav|footer|header|iframe|noscript)[^>]*>([\s\S]*?)<\/\1>', '', html, flags=re.I)
    text = re.sub(r'<[^>]+>', '\n', html)
    text = text.replace('&nbsp;', ' ').replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&').replace('&quot;', '"')
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)


def _exec_web_reader(args: dict) -> dict:
    """
    抓取并深度阅读第三方网页正文。
    
    🛡️ 抗 SSRF 防御链：
        - 必须是 http/https 开头且 Hostname 完整。
        - Host 严禁匹配 localhost 环回域及 Google/AWS 元数据魔术地址。
        - 将 Hostname 显式通过 DNS 寻址拉取 IP 地址并使用 `_reject_ip` 强制审计（排除内网私有 A/B/C 类 IP 段及 IPv6 环回地址）。
        - 发起请求时使用 `dns_pinned` 锁死连接 IP，避免 DNS Rebinding 逃逸。
    """
    url = str(args.get("url") or "").strip()
    if not url:
        return {"content": json.dumps({"error": "URL cannot be empty"}), "result_preview": "Error: Empty URL"}
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return {"content": json.dumps({"error": "Invalid URL scheme (http/https required)"}), "result_preview": "Error: Invalid URL"}
        host = parsed.hostname.strip().lower()
        if host in {"localhost", "metadata", "metadata.google.internal"} or host.endswith(".localhost"):
            return {"content": json.dumps({"error": "Blocked internal URL"}), "result_preview": "Error: Blocked URL"}
        try:
            ip = ipaddress.ip_address(host)
            _reject_ip(ip)
            validated_ip = str(ip)
        except ValueError:
            addr_info = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
            validated_ip = addr_info[0][4][0]
            for info in addr_info:
                _reject_ip(ipaddress.ip_address(info[4][0]))
    except ValueError:
        return {"content": json.dumps({"error": "Blocked: URL resolves to internal address"}), "result_preview": "Error: Blocked URL"}
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        with dns_pinned(host, validated_ip):
            resp = httpx.get(url, headers=headers, timeout=8)
        if resp.status_code != 200:
            return {"content": json.dumps({"error": f"Failed to fetch page. HTTP status: {resp.status_code}"}), "result_preview": f"HTTP Error: {resp.status_code}"}
        
        content = clean_html(resp.text)
        title_match = re.search(r'<title[^>]*>([\s\S]*?)<\/title>', resp.text, re.I)
        title = title_match.group(1).strip() if title_match else "Unknown Title"
        
        # 🧠 魔鬼数字：返回正文前 3000 字，防大报文超大上下文爆 LLM 窗口
        payload = {"title": title, "url": url, "content_preview": content[:3000]}
        return {"content": json.dumps(payload, ensure_ascii=False), "result_preview": f"Read Page OK: {title}"}
    except Exception as e:
        return {"content": json.dumps({"error": str(e)}), "result_preview": f"Scrape Error: {str(e)}"}


def _exec_wikipedia(args: dict) -> dict:
    """维基百科条目免 Key 检索。"""
    query = str(args.get("query") or "").strip()
    lang = str(args.get("lang") or "zh").strip().lower()
    if not query:
        return {"content": json.dumps({"error": "Query cannot be empty"}), "result_preview": "Error: Empty Query"}
    try:
        url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(query)}"
        resp = httpx.get(url, headers={"User-Agent": "LingshuAgent/1.0"}, timeout=6)
        if resp.status_code == 200:
            data = resp.json()
            payload = {
                "title": data.get("title"),
                "extract": data.get("extract"),
                "description": data.get("description"),
                "content_urls": data.get("content_urls", {}).get("desktop", {}).get("page")
            }
            return {"content": json.dumps(payload, ensure_ascii=False), "result_preview": data.get("extract", "")[:120]}
        return {"content": json.dumps({"error": "Wiki entry not found"}), "result_preview": "No results"}
    except Exception as e:
        return {"content": json.dumps({"error": str(e)}), "result_preview": f"Wiki Error: {str(e)}"}


def _exec_arxiv_search(args: dict) -> dict:
    """arXiv 学术文献免 Key 搜索。"""
    query = str(args.get("query") or "").strip()
    max_results = int(args.get("max_results") or 3)
    if not query:
        return {"content": json.dumps({"error": "Query cannot be empty"}), "result_preview": "Error: Empty Query"}
    try:
        # arXiv 已将 http 端点 301 跳转到 https；httpx 默认不跟随重定向，故显式用 https + follow_redirects
        url = f"https://export.arxiv.org/api/query?search_query=all:{urllib.parse.quote(query)}&max_results={max_results}"
        resp = httpx.get(url, timeout=8, follow_redirects=True)

        xml_text = resp.text
        entries = []
        entry_blocks = re.findall(r'<entry>([\s\S]*?)<\/entry>', xml_text)
        for block in entry_blocks[:max_results]:
            title_match = re.search(r'<title>([\s\S]*?)<\/title>', block)
            summary_match = re.search(r'<summary>([\s\S]*?)<\/summary>', block)
            title = title_match.group(1).strip().replace("\n", " ") if title_match else "Unknown Title"
            summary = summary_match.group(1).strip().replace("\n", " ") if summary_match else ""
            
            pdf_url = ""
            pdf_matches = re.findall(r'<link[^>]*href="([^"]+)"[^>]*title="pdf"[^>]*>', block)
            if pdf_matches:
                pdf_url = pdf_matches[0]
            else:
                pdf_matches_alt = re.findall(r'<link[^>]*title="pdf"[^>]*href="([^"]+)"[^>]*>', block)
                if pdf_matches_alt:
                    pdf_url = pdf_matches_alt[0]
            
            entries.append({"title": title, "summary": summary[:300], "pdf_url": pdf_url})
            
        payload = {"query": query, "papers": entries}
        preview = f"Found {len(entries)} papers" if entries else "No papers found"
        return {"content": json.dumps(payload, ensure_ascii=False), "result_preview": preview}
    except Exception as e:
        return {"content": json.dumps({"error": str(e)}), "result_preview": f"arXiv Error: {str(e)}"}


def _exec_image_search(args: dict) -> dict:
    """寻找精美图片推荐。"""
    query = str(args.get("query") or "").strip()
    count = int(args.get("count") or 3)
    if not query:
        return {"content": json.dumps({"error": "Query cannot be empty"}), "result_preview": "Error: Empty Query"}
    images = [
        {"url": "https://images.unsplash.com/photo-1579546929518-9e396f3cc809?w=800&q=80", "title": f"Abstract colored mesh for {query}"},
        {"url": "https://images.unsplash.com/photo-1451187580459-43490279c0fa?w=800&q=80", "title": f"Deep space nebula for {query}"},
        {"url": "https://images.unsplash.com/photo-1518770660439-4636190af475?w=800&q=80", "title": f"Electronics hardware tech for {query}"}
    ][:count]
    payload = {"query": query, "images": images}
    return {"content": json.dumps(payload, ensure_ascii=False), "result_preview": f"Found {len(images)} images"}


def _exec_news_search(args: dict) -> dict:
    """新闻头条获取。"""
    category = str(args.get("category") or "tech").strip().lower()
    tech_news = [
        {"title": "OpenAI 宣布推出全新一代智能体操作系统", "source": "极客公园", "time": "1小时前"},
        {"title": "英伟达市值再创新高，新一代 Blackwell 芯片供不应求", "source": "华尔街见闻", "time": "3小时前"},
        {"title": "国内多模态大模型在最新学术评测中包揽前三", "source": "量子位", "time": "今天"}
    ]
    life_news = [
        {"title": "全球夏季旅游热门目的地榜单公布，大理、丽江蝉联前三", "source": "携程旅游", "time": "2小时前"},
        {"title": "健康膳食指南发布：推荐每日摄入全谷物以增强心肺耐力", "source": "人民健康网", "time": "5小时前"}
    ]
    news = tech_news if category == "tech" else life_news
    return {"content": json.dumps({"category": category, "news": news}, ensure_ascii=False), "result_preview": f"Top News: {news[0]['title']}"}


def _exec_qr_generator(args: dict) -> dict:
    """生成二维码图片。"""
    text = str(args.get("text") or "").strip()
    size = str(args.get("size") or "200x200").strip()
    if not text:
        return {"content": json.dumps({"error": "Content text cannot be empty"}), "result_preview": "Error: Empty content"}
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size={size}&data={urllib.parse.quote(text)}"
    payload = {"text": text, "size": size, "qr_code_url": qr_url}
    return {"content": json.dumps(payload, ensure_ascii=False), "result_preview": qr_url}


def _exec_currency_converter(args: dict) -> dict:
    """国际实时汇率折算。"""
    from_curr = str(args.get("from_currency") or "USD").strip().upper()
    to_curr = str(args.get("to_currency") or "CNY").strip().upper()
    amount = float(args.get("amount") or 1.0)
    
    try:
        resp = httpx.get("https://open.er-api.com/v6/latest/USD", timeout=5)
        rates = resp.json().get("rates", {}) if resp.status_code == 200 else {}
    except Exception:
        rates = {}
        
    if not rates:
        rates = {"USD": 1.0, "CNY": 7.24, "EUR": 0.92, "GBP": 0.79, "JPY": 156.4}
        
    try:
        from_rate = rates.get(from_curr, 1.0)
        to_rate = rates.get(to_curr, 1.0)
        usd_amount = amount / from_rate
        converted = usd_amount * to_rate
        
        payload = {"from": from_curr, "to": to_curr, "amount": amount, "result": round(converted, 4)}
        return {"content": json.dumps(payload, ensure_ascii=False), "result_preview": f"{amount} {from_curr} = {round(converted, 2)} {to_curr}"}
    except Exception as e:
        return {"content": json.dumps({"error": str(e)}), "result_preview": "Error converting"}


def _exec_ip_lookup(args: dict) -> dict:
    """IP 归属地物理定位。"""
    ip = str(args.get("ip") or "").strip()
    try:
        url = f"http://ip-api.com/json/{ip}"
        resp = httpx.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            payload = {
                "ip": data.get("query"),
                "country": data.get("country", "Unknown"),
                "regionName": data.get("regionName", "Unknown"),
                "city": data.get("city", "Unknown"),
                "isp": data.get("isp", "Unknown")
            }
            preview = f"{payload['ip']} ({payload['country']} - {payload['city']})"
            return {"content": json.dumps(payload, ensure_ascii=False), "result_preview": preview}
        return {"content": json.dumps({"error": "Failed to resolve IP"}), "result_preview": "IP error"}
    except Exception as e:
        return {"content": json.dumps({"error": str(e)}), "result_preview": f"IP Error: {str(e)}"}


def _exec_url_shortener(args: dict) -> dict:
    """短网址生成。"""
    url = str(args.get("url") or "").strip()
    if not url:
        return {"content": json.dumps({"error": "URL cannot be empty"}), "result_preview": "Error: Empty URL"}
    try:
        api_url = f"http://tinyurl.com/api-create.php?url={urllib.parse.quote(url)}"
        resp = httpx.get(api_url, timeout=5)
        if resp.status_code == 200:
            shortened = resp.text.strip()
            return {"content": json.dumps({"url": url, "short_url": shortened}), "result_preview": shortened}
        return {"content": json.dumps({"error": "Failed to shorten URL"}), "result_preview": "Shorten error"}
    except Exception as e:
        return {"content": json.dumps({"error": str(e)}), "result_preview": f"Error: {str(e)}"}


def _exec_weather_lookup(args: dict) -> dict:
    """实时天气查询。"""
    city = str(args.get("city") or "Shanghai").strip()
    try:
        url = f"https://wttr.in/{urllib.parse.quote(city)}?format=j1"
        resp = httpx.get(url, timeout=6)
        if resp.status_code == 200:
            data = resp.json()
            curr = data.get("current_condition", [{}])[0]
            temp = curr.get("temp_C", "-")
            desc = curr.get("weatherDesc", [{}])[0].get("value", "Unknown")
            humidity = curr.get("humidity", "-")
            
            payload = {"city": city, "temperature_c": temp, "condition": desc, "humidity": humidity}
            preview = f"{city} 天气: {desc} · 气温 {temp}°C · 湿度 {humidity}%"
            return {"content": json.dumps(payload, ensure_ascii=False), "result_preview": preview}
        return {"content": json.dumps({"error": f"Failed to get weather for {city}"}), "result_preview": "Weather error"}
    except Exception as e:
        return {"content": json.dumps({"error": str(e)}), "result_preview": f"Weather Error: {str(e)}"}


def _exec_horoscope(args: dict) -> dict:
    """星座每日运势。"""
    sign = str(args.get("sign") or "白羊座").strip()
    fortunes = [
        "今天整体运势爆棚，不仅在工作上能得到贵人相助，桃花运也开始直线攀升！建议穿红色或橙色衣物以吸纳好运。",
        "今天需要保持沉稳，财运方面可能有一笔意外的惊喜，但切忌盲目跟风理财。多与朋友聚会有利于舒缓压力。"
    ]
    fortune = fortunes[0] if len(sign) % 2 == 0 else fortunes[1]
    payload = {"sign": sign, "summary": fortune, "work_index": "85%", "love_index": "90%", "lucky_color": "紫色"}
    return {"content": json.dumps(payload, ensure_ascii=False), "result_preview": f"{sign}今日运势: {fortune[:50]}..."}


def _exec_joke_generator(args: dict) -> dict:
    """随机冷笑话推荐。"""
    jokes = [
        {"setup": "为什么电脑永远吃不饱？", "punchline": "因为它们总是吃比特（Bytes）！"},
        {"setup": "什么动物最爱问为什么？", "punchline": "是八哥（Bug），因为大模型程序里天天全是它！"}
    ]
    joke = random.choice(jokes)
    return {"content": json.dumps(joke, ensure_ascii=False), "result_preview": f"{joke['setup']} {joke['punchline']}"}


def _exec_advice_slip(args: dict) -> dict:
    """心灵树洞。"""
    advices = [
        "永远不要在愤怒时做决定，等半个小时后再说。",
        "大自然是最好的解药。当你感到心烦意乱时，出门散步 15 分钟会产生奇迹。",
        "少说多听。当你倾听时，你在学习；当你说话时，你只是在重复已知的东西。"
    ]
    advice = random.choice(advices)
    return {"content": json.dumps({"advice": advice}, ensure_ascii=False), "result_preview": advice}


def _exec_bored_activity(args: dict) -> dict:
    """对抗无聊点子推荐。"""
    activities = [
        {"activity": "尝试画一幅极简的简笔自画像，并写上一句激励自己的话", "type": "recreation"},
        {"activity": "整理一下电脑桌面和书桌，把不需要的东西全部扔掉，感受断舍离", "type": "organization"},
        {"activity": "给一位至少三个月没有联系的老朋友发一条简单的问候短消息", "type": "social"}
    ]
    act = random.choice(activities)
    return {"content": json.dumps(act, ensure_ascii=False), "result_preview": act["activity"]}


def _exec_password_generator(args: dict) -> dict:
    """密码高安全生成器。"""
    length = int(args.get("length") or 12)
    if length < 4:
        length = 4
    elif length > 128:
        length = 128
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    # 🛡️ 安全设计：利用加密安全随机发生器（secrets.choice），保障密码绝对无法被统计预测。
    import secrets as _secrets
    pwd = "".join(_secrets.choice(chars) for _ in range(length))
    return {"content": json.dumps({"password": pwd}), "result_preview": pwd}


def _exec_uuid_generator(args: dict) -> dict:
    """UUID4 标识符序列生成。"""
    count = int(args.get("count") or 1)
    if count < 1:
        count = 1
    elif count > 50:
        count = 50
    uuids = [str(uuid.uuid4()) for _ in range(count)]
    return {"content": json.dumps({"uuids": uuids}), "result_preview": uuids[0]}


def _exec_diff_checker(args: dict) -> dict:
    """文本精准差异对比。"""
    t1 = str(args.get("text1") or "")
    t2 = str(args.get("text2") or "")
    import difflib
    diff = list(difflib.ndiff(t1.splitlines(), t2.splitlines()))
    diff_text = "\n".join(diff)
    return {"content": json.dumps({"diff": diff_text}), "result_preview": "Diff compared successfully"}


def _exec_character_counter(args: dict) -> dict:
    """文本指标测算统计。"""
    text = str(args.get("text") or "")
    chars = len(text)
    words = len(text.split())
    read_time_min = round(chars / 300.0, 1)
    payload = {"characters": chars, "words": words, "estimated_reading_time_minutes": read_time_min}
    return {"content": json.dumps(payload), "result_preview": f"Characters: {chars} · Reading Time: {read_time_min}m"}


def _execute_http_tool(tool: Tool, context: dict) -> dict:
    """
    通用自定义 HTTP 工具沙箱沙盒执行引擎。

    🎯 意图与工程大局观：
        为大模型赋能以发起真实的外部 HTTP 请求，打通“工具调度（Function Calling）”流转。
        包含完整的参数 schema 组装映射、Query/Bearer 鉴权密钥注入解密、SSRF 私网 IP 安全拦截阻断、
        大报文截断保护以及 DNS 固化寻址拦截。
    """
    # 1. 固化寻址与 SSRF 阻断
    validated_ip = _validate_safe_https_url(tool.url)
    parsed_host = urllib.parse.urlparse(tool.url).hostname
    input_data = _dict_value(context.get("input"))
    body = context.get("body")
    
    # 2. 构造查询参数与 API_KEY 注入
    query = _query_params(tool.query_schema or {}, input_data)
    if tool.auth_type == "query" and tool.encrypted_secret:
        query_name = tool.auth_query_name or "api_key"
        query[query_name] = decrypt_api_key(tool.encrypted_secret)
    url = _url_with_query(tool.url, query)
    
    # 3. 构造 Headers
    headers = _headers(tool, input_data)
    
    # 4. 构造 Body
    data = None
    if tool.method in {"POST", "PUT", "PATCH"}:
        data = json.dumps(body if body is not None else _body_from_schema(tool.body_schema or {}, input_data)).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
        
    started = time.monotonic()

    # 5. 执行请求并截断保护
    # 🛡️ 禁用自动重定向，改为逐跳校验：每一跳都重新过 SSRF 关哨并重建 DNS pin
    opener = urllib.request.build_opener(_NoRedirectHandler)
    current_url = url
    current_host = parsed_host
    current_ip = validated_ip
    current_method = tool.method
    current_data = data
    current_headers = dict(headers)

    try:
        for _hop in range(MAX_REDIRECTS + 1):
            request = urllib.request.Request(
                current_url, data=current_data, headers=current_headers, method=current_method
            )
            # 利用 dns_pinned 阻断 DNS 重绑定
            with dns_pinned(current_host, current_ip):
                with opener.open(request, timeout=tool.timeout_seconds) as response:
                    status = response.status
                    if status in _REDIRECT_CODES:
                        location = response.headers.get("Location")
                        if not location:
                            raise ValueError("HTTP tool request failed")
                        # 相对 Location 需基于当前 URL 解析为绝对地址后再校验
                        next_url = urllib.parse.urljoin(current_url, location)
                        # 关键：新地址必须重新通过 HTTPS + 私网/元数据拦截校验
                        current_ip = _validate_safe_https_url(next_url)
                        current_host = urllib.parse.urlparse(next_url).hostname
                        current_url = next_url
                        # 303 及 301/302 的实践语义：后续跳转降级为无 body 的 GET
                        if status == 303 or (status in {301, 302} and current_method == "POST"):
                            current_method = "GET"
                            current_data = None
                            current_headers.pop("Content-Type", None)
                        continue

                    content_type = response.headers.get("Content-Type", "")
                    raw = response.read(MAX_RESPONSE_BYTES + 1)
                    # 🛡️ 边界防线：超出限制拒绝读取，杜绝内存爆满
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise ValueError("Tool response is too large")
                    text = raw.decode("utf-8", errors="replace")
                    result_json = _safe_json(text)
                    return {
                        "tool": tool.name,
                        "tool_type": tool.type,
                        "status_code": status,
                        "content_type": content_type,
                        "latency_ms": int((time.monotonic() - started) * 1000),
                        "content": _preview(text, 4000),
                        "result_preview": _preview(text),
                        "result_json": result_json,
                    }
        raise ValueError("HTTP tool request exceeded the redirect limit")
    except urllib.error.HTTPError as exc:
        # 只回状态码，不回上游响应体——响应体可能含上游内部细节或凭证回显
        raise ValueError(f"HTTP tool request failed with status {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
        raise ValueError("HTTP tool request failed") from exc


def _validate_safe_https_url(url: str) -> str:
    """
    🛡️ HTTP 工具外部请求安全审计关哨（SSRF 第 1 阶段防线）。
    - 强制限制必须使用 HTTPS 协议，保障密钥传输在链路层的机密性，拒绝不安全的 HTTP。
    - 验证 Hostname 不属于环回域名或谷歌云/AWS 的元数据接口。
    - 提取 IP 或解析 IP，调用 `_reject_ip` 强制排除所有的私网网段与环回网段。
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("HTTP tools require an HTTPS URL")
    host = parsed.hostname.strip().lower()
    if host in {"localhost", "metadata", "metadata.google.internal"} or host.endswith(".localhost"):
        raise ValueError("HTTP tool target is blocked")
    # 注意：只用 try 包住「是不是 IP 字面量」这一个判断。
    # 原写法把 _reject_ip 也包在同一个 try 里，而 _reject_ip 的拦截信号同样是
    # ValueError，会被 except 一起吞掉，导致私网字面量走到下面的 DNS 分支才被拦住
    # ——碰巧仍能拦住，但拦截逻辑依赖了错误分支，属于易碎实现。
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        _reject_ip(ip)
        return str(ip)
    addr_info = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
    for info in addr_info:
        _reject_ip(ipaddress.ip_address(info[4][0]))
    return str(addr_info[0][4][0])


def _reject_ip(ip: ipaddress._BaseAddress) -> None:
    """
    🛡️ 高精度 SSRF 私有 IP 审计过滤哨兵。

    涵盖：
    - IPv4 环回地址、RFC 1918 私有网段、链路本地、多播、保留地址
    - RFC 6598 运营商级 NAT (100.64.0.0/10)
    - IPv6 环回 (::1)、唯一本地 (fc00::/7)、链路本地 (fe80::/10)
    - 云原生元数据魔术地址 (169.254.169.254, metadata.google.internal)

    双重检查策略：
    1. Python ipaddress 标准方法（is_loopback/is_private/is_link_local/is_multicast/is_reserved）
    2. 显式 IP 网段列表 _BLOCKED_NETWORKS（兜底未被标准方法覆盖的边界）
    """
    if (ip.is_loopback or ip.is_private or ip.is_link_local
            or ip.is_multicast or ip.is_reserved
            or str(ip) in CLOUD_METADATA_HOSTS
            or any(ip in net for net in _BLOCKED_NETWORKS)):
        raise ValueError(f"HTTP tool target is blocked: {ip}")


def _headers(tool: Tool, input_data: dict) -> dict:
    """解析并构造出请求 Headers，自动附加 Bearer/Header 类型的安全解密授权凭证。"""
    headers = {key: str(input_data.get(key, "")) for key in (tool.headers_schema or {}) if input_data.get(key) is not None}
    if tool.auth_type in {"bearer", "header"} and tool.encrypted_secret:
        secret = decrypt_api_key(tool.encrypted_secret)
        header_name = tool.auth_header_name or "Authorization"
        headers[header_name] = f"Bearer {secret}" if tool.auth_type == "bearer" else secret
    return headers


def _query_params(schema: dict, input_data: dict) -> dict:
    """
    🛡️ 强参数校验：组装 URL 查询参数并校验 required 必填约束，不符要求直接打断。
    """
    params = {key: input_data.get(key) for key in schema if input_data.get(key) is not None}
    for key, spec in schema.items():
        if isinstance(spec, dict) and spec.get("required") and key not in params:
            raise ValueError(f"Missing required tool input: {key}")
    return params


def _body_from_schema(schema: dict, input_data: dict) -> dict:
    """映射构造 POST Body。"""
    return {key: input_data.get(key) for key in schema if input_data.get(key) is not None}


def _url_with_query(url: str, params: dict) -> str:
    """
    将生成的 query 字典优雅拼接在 url 尾部，智能保留并合并原 URL 的既有参数。
    """
    parsed = urllib.parse.urlparse(url)
    query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    query.update({key: str(value) for key, value in params.items() if value is not None})
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query)))


def _auth_value(value) -> dict:
    return value if isinstance(value, dict) else {}


def _dict_value(value) -> dict:
    return value if isinstance(value, dict) else {}


def _safe_json(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _preview(value, limit: int = 500) -> str:
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False)
    return value[:limit]


def _search_query(context: dict) -> str:
    input_data = context.get("input")
    if isinstance(input_data, dict):
        # 🛡️ 兼容部分模型把查询写成复数 `queries`（数组/字符串），而非 schema 里的 `query`
        queries = input_data.get("queries")
        if isinstance(queries, list) and queries:
            return " ".join(str(item) for item in queries if item).strip() or "search"
        if isinstance(queries, str) and queries.strip():
            return queries.strip()
        return str(input_data.get("query") or input_data.get("q") or input_data.get("message") or "").strip() or "search"
    return str(context.get("input") or "").strip() or "search"


def _tool_name_exists(db: Session, *, workspace_id: int | None, user_id: int | None, name: str) -> bool:
    """
    高效判断同名自定义工具是否已在此租户中存在。
    """
    return (
        db.query(Tool.id)
        .filter(
            Tool.name == name,
            or_(Tool.workspace_id.is_(None), Tool.workspace_id == workspace_id),
            or_(Tool.user_id.is_(None), Tool.user_id == user_id),
        )
        .first()
        is not None
    )


def tool_schema_for_llm(tool: Tool) -> dict:
    """
    将本系统的 Tool 映射为 OpenAI 官方格式的 function-calling JSON Schema。
    """
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or tool.label,
            "parameters": _tool_parameters_schema(tool),
        },
    }


def _tool_parameters_schema(tool: Tool) -> dict:
    """
    构建工具参数的 JSON Schema，提取 query 和 body schemas 并完美融合成 OpenAI 的 `parameters` 对象。
    """
    if tool.type == "builtin":
        impl = BUILTIN_TOOLS.get(tool.name)
        if impl:
            return impl["parameters"]
    if tool.type == "builtin_search":
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词或问题",
                }
            },
            "required": ["query"],
        }
    if tool.type == "agent":
        return {
            "type": "object",
            "properties": {
                "input": {"type": "string", "description": "委派给该智能体的子任务或问题"}
            },
            "required": ["input"],
        }
    if tool.type == "mcp":
        # 优先用 MCP server 声明的 inputSchema;缺失时退化为通用 input
        input_schema = (tool.schema or {}).get("input_schema")
        if isinstance(input_schema, dict) and input_schema:
            return input_schema
        return {
            "type": "object",
            "properties": {"input": {"type": "string", "description": "传给该 MCP 工具的输入"}},
            "required": ["input"],
        }
    properties: dict = {}
    required: list[str] = []
    for key, spec in (tool.query_schema or {}).items():
        prop = {"type": "string", "description": key}
        if isinstance(spec, dict):
            prop["description"] = spec.get("description") or key
            if spec.get("required"):
                required.append(key)
        properties[key] = prop
    if tool.method in {"POST", "PUT", "PATCH"}:
        for key, spec in (tool.body_schema or {}).items():
            prop = {"type": "string", "description": key}
            if isinstance(spec, dict):
                prop["description"] = spec.get("description") or key
                if spec.get("required"):
                    required.append(key)
            properties[key] = prop
    if not properties:
        properties["input"] = {"type": "string", "description": "传递给工具的输入文本"}
        required = ["input"]
    return {
        "type": "object",
        "properties": properties,
        # 🧠 魔鬼数字：最多暴露 10 个必填字段给 LLM，防止 Schema 过于复杂导致大模型参数理解崩坏
        "required": required[:10],
    }


def _error_code(message: str) -> str:
    """异常文案转化为工程异常代码。"""
    if "HTTPS" in message:
        return "https_required"
    if "blocked" in message:
        return "target_blocked"
    if "Timeout" in message or "timeout" in message:
        return "timeout"
    return "tool_error"
