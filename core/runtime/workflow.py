from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from sqlalchemy.orm import Session

from core.config import get_settings
from core.db.models import (
    Agent,
    AgentAgentBinding,
    AgentKnowledgeBase,
    AgentMcpBinding,
    AgentVersion,
    McpServer,
    ModelConfig,
    Run,
    RunStep,
    SessionMemory,
    Tool,
    Upload,
    UserModelConfig,
)
from core.db.models import (
    Session as ChatSession,
)
from core.integrations.llm import OpenAICompatibleProvider
from core.integrations.mcp_client import get_mcp_client
from core.services import query_understanding as qu_service
from core.services.agents import (
    get_agent_detail,
    normalize_memory,
    normalize_query_understanding,
    normalize_rag,
    normalize_tool_policy,
)
from core.services.memory import (
    get_memory_profile,
    memory_used_event,
    recall_facts,
    recall_profile_memory,
)
from core.services.memory_summary import (
    build_memory_payload,
    parse_memory,
    summarize_turns,
)
from core.services.rag import retrieve

try:
    from langchain_core.messages.utils import count_tokens_approximately
except ImportError:
    def count_tokens_approximately(msgs):
        return sum(len(m.content) for m in msgs) // 2
from core.runtime.graph import MAX_GRAPH_STEPS, advance, resolve_next
from core.services import web_search as web_search_service
from core.services.models import resolve_agent_model
from core.services.tools import execute_tool, tool_call_event, tool_schema_for_llm
from core.services.uploads import get_workspace_uploads, resolve_image_data_url
from core.services.user_models import (
    resolve_user_model_config,
    user_model_runtime_config,
)
from core.services.web_search import WebSearchError


def default_workflow() -> list[dict]:
    """
    提供全平台默认的线性编排工作流图元。
    
    🎯 意图与工程大局观：
        为了确保 Agent 资产创建时的平滑易用性，系统采用了一套“开箱即用”的标准 RAG 与 ReAct 编排链条：
        接收输入 -> 知识库召回 -> 工具自适应抉择 -> 大语言模型拟稿 -> 最终结果规整输出。
    """
    return [
        {"id": "start", "type": "Start", "name": "接收用户输入", "config": {}},
        {"id": "knowledge", "type": "Knowledge", "name": "检索绑定知识库", "config": {"top_k": 4}},
        {"id": "tool", "type": "Tool", "name": "调用绑定工具", "config": {"tools": []}},
        {"id": "llm", "type": "LLM", "name": "生成候选回答", "config": {}},
        {"id": "answer", "type": "Answer", "name": "输出最终回答", "config": {}},
    ]


class WorkflowRunner:
    """
    智能体运行时状态机总控制器（Agent Workflow Engine）。

    🎯 意图与工程大局观：
        本类是整个平台多智能体协作与工作流执行的核心总枢纽。
        它承载了从多租户配置隔离、长短期记忆召回、文件附件安检、RAG 向量混合检索，
        到 ReAct 多轮工具迭代循环的完整生命周期管理。
        设计上支持两大核心执行管道：
        - `run(...)`: 同步模式，串行跑完所有工作流节点并写入 Run/RunStep 运行轨迹，通常用于离线测试、API 批量调用。
        - `run_events(...)`: 流式 SSE（Server-Sent Events）模式，利用 Python 生成器（Generator）和 `yield` 机制，
          逐个 Token 发射流式文本，并实时推送结构化中间状态事件（知识库检索明细、工具执行耗时），支撑极致流畅的 C 端交互。
    """

    def __init__(self, db: Session) -> None:
        self.db = db
        self.provider = OpenAICompatibleProvider()

    def run(
        self,
        *,
        agent: Agent,
        chat_session: ChatSession,
        user_message: str,
        mode: str = "draft",
        variables: dict | None = None,
        rag_enabled: bool | None = None,
        rag_options: dict | None = None,
        thinking_enabled: bool | None = None,
        search_enabled: bool | None = None,
        attachments: list[dict] | None = None,
        _agent_call_stack: list[int] | None = None,
    ) -> tuple[Run, str, list[dict], list[dict]]:
        """
        同步执行工作流引擎（Sync Workflow Pipeline）。
        
        ⚡ 边界与性能思考：
            每一个 Run 代表一次用户交互，涉及多达几十次数据库读写。本方法在开始和结束时精确圈定
            SQLAlchemy 事务边界（db.commit），确保即使中间某个节点崩溃，前面的运行步骤依旧能持久化，为系统可观测性留下链路 Trace。
        """
        runtime = self._runtime_agent(agent, mode, chat_session.user_id)
        self.runtime = runtime
        upload_ids = [str(item.get("id")) for item in attachments or [] if item.get("id")]
        uploads = get_workspace_uploads(self.db, workspace_id=agent.workspace_id, upload_ids=upload_ids)
        self._validate_model_capabilities(runtime.capability_config, uploads)
        thinking_status = self._thinking_status(runtime.capability_config, thinking_enabled)
        search_status = self._search_status(user_message, search_enabled)

        # 弹性参数合并优先级：用户请求传入配置 > Agent 草稿/快照默认设置
        rag_config = normalize_rag({**dict(runtime.settings.get("rag") or {}), **dict(rag_options or {})})
        effective_rag_enabled = rag_config["enabled_by_default"] if rag_enabled is None else bool(rag_enabled)

        # 初始化 Run 实体，生成全局唯一 Run 链路 ID
        run = Run(workspace_id=agent.workspace_id, agent_id=agent.id, session_id=chat_session.id, status="running")
        self.db.add(run)
        self.db.flush()
        self.db.commit()
        self.db.refresh(run)

        # 召回短期会话记忆与长期画像记忆
        memory = self._session_memory(chat_session.id)
        profile_memory = get_memory_profile(
            self.db,
            workspace_id=agent.workspace_id,
            user_id=chat_session.user_id,
            agent_id=agent.id,
        )
        
        # 统一工作流环境上下文对象（The Single Source of Truth）
        context: dict = {
            "input": user_message,
            "sources": [],
            "tool_outputs": [],
            "draft": "",
            "variables": self._merge_variables(runtime.settings.get("variables", []), variables or {}),
            "memory_summary": memory.summary if memory else "",
            "profile_memory": "",
            "profile_memory_used": {},
            "memory_enabled": normalize_memory(runtime.settings.get("memory")).get("enabled", False),
            "rag_enabled": effective_rag_enabled,
            "knowledge_base_ids": runtime.knowledge_base_ids,
            **({"rag_enabled_request": rag_enabled} if rag_enabled is not None else {}),
            "rag_top_k": rag_config["top_k"],
            "rag_config": rag_config,
            "thinking_enabled": thinking_status["enabled"],
            "thinking_status": thinking_status,
            "search_enabled": search_status["enabled"],
            "search_status": search_status,
            "web_sources": search_status.get("sources", []),
            "uploads": uploads,
            "user_id": chat_session.user_id,
            "agent_call_stack": _agent_call_stack or [],
        }
        self._understand_query(runtime, context)

        # 在查询理解完成后，利用 rewritten_query 做长期画像记忆的向量检索
        from core.config import get_settings
        settings = get_settings()
        query = context.get("rewritten_query") or context["input"]
        # 🎯 同一回合只召回一次：facts 复用于 prompt 装配与观测事件，避免重复 embedding + 检索
        recalled_facts = recall_facts(
            profile_memory,
            query=query,
            k=settings.memory_recall_top_k,
        )
        profile_memory_text = recall_profile_memory(
            profile_memory,
            query=query,
            k=settings.memory_recall_top_k,
            facts=recalled_facts,
        )
        profile_memory_event = memory_used_event(
            profile_memory,
            session_summary_used=bool(memory and memory.summary),
            recalled_facts=recalled_facts,
        )
        context["profile_memory"] = profile_memory_text
        context["profile_memory_used"] = profile_memory_event
        steps: list[dict] = []

        # 游标驱动调度:按节点 next / Condition 分支决定下一跳;无显式边时退化为列表顺序(线性兼容)。
        executed: set[str] = set()
        i = 0
        while i < len(runtime.workflow):
            node = runtime.workflow[i]
            # 🛡️ 防环与防失控:已执行节点不再进入(Condition 死循环)+ 全局步数硬墙
            if node.get("id") in executed or len(executed) >= MAX_GRAPH_STEPS:
                break
            executed.add(node.get("id"))
            output = self._execute_node(runtime, node, context)
            if not steps:
                self._inject_first_node_events(output, context)
            events = output.pop("events", [])
            context.update(output)
            is_paused = bool(output.get("paused"))
            step = RunStep(
                run_id=run.id,
                node_id=node["id"],
                node_type=node["type"],
                status="paused" if is_paused else "succeeded",
                input={"input": user_message},
                output=output,
            )
            self.db.add(step)
            self.db.flush()
            self.db.commit()
            self.db.refresh(step)
            steps.append(
                {
                    "id": step.id,
                    "node_id": step.node_id,
                    "node_type": step.node_type,
                    "status": step.status,
                    "output": output,
                    "events": events,
                }
            )
            if is_paused:
                run.status = "paused"
                self.db.commit()
                return run, "", self._public_sources(context), steps
            next_id = resolve_next(node, output, context)
            i = advance(i, next_id, runtime.workflow)

        final_answer = context.get("answer") or context.get("draft") or "当前智能体没有生成回答。"
        # 若开启了会话上下文记忆，同步将其打包并截断更新
        if context.get("memory_enabled"):
            compact_session_memory(
                self.db,
                chat_session.id,
                user_message,
                final_answer,
                int(runtime.settings.get("memory", {}).get("max_messages", 12)),
                runtime_config=runtime.runtime_config,
            )
        run.status = "succeeded"
        run.completed_at = datetime.now(timezone.utc)
        self.db.commit()
        return run, final_answer, self._public_sources(context), steps

    def run_events(
        self,
        *,
        agent: Agent,
        chat_session: ChatSession,
        user_message: str,
        mode: str = "draft",
        variables: dict | None = None,
        rag_enabled: bool | None = None,
        rag_options: dict | None = None,
        thinking_enabled: bool | None = None,
        search_enabled: bool | None = None,
        attachments: list[dict] | None = None,
        async_memory: bool = False,
        _agent_call_stack: list[int] | None = None,
    ):
        """
        流式生成器执行工作流（SSE Streaming Event Generator）。

        🎯 意图与工程大局观：
            - 利用 Python 的 `yield` 机制，在长达几分钟的智能体链条中，将中间进度以细粒度事件实时推送。
            - 包含三类流式事件：
              1. `step`: 指示某个工作流节点开始/结束，并附带状态数据。
              2. `token`: 大模型实时打字流，用于 C端 界面渲染。
              3. `complete`: 整个 Run 链路圆满收官，输出完整持久化的实体与最终回答。
        """
        runtime, run, context = self._start_run(
            agent=agent,
            chat_session=chat_session,
            user_message=user_message,
            mode=mode,
            variables=variables,
            rag_enabled=rag_enabled,
            rag_options=rag_options,
            thinking_enabled=thinking_enabled,
            search_enabled=search_enabled,
            attachments=attachments,
            _agent_call_stack=_agent_call_stack,
        )
        self.runtime = runtime
        steps: list[dict] = []
        executed: set[str] = set()
        i = 0
        while i < len(runtime.workflow):
            node = runtime.workflow[i]
            # 🛡️ 防环与防失控:已执行节点不再进入(Condition 死循环)+ 全局步数硬墙
            if node.get("id") in executed or len(executed) >= MAX_GRAPH_STEPS:
                break
            executed.add(node.get("id"))
            if node["type"] == "LLM":
                # LLM 节点流式输出专用生成器中继
                output = yield from self._stream_llm_node(runtime, node, context)
            else:
                output = self._execute_node(runtime, node, context)
            if not steps:
                self._inject_first_node_events(output, context)
            events = output.pop("events", [])
            context.update(output)
            is_paused = bool(output.get("paused"))
            step = self._persist_step(run, node, user_message, output, status="paused" if is_paused else "succeeded")
            step_payload = {
                "id": step.id,
                "node_id": step.node_id,
                "node_type": step.node_type,
                "status": step.status,
                "output": output,
                "events": events,
            }
            steps.append(step_payload)
            yield {"event": "step", "step": step_payload}
            if is_paused:
                run.status = "paused"
                self.db.commit()
                yield {"event": "paused", "data": {"node_id": node.get("id"), "prompt": output.get("approval_prompt")}}
                return
            next_id = resolve_next(node, output, context)
            i = advance(i, next_id, runtime.workflow)

        final_answer = context.get("answer") or context.get("draft") or "当前智能体没有生成回答。"
        compaction_event = None
        if context.get("memory_enabled") and not async_memory:
            compaction_event = compact_session_memory(
                self.db,
                chat_session.id,
                user_message,
                final_answer,
                int(runtime.settings.get("memory", {}).get("max_messages", 12)),
                runtime_config=runtime.runtime_config,
            )
        run.status = "succeeded"
        run.completed_at = datetime.now(timezone.utc)
        self.db.commit()
        yield {
            "event": "complete",
            "run": run,
            "answer": final_answer,
            "sources": self._public_sources(context),
            "steps": steps,
        }
        if compaction_event:
            compaction_event_copy = dict(compaction_event)
            compaction_event_copy.pop("older_turns_list", None)
            yield {
                "event": "memory_compaction",
                "data": compaction_event_copy,
            }

    def _start_run(
        self,
        *,
        agent: Agent,
        chat_session: ChatSession,
        user_message: str,
        mode: str,
        variables: dict | None,
        rag_enabled: bool | None,
        rag_options: dict | None,
        thinking_enabled: bool | None,
        search_enabled: bool | None,
        attachments: list[dict] | None,
        _agent_call_stack: list[int] | None = None,
    ) -> tuple[object, Run, dict]:
        """
        初始化运行上下文并落库草稿（流式运行时前置管道）。
        """
        runtime = self._runtime_agent(agent, mode, chat_session.user_id)
        upload_ids = [str(item.get("id")) for item in attachments or [] if item.get("id")]
        uploads = get_workspace_uploads(self.db, workspace_id=agent.workspace_id, upload_ids=upload_ids)
        self._validate_model_capabilities(runtime.capability_config, uploads)
        thinking_status = self._thinking_status(runtime.capability_config, thinking_enabled)
        search_status = self._search_status(user_message, search_enabled)

        rag_config = normalize_rag({**dict(runtime.settings.get("rag") or {}), **dict(rag_options or {})})
        effective_rag_enabled = rag_config["enabled_by_default"] if rag_enabled is None else bool(rag_enabled)

        run = Run(workspace_id=agent.workspace_id, agent_id=agent.id, session_id=chat_session.id, status="running")
        self.db.add(run)
        self.db.flush()
        self.db.commit()
        self.db.refresh(run)

        memory = self._session_memory(chat_session.id)
        profile_memory = get_memory_profile(
            self.db,
            workspace_id=agent.workspace_id,
            user_id=chat_session.user_id,
            agent_id=agent.id,
        )
        context: dict = {
            "input": user_message,
            "sources": [],
            "tool_outputs": [],
            "draft": "",
            "variables": self._merge_variables(runtime.settings.get("variables", []), variables or {}),
            "memory_summary": memory.summary if memory else "",
            "profile_memory": "",
            "profile_memory_used": {},
            "memory_enabled": normalize_memory(runtime.settings.get("memory")).get("enabled", False),
            "rag_enabled": effective_rag_enabled,
            **({"rag_enabled_request": rag_enabled} if rag_enabled is not None else {}),
            "rag_top_k": rag_config["top_k"],
            "rag_config": rag_config,
            "thinking_enabled": thinking_status["enabled"],
            "thinking_status": thinking_status,
            "search_enabled": search_status["enabled"],
            "search_status": search_status,
            "web_sources": search_status.get("sources", []),
            "uploads": uploads,
            "user_id": chat_session.user_id,
            "agent_call_stack": _agent_call_stack or [],
        }
        self._understand_query(runtime, context)

        # 在查询理解完成后，利用 rewritten_query 做长期画像记忆的向量检索
        from core.config import get_settings
        settings = get_settings()
        query = context.get("rewritten_query") or context["input"]
        # 🎯 同一回合只召回一次：facts 复用于 prompt 装配与观测事件，避免重复 embedding + 检索
        recalled_facts = recall_facts(
            profile_memory,
            query=query,
            k=settings.memory_recall_top_k,
        )
        profile_memory_text = recall_profile_memory(
            profile_memory,
            query=query,
            k=settings.memory_recall_top_k,
            facts=recalled_facts,
        )
        profile_memory_event = memory_used_event(
            profile_memory,
            session_summary_used=bool(memory and memory.summary),
            recalled_facts=recalled_facts,
        )
        context["profile_memory"] = profile_memory_text
        context["profile_memory_used"] = profile_memory_event

        return runtime, run, context

    def _persist_step(self, run: Run, node: dict, user_message: str, output: dict, *, status: str = "succeeded") -> RunStep:
        """持久化步骤实体元数据。"""
        step = RunStep(
            run_id=run.id,
            node_id=node["id"],
            node_type=node["type"],
            status=status,
            input={"input": user_message},
            output=output,
        )
        self.db.add(step)
        self.db.flush()
        self.db.commit()
        self.db.refresh(step)
        return step

    def _execute_node(self, agent, node: dict, context: dict) -> dict:
        """
        工作流图元节点单步核心分发器（图节点解释器模式）。
        """
        node_type = node["type"]
        
        # ==========================================
        # 1. Start 节点：基础资产接收与前置计数
        # ==========================================
        if node_type == "Start":
            return {
                "started": True,
                "variables": context.get("variables", {}),
                "rag_enabled": context.get("rag_enabled", True),
                "search_enabled": context.get("search_enabled", False),
                "attachment_count": len(context.get("uploads", [])),
            }
            
        # ==========================================
        # 2. Knowledge 节点：RAG 混合检索引擎调用
        # ==========================================
        if node_type == "Knowledge":
            effective_source = "request" if "rag_enabled_request" in context else "agent_default"
            # 若用户强制关闭了 RAG 功能，直接平滑退出并吐出空结果，避免任何多余的嵌入和检索开销
            if not context.get("rag_enabled", True):
                status = {
                    "enabled": False,
                    "effective_source": effective_source,
                    "knowledge_base_ids": [],
                    "query": context["input"],
                    "top_k": int(context.get("rag_top_k") or node.get("config", {}).get("top_k", 4)),
                    "matched_chunks": 0,
                    "sources_emitted": False,
                    "reason": "disabled",
                    "dense": {"matched": 0},
                    "bm25": {"matched": 0},
                    "rrf": {"matched": 0},
                    "rerank": {"enabled": False, "applied": False, "model": None, "error": None},
                    "cache": {"enabled": False, "hit": False, "backend": "none"},
                    "no_evidence": False,
                }
                return {"sources": [], "rag_enabled": False, "rag_status": status, "events": [{"event": "rag_status", "data": status}]}

            kb_ids = getattr(agent, "knowledge_base_ids", None)
            if kb_ids is None:
                kb_ids = [
                    row.knowledge_base_id
                    for row in self.db.query(AgentKnowledgeBase).filter(AgentKnowledgeBase.agent_id == agent.id).all()
                ]

            # 执行 RAG 向量混合检索与重排重估；自纠模式默认关闭，关闭时仍走原生单趟路径。
            retrieve_kwargs = {
                "workspace_id": agent.workspace_id,
                "knowledge_base_ids": kb_ids,
                "query": context.get("rewritten_query") or context["input"],
                "config": context.get("rag_config") or {},
                "runtime_config": getattr(agent, "runtime_config", None),
            }
            if get_settings().rag_self_correct:
                from core.runtime.self_correct_retrieval import self_correct_retrieve

                rag_result = self_correct_retrieve(self.db, **retrieve_kwargs)
            else:
                rag_result = retrieve(self.db, **retrieve_kwargs)
            sources = rag_result.sources
            status = {**rag_result.status, "effective_source": effective_source}
            return {"sources": sources, "rag_enabled": True, "rag_status": status, "events": [{"event": "rag_status", "data": status}]}
            
        # ==========================================
        # 3. Tool 节点：极硬核 ReAct 自适应工具决策迭代循环 (The Core Brain of Agent)
        # ==========================================
        if node_type == "Tool":
            bound_tools = self._runtime_tools(agent, node)
            tool_policy = (agent.settings.get("tool_policy") or {})
            allowed_names = set(tool_policy.get("allowed_tool_names") or [])
            if allowed_names:
                bound_tools = [t for t in bound_tools if t.name in allowed_names]
            # 🧠 设计修正：没有任何可用工具时直接空转返回。
            # 但只要 Agent 绑定了工具，就不再因为查询理解把意图判成 chitchat/clarify
            # 而提前剥夺工具——那会导致像「你能搜到这篇论文吗」这类问题被误判为闲聊、
            # 模型根本看不到 arxiv_search 等工具。是否真正调用工具交由模型在
            # tool_choice="auto" 下自行决策（纯闲聊时模型只回文本、不产生 tool_calls，
            # 成本与原先跳过该节点一致）。
            if not bound_tools:
                return {"tool_outputs": [], "tool_stats": {"total_calls": 0, "tools_used": []}}

            # 翻译为标准符合 OpenAI/Claude 格式的 Tool Schemas 暴露给模型
            tool_schemas = [tool_schema_for_llm(t) for t in bound_tools]
            messages = self._llm_messages(agent, context)
            total_calls = 0
            tools_used: list[str] = []
            events = []
            
            # 🧠 魔鬼数字与硬兜底红线：
            # - 最大工具调用预算 `max_tool_calls` 硬设定为 20。
            # - 最多轮询上限 `_round` 为 8 轮。
            # - 单次交互多轮工具执行的物理硬墙时间限制为 120 秒 (`max_tool_wall_time`)。
            # 以上三重拦截指标，能够死死锁住由于 LLM 幻觉产生错误、反复触发同个重试工具、或者死循环解析报错产生的死循环调用，防止其拖死整个 Web Server 的工作线程池并榨干 Token 余额。
            max_tool_calls = 20
            max_tool_wall_time = 120  # seconds
            tool_loop_start = time.monotonic()

            for _round in range(8):
                if total_calls >= max_tool_calls:
                    break
                if time.monotonic() - tool_loop_start > max_tool_wall_time:
                    break
                response = self.provider.chat(
                    messages,
                    model=agent.model,
                    temperature=agent.temperature,
                    runtime_config=agent.runtime_config,
                    tools=tool_schemas,
                )
                
                # 如果大模型返回了纯文本但没有工具调用指示，说明抉择完毕，直接吐出候选草稿返回
                if response.content and not response.tool_calls:
                    return {
                        "draft": response.content,
                        "tool_outputs": [],
                        "tool_stats": {"total_calls": total_calls, "tools_used": tools_used},
                    }
                
                # 开始执行大模型呼叫的工具集
                if response.tool_calls:
                    assistant_msg = {"role": "assistant", "content": response.content, "tool_calls": response.tool_calls}
                    messages.append(assistant_msg)
                    
                    # 裁剪本轮工具呼叫，确保不超过总调配预算上限
                    calls_this_round = response.tool_calls[:max_tool_calls - total_calls]
                    for tc in calls_this_round:
                        if time.monotonic() - tool_loop_start > max_tool_wall_time:
                            break
                        func = tc["function"]
                        tool_name = func["name"]
                        try:
                            tool_args = json.loads(func.get("arguments") or "{}")
                        except json.JSONDecodeError:
                            # 🛡️ 防御性容错：模型可能直接吐出字符串而非格式化好的 JSON arguments
                            tool_args = {"input": func.get("arguments") or ""}
                        matching = next((t for t in bound_tools if t.name == tool_name), None)
                        started = time.monotonic()
                        
                        if matching:
                            try:
                                # 安全沙箱化调度工具执行
                                result = execute_tool(matching, {"input": tool_args, "_db": self.db, "_user_id": context.get("user_id"), "agent_call_stack": context.get("agent_call_stack")})
                                result["latency_ms"] = result.get("latency_ms", int((time.monotonic() - started) * 1000))
                                events.append({"event": "tool_call", "data": tool_call_event(matching, result, input_preview=json.dumps(tool_args, ensure_ascii=False))})
                                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result.get("content") or result.get("result_preview") or ""})
                            except ValueError as exc:
                                # 🛡️ 稳妥抓取工具内部报错：当做正常的 Tool 输出反馈给 LLM，指导大模型在下一轮尝试自我修复
                                events.append({"event": "tool_call", "data": tool_call_event(matching, {"tool": tool_name, "content": "", "result_preview": "", "latency_ms": int((time.monotonic() - started) * 1000), "error": str(exc)}, status="error", input_preview=json.dumps(tool_args, ensure_ascii=False), error_code="tool_error")})
                                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": f"Error: {exc}"})
                        else:
                            # 🛡️ 错误兜底：呼叫了未绑定的不存在工具
                            events.append({"event": "tool_call", "data": tool_call_event(type("_", (), {"id": None, "name": tool_name, "type": "unknown"})(), {"tool": tool_name, "content": "", "result_preview": "", "latency_ms": 0}, status="error", input_preview="{}", error_code="tool_not_found")})
                            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": f"Tool '{tool_name}' not found"})
                        total_calls += 1
                        tools_used.append(tool_name)

            # 多轮工具调用上限拦截后，由大模型根据现有对话追踪上下文产出最终回答
            final = self.provider.chat(messages, model=agent.model, temperature=agent.temperature, runtime_config=agent.runtime_config)
            return {
                "draft": final.content or "",
                "tool_outputs": [],
                "tool_stats": {"total_calls": total_calls, "tools_used": tools_used, "max_rounds_reached": True},
                "events": events,
            }
            
        # ==========================================
        # 4. LLM 节点：草稿提取或单独文本模型生成
        # ==========================================
        if node_type == "LLM":
            if context.get("draft"):
                return self._llm_output(agent, context, context["draft"])
            messages = self._llm_messages(agent, context)
            draft = self.provider.chat(
                messages,
                model=agent.model,
                temperature=agent.temperature,
                runtime_config=agent.runtime_config,
                thinking=bool(context.get("thinking_enabled")),
            ).content or ""
            return self._llm_output(agent, context, draft)
            
        # ==========================================
        # 5. Answer 节点：最终输出与强类型校验
        # ==========================================
        if node_type == "Answer":
            answer = (context.get("draft") or "").strip()
            if not answer:
                # 🛡️ 拦截模型彻底返回空文本的异常崩溃状况
                raise ValueError("Model returned an empty answer")
            return {"answer": answer, "citation_count": len([*context.get("sources", []), *context.get("web_sources", [])])}
        # Human-in-the-loop:人工审批节点,执行到此暂停,等人工确认(切片2 resume)后继续
        if node_type == "HumanApproval":
            config = node.get("config") or {}
            return {"paused": True, "approval_prompt": config.get("prompt", "请确认是否继续执行")}
        return {}

    def _stream_llm_node(self, agent, node: dict, context: dict):
        """
        LLM 节点流式 Token 中继。

        🎯 双事件通道：
            - provider 帧协议 `{"type": "content"|"reasoning", "text": ...}` 在此分流：
              content 帧照旧发 `token` 事件并拼入 draft；reasoning 帧（原生推理模型的思考轨迹）
              转成独立的 `thinking_token` 事件实时推送前端。
            - 思考文本绝不拼入 draft——draft 会作为最终回答存库并进入会话记忆，混入思考流会污染正文。
            - 仅当本轮裁决为原生推理（thinking_status.type == "native"）时才向 provider 下发 thinking，
              提示词增强（prompt）模式不产生思考流，无需开启。

        ⚡ 边界与性能思考（极致的首帧响应 TTFT 模拟设计）：
            - 若大模型已经在 Tool 调用环节产出了完整的草稿 `draft`，此时没有实时向远端 LLM 发起连接的动作。
              为了保持流式打字输出的“一致人机感”，设计了一个“极小时间常数的流式模拟生成器”：
              每帧均匀吐出 24 个字符（`draft[index : index + 24]`），在毫秒级微延时下平滑推送至前端。
            - 否则，直接中继外部 OpenAICompatibleProvider 产出的 `chat_stream` 生成器，实现实时长连接输出。
        """
        draft = context.get("draft", "")
        if draft:
            for index in range(0, len(draft), 24):
                yield {"event": "token", "content": draft[index : index + 24]}
            return self._llm_output(agent, context, draft)
        messages = self._llm_messages(agent, context)
        native_thinking = bool(context.get("thinking_enabled")) and (context.get("thinking_status") or {}).get("type") == "native"
        chunks = []
        for frame in self.provider.chat_stream(
            messages,
            model=agent.model,
            temperature=agent.temperature,
            runtime_config=agent.runtime_config,
            thinking=native_thinking,
        ):
            # 🛡️ 防御性兼容：容忍 provider 退化产出裸字符串（一律视为正文 Token）
            if isinstance(frame, str):
                frame = {"type": "content", "text": frame}
            text = frame.get("text") or ""
            if not text:
                continue
            if frame.get("type") == "reasoning":
                yield {"event": "thinking_token", "content": text}
                continue
            chunks.append(text)
            yield {"event": "token", "content": text}
        draft = "".join(chunks)
        return self._llm_output(agent, context, draft)

    def _llm_messages(self, agent, context: dict) -> list[dict]:
        """
        极其精密的全局系统 System Prompt / User Message 的“编织与熔接引擎”。

        🎯 意图与工程大局观：
            这是整个 Agent 控制流的拼装大总管。它优雅地抓取了当前对话的多重维度数据：
            系统设定、原生或提示词伪推理说明、RAG 知识块、工具交互历史、用户上下文变量、历史聊天摘要、上传多模态数据、长期画像记忆。
        
        🛡️ 防御性编程与大模型兜底（绝对核心的安全阀门）：
            - 对短期会话历史摘要 `memory_summary` 执行健壮的反序列化，兼容早期纯文本和现代 JSON 列表存储。
            - **防 Token 溢出与系统注入（防爆机制）**：
              当系统 Prompt、知识片段、记忆片段无节制累积时，会轻易冲垮大模型的 `max_context` 上下文限制并诱发网络阻塞。
              为此，我们实施了全局安全防爆屏障：对拼装完成的整个 `system_content` 实施强力长度限制：`max_system_chars = 100_000`（约等于 5 万个 Token）。
              一旦溢出，强行在边缘做物理截断并补齐 `[上下文已截断以避免超出模型上下文窗口限制]`。
        """
        source_text = self._knowledge_source_text(context.get("sources", []))
        web_source_text = self._web_source_text(context.get("web_sources", []))
        tool_text = "\n".join(f"- {item['tool']}: {item['content']}" for item in context.get("tool_outputs", []))
        variable_text = "\n".join(f"- {key}: {value}" for key, value in context.get("variables", {}).items())
        attachment_text = self._attachment_text(context.get("uploads", []))
        thinking_blocks = []
        thinking_msgs = self._thinking_messages(context)
        if thinking_msgs:
            thinking_blocks = [msg["content"] for msg in thinking_msgs]
            
        raw_summary = context.get('memory_summary') or ''
        formatted_summary = "无"
        if raw_summary.strip():
            # B1 兼容渲染：新 dict {summary,turns} / 旧 list / 旧文本统一走 parse_memory
            summary_text, turns = parse_memory(raw_summary)
            if summary_text or turns:
                parts = []
                if summary_text:
                    parts.append(f"摘要：{summary_text}")
                if turns:
                    parts.append("最近对话：")
                    parts.append("\n".join(
                        f"用户：{t.get('user', '')}\n助手：{t.get('assistant', '')}" for t in turns
                    ))
                formatted_summary = "\n".join(parts)
            else:
                # parse_memory 无法解析时保留原始文本（纯文本等非结构化记忆）
                formatted_summary = raw_summary
                
        rewritten = context.get("rewritten_query")
        rewrite_hint = (
            f"解析后的检索意图（供参考，回答仍针对用户原话）：{rewritten}"
            if rewritten and rewritten != context.get("input") else ""
        )
        system_parts = [
            agent.system_prompt or "你是一个自定义智能体。",
            rewrite_hint,
            *thinking_blocks,
            f"Web search results for this turn:\n{web_source_text or 'None'}",
            f"可用知识片段：\n{source_text or '无'}",
            f"工具输出：\n{tool_text or '无'}",
            f"用户变量：\n{variable_text or '无'}",
            f"会话记忆摘要：\n{formatted_summary}",
            f"本轮附件上下文：\n{attachment_text or '无'}",
            f"Long-term Agent memory:\n{context.get('profile_memory') or 'None'}",
        ]
        system_content = "\n\n".join(part for part in system_parts if part.strip())
        
        # 🛡️ 边界防御：硬性截断过长上下文
        max_system_chars = 100_000  # ~50k tokens, safe for most model context windows
        if len(system_content) > max_system_chars:
            system_content = system_content[:max_system_chars] + "\n\n[上下文已截断以避免超出模型上下文窗口限制]"
            
        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": self._user_content(context["input"], context.get("uploads", []))},
        ]

    def _llm_output(self, agent, context: dict, draft: str) -> dict:
        """结构化 LLM 节点的输出承载元数据。"""
        return {
            "draft": draft,
            "used_memory": bool(context.get("memory_summary")),
            "used_profile_memory": bool(context.get("profile_memory")),
            "attachment_count": len(context.get("uploads", [])),
            "model": agent.model,
            "mock": self.provider.last_chat_mock,
            "thinking_enabled": bool(context.get("thinking_enabled")),
            "thinking_type": (context.get("thinking_status") or {}).get("type", "none"),
            "search_enabled": bool(context.get("search_enabled")),
            "search_result_count": len(context.get("web_sources", [])),
        }

    def _runtime_agent(self, agent: Agent, mode: str, user_id: int):
        """
        解析和打包当前租户下的 Agent 资产配置快照（智能体实例化快照）。

        🎯 意图与工程大局观：
            - `mode == "published"`：读取已审批、归档的 `AgentVersion` 里的 snapshot（快照）。
              保障处于生产环境中的智能体行为的一致与稳定，即使管理员目前正在草稿页对配置做出颠覆性更改，
              历史会话依旧能按当时的“契约 snapshot”完美流转。
            - `mode == "draft"`：实时查询 Agent 数据库的最新字段，快速响应开发调试期的即时保存预览。
        """
        if mode not in {"draft", "published"}:
            raise ValueError("mode must be draft or published")
        if mode == "published":
            if not agent.published_version_id:
                raise ValueError("当前智能体还没有发布版本")
            version = self.db.get(AgentVersion, agent.published_version_id)
            if not version:
                raise ValueError("发布版本不存在")
            snapshot = version.snapshot or {}
            source = {
                "system_prompt": snapshot.get("system_prompt", agent.system_prompt),
                "model_id": snapshot.get("model_id", agent.model_id),
                "model": snapshot.get("model", agent.model),
                "temperature": snapshot.get("temperature", agent.temperature),
                "knowledge_base_ids": snapshot.get("knowledge_base_ids") or [],
                "tool_ids": [tool.get("id") for tool in snapshot.get("tools", []) if tool.get("id")],
                "workflow": snapshot.get("workflow") or default_workflow(),
                "variables": snapshot.get("variables") or [],
                "memory": normalize_memory(snapshot.get("memory")),
                "rag": normalize_rag(snapshot.get("rag")),
                "tool_policy": normalize_tool_policy(snapshot.get("tool_policy")),
                "user_model_config_id": snapshot.get("user_model_config_id", agent.user_model_config_id),
                "query_understanding": normalize_query_understanding(snapshot.get("query_understanding")),
            }
        else:
            detail = get_agent_detail(self.db, agent)
            source = {
                "system_prompt": agent.system_prompt,
                "model_id": agent.model_id,
                "model": agent.model,
                "temperature": agent.temperature,
                "knowledge_base_ids": detail.get("knowledge_base_ids") or [],
                "tool_ids": [tool.get("id") for tool in detail.get("tools", []) if tool.get("id")],
                "workflow": detail.get("workflow") or default_workflow(),
                "variables": detail.get("variables") or [],
                "memory": normalize_memory(detail.get("memory")),
                "rag": normalize_rag(detail.get("rag")),
                "tool_policy": normalize_tool_policy(detail.get("tool_policy")),
                "user_model_config_id": agent.user_model_config_id,
                "query_understanding": normalize_query_understanding(detail.get("query_understanding")),
            }

        user_model_config = self._user_model_config(user_id, source["user_model_config_id"])
        runtime_config = user_model_runtime_config(user_model_config) if user_model_config else None
        return SimpleNamespace(
            id=agent.id,
            workspace_id=agent.workspace_id,
            system_prompt=source["system_prompt"],
            model_id=source["model_id"],
            user_model_config_id=source["user_model_config_id"],
            model=(runtime_config or {}).get("chat_model") or source["model"],
            temperature=source["temperature"],
            knowledge_base_ids=source["knowledge_base_ids"],
            tool_ids=source["tool_ids"],
            workflow=source["workflow"],
            model_config=self._model_config(source["model_id"], source["model"]),
            user_model_config=user_model_config,
            runtime_config=runtime_config,
            capability_config=user_model_config or self._model_config(source["model_id"], source["model"]),
            settings={
                "variables": source["variables"],
                "memory": source["memory"],
                "rag": source["rag"],
                "tool_policy": source["tool_policy"],
                "query_understanding": source["query_understanding"],
            },
        )

    def _model_config(self, model_id: int | None, model_name: str | None) -> ModelConfig | None:
        return resolve_agent_model(self.db, model_id=model_id, model_name=model_name)

    def _user_model_config(self, user_id: int, config_id: int | None) -> UserModelConfig | None:
        if config_id is None:
            return None
        return resolve_user_model_config(self.db, user_id=user_id, config_id=config_id, enabled_only=True)

    def _validate_model_capabilities(self, model: ModelConfig | UserModelConfig | None, uploads: list[Upload]) -> None:
        """
        🛡️ 防御性编程：前置验证图片/文档附件与大模型的处理能力是否对齐，不对齐时提前切断。
        """
        if not model:
            return
        has_document = any(upload.kind == "document" for upload in uploads)
        if has_document and not getattr(model, "supports_document", True):
            raise ValueError("Selected model does not support document input")

    def _thinking_status(self, model: ModelConfig | UserModelConfig | None, requested: bool | None) -> dict:
        """
        深度思考状态裁决与平滑降级链。

        🎯 意图与工程大局观：
            - 用户端可以选择强制启动 `thinking_enabled`。
            - 某些模型（如 o1/o3/deepseek-r1 等）天然支持 `native` 原生推理输出（此时会将思考轨迹流式推前端）。
            - 对于传统不支持推理的模型，如果强开思考，系统会通过自动降级到 `prompt` 推理模式：
              在 System Prompt 头部注入精心设计的深度分析元指令（_thinking_messages），平滑地在旧模型上激发推理规划动作。
        """
        reasoning_type = str(getattr(model, "reasoning_type", "none") or "none")
        if reasoning_type not in {"native", "prompt", "none"}:
            reasoning_type = "none"
        supports_reasoning = bool(getattr(model, "supports_reasoning", False)) and reasoning_type != "none"
        label = str(getattr(model, "reasoning_label", "") or self._reasoning_label(reasoning_type))

        if not requested:
            return {
                "enabled": False,
                "requested": False,
                "type": reasoning_type,
                "label": label,
                "reason": "not_requested",
            }
        if not supports_reasoning:
            return {
                "enabled": False,
                "requested": True,
                "type": "none",
                "label": self._reasoning_label("none"),
                "reason": "model_not_supported",
            }
        return {
            "enabled": True,
            "requested": True,
            "type": reasoning_type,
            "label": label,
            "reason": "enabled",
        }

    def _thinking_messages(self, context: dict) -> list[dict]:
        """
        对伪推理模式（提示词模拟增强模式）的大模型注入思考诱导词，限制其隐藏推理路径以获得干净输出。
        """
        status = context.get("thinking_status") or {}
        if not status.get("enabled"):
            return []
        if status.get("type") == "prompt":
            return [
                {
                    "role": "system",
                    "content": (
                        "本轮已开启深度思考模式，但当前模型使用提示词增强，不是原生推理。"
                        "请先进行更周全的分析，检查关键假设、约束、风险和反例，再给出清晰答案。"
                        "不要输出隐藏推理链，只输出必要的结论、依据和可执行步骤。"
                    ),
                }
            ]
        return [
            {
                "role": "system",
                "content": "本轮已开启原生深度思考能力。请给出经过审慎推理后的答案，不要输出隐藏推理链。",
            }
        ]

    @staticmethod
    def _reasoning_label(reasoning_type: str) -> str:
        return {"native": "深度思考", "prompt": "提示词增强", "none": "不支持"}.get(reasoning_type, "不支持")

    def _search_status(self, query: str, requested: bool | None) -> dict:
        """
        自适应实时 Web 搜索决策阀。
        允许 Agent 自动降级，若搜索接口遭遇速率限制或出错，立刻拦截并以无搜索结果兜底返回，保证会话绝不中断。
        """
        if not requested:
            return {
                "enabled": False,
                "requested": False,
                "query": query,
                "provider": "duckduckgo_html",
                "matched_results": 0,
                "sources_emitted": False,
                "items": [],
                "sources": [],
                "reason": "not_requested",
            }
        try:
            result = web_search_service.search_web(query)
            sources = web_search_service.search_items_as_sources(result.get("items", []))
            return {
                "enabled": bool(sources),
                "requested": True,
                "query": result.get("query", query),
                "provider": result.get("provider", "duckduckgo_html"),
                "matched_results": len(sources),
                "sources_emitted": bool(sources),
                "items": result.get("items", []),
                "sources": sources,
                "latency_ms": result.get("latency_ms", 0),
                "reason": "enabled" if sources else "no_results",
            }
        except WebSearchError as exc:
            return {
                "enabled": False,
                "requested": True,
                "query": query,
                "provider": "duckduckgo_html",
                "matched_results": 0,
                "sources_emitted": False,
                "items": [],
                "sources": [],
                "reason": str(exc),
            }

    def _inject_first_node_events(self, output: dict, context: dict) -> None:
        """首节点统一注入记忆/思考/搜索/查询理解状态事件（run 与 run_events 共用）。"""
        events = output.setdefault("events", [])
        events.append({"event": "memory_used", "data": context.get("profile_memory_used", {})})
        events.append({"event": "thinking_status", "data": context.get("thinking_status", {})})
        events.append({"event": "search_status", "data": self._search_status_event(context.get("search_status", {}))})
        events.append({"event": "query_understanding", "data": context.get("query_understanding_event", {})})

    def _search_status_event(self, status: dict) -> dict:
        return {key: value for key, value in status.items() if key != "sources"}

    def _public_sources(self, context: dict) -> list[dict]:
        """
        汇集对外（SSE 回传 + 落库 message.sources）的引用列表。

        🎯 剥离内部字段 `content`（父块扩展后的全文，仅用于本轮拼 prompt）：
            前端引用展示与历史记录只用 `snippet`，把父块全文存进每条消息纯属冗余膨胀。
            浅拷贝输出，避免污染 retrieve 的（可能被缓存复用的）原始 source 字典。
        """
        public = []
        for item in [*context.get("sources", []), *context.get("web_sources", [])]:
            if "content" in item:
                item = {k: v for k, v in item.items() if k != "content"}
            public.append(item)
        return public

    def _knowledge_source_text(self, sources: list[dict]) -> str:
        """
        拼装喂给 LLM 的知识片段正文。

        🎯 优先用父块扩展后的 `content`（更完整上下文），缺失时回退到 `snippet`（child 截断）。
        🛡️ 父块去重：多个 child 命中同一父块时，父块全文只注入一次，避免重复内容挤占上下文窗口。
            sources 列表本身不动（前端引用仍逐条展示）。
        """
        seen_parents: set = set()
        lines = []
        for item in sources:
            body = item.get("content") or item.get("snippet") or ""
            parent_key = item.get("parent_id")
            if parent_key and item.get("content"):
                if parent_key in seen_parents:
                    continue
                seen_parents.add(parent_key)
            lines.append(f"- {item['title']}: {body}")
        return "\n".join(lines)

    def _web_source_text(self, sources: list[dict]) -> str:
        lines = []
        for index, item in enumerate(sources, start=1):
            title = item.get("title") or f"Result {index}"
            url = item.get("url") or ""
            snippet = item.get("snippet") or ""
            lines.append(f"{index}. {title}\nURL: {url}\nSnippet: {snippet}")
        return "\n\n".join(lines)

    def _runtime_tools(self, agent, node: dict) -> list:
        tool_ids = getattr(agent, "tool_ids", []) or []
        tools = []
        if tool_ids:
            tools = (
                self.db.query(Tool)
                .filter(Tool.id.in_(tool_ids), Tool.enabled.is_(True))
                .order_by(Tool.id.asc())
                .all()
            )
        # Agent-as-Tool:把绑定的已发布子 agent 包装成 type="agent" 工具,LLM 在 ReAct 循环里按需调用
        bindings = (
            self.db.query(AgentAgentBinding)
            .filter(AgentAgentBinding.agent_id == agent.id, AgentAgentBinding.enabled.is_(True))
            .all()
        )
        for b in bindings:
            target = self.db.get(Agent, b.target_agent_id)
            if not target or not target.published_version_id:
                continue
            tools.append(
                SimpleNamespace(
                    id=f"agent_{target.id}",
                    name=f"call_agent_{target.id}",
                    label=target.name,
                    description=f"委派任务给智能体「{target.name}」",
                    type="agent",
                    schema={"target_agent_id": target.id},
                    enabled=True,
                )
            )
        # MCP 工具:把绑定的 MCP server 的工具包装成 type="mcp" 工具,LLM 在 ReAct 循环里按需调用
        mcp_bindings = (
            self.db.query(AgentMcpBinding)
            .filter(AgentMcpBinding.agent_id == agent.id, AgentMcpBinding.enabled.is_(True))
            .all()
        )
        for mb in mcp_bindings:
            server = self.db.get(McpServer, mb.mcp_server_id)
            if not server or not server.enabled:
                continue
            try:
                mcp_tools = get_mcp_client(server).list_tools()
            except Exception:
                # server 不可达:跳过其工具,不阻断主流程
                continue
            for mt in mcp_tools:
                mt_name = mt.get("name")
                if not mt_name:
                    continue
                tools.append(
                    SimpleNamespace(
                        id=f"mcp_{server.id}_{mt_name}",
                        name=f"mcp_{server.id}_{mt_name}",
                        label=mt.get("description") or mt_name,
                        description=mt.get("description") or mt_name,
                        type="mcp",
                        schema={"mcp_server_id": server.id, "tool_name": mt_name, "input_schema": mt.get("inputSchema")},
                        enabled=True,
                    )
                )
        return tools

    def _attachment_text(self, uploads: list[Upload]) -> str:
        """格式化传入文档提取出的文本附件，并强硬截取前 6000 字符限制单次对话 Token 的无序爆发。"""
        lines = []
        for upload in uploads:
            if upload.kind == "document":
                lines.append(f"[{upload.filename}]\n{upload.text[:6000]}")
            elif upload.kind == "image":
                lines.append(f"[Image: {upload.filename}]")
        return "\n\n".join(lines)

    def _user_content(self, text: str, uploads: list[Upload]):
        """拼装符合 Vision 多模态规范的 User Payload 结构（图片以 Base64 Data URL 直接注入）。"""
        image_uploads = [upload for upload in uploads if upload.kind == "image"]
        if not image_uploads:
            return text
        content = [{"type": "text", "text": text}]
        for upload in image_uploads:
            # 兼容对象存储：data_url 内联则直用，否则从对象库取回转 base64（外部 LLM 够不到内网对象库）
            content.append({"type": "image_url", "image_url": {"url": resolve_image_data_url(upload)}})
        return content

    def _merge_variables(self, definitions: list[dict], provided: dict) -> dict:
        """高精度融合预设环境变量与用户前端自定义变量，支持默认值优雅降级。"""
        merged = {}
        for definition in definitions:
            key = definition.get("key")
            if key:
                merged[key] = provided.get(key, definition.get("default_value"))
        for key, value in provided.items():
            if key not in merged:
                merged[key] = value
        return merged

    def _understand_query(self, runtime, context: dict) -> None:
        """查询理解前置阶段：仅当用户开启「知识库」检索（rag_enabled）时，做一次轻量 LLM 调用，
        把多轮指代/省略补全、改写成不依赖上下文的自包含 query 供检索使用。

        是否走 RAG 完全由用户的「知识库」按钮决定，不再由模型意图路由裁决；也不再做低置信度
        澄清反问（交由用户自己表达）。未开启知识库时直接 passthrough，省去这次前置 LLM 调用，
        让普通对话直达 LLM 节点（避免「1+1」也要等一次前置推理）。
        """
        if not context.get("rag_enabled", True):
            result = qu_service._passthrough(context["input"], reason="rag_disabled")
        elif not context.get("knowledge_base_ids"):
            result = qu_service._passthrough(context["input"], reason="no_knowledge_base")
        else:
            config = runtime.settings.get("query_understanding") or {}
            history = self._history_turns(context.get("memory_summary") or "")
            result = qu_service.analyze(
                self.provider,
                user_message=context["input"],
                history=history,
                config=config,
                runtime_config=getattr(runtime, "runtime_config", None),
            )
        context["rewritten_query"] = result.rewritten_query
        context["intent"] = result.intent
        context["confidence"] = result.confidence
        context["route"] = result.route
        context["query_understanding_event"] = result.event_payload()

    @staticmethod
    def _history_turns(memory_summary: str) -> list[dict]:
        """把 session memory 解析成 [{user, assistant}] 轮次列表（兼容三种格式），失败则空。"""
        if not memory_summary.strip():
            return []
        _, turns = parse_memory(memory_summary)
        return turns

    def _session_memory(self, session_id: int) -> SessionMemory | None:
        return self.db.query(SessionMemory).filter(SessionMemory.session_id == session_id).first()


def compact_session_memory(
    db: Session,
    session_id: int,
    user_message: str,
    answer: str,
    max_messages: int,
    runtime_config: dict | None = None,
) -> dict:
    """
    模块级的会话记忆压缩函数，移出主请求链路，支持乐观锁重试与返回 compaction_event。
    """
    import logging
    import time

    logger = logging.getLogger(__name__)
    start_time = time.perf_counter()
    settings = get_settings()

    triggered = False
    older_turns_count = 0
    kept_turns_count = 0
    tokens_before = 0
    tokens_after = 0
    degraded = False
    summarizer_model = settings.memory_summary_model or settings.openai_model

    for attempt in range(2):
        memory = db.query(SessionMemory).filter(SessionMemory.session_id == session_id).first()
        if not memory:
            try:
                memory = SessionMemory(session_id=session_id, summary="", message_count=0, version=0)
                db.add(memory)
                db.commit()
                db.refresh(memory)
            except Exception:
                db.rollback()
                memory = db.query(SessionMemory).filter(SessionMemory.session_id == session_id).first()
                if not memory:
                    logger.warning("Failed to get or create SessionMemory for session %d", session_id)
                    return {"triggered": False}

        old_version = memory.version
        old_summary = memory.summary

        _, parsed_turns = parse_memory(old_summary)
        last_turn = parsed_turns[-1] if parsed_turns else None
        is_duplicate = (
            last_turn is not None
            and last_turn.get("user") == user_message.strip()
            and last_turn.get("assistant") == answer.strip()
        )

        if is_duplicate:
            payload = {"summary": memory.summary, "turns": parsed_turns}
            triggered = False
            break

        max_turns = max(1, max_messages // 2)
        summary_config = {
            "enabled": settings.memory_summary_enabled,
            "summary_max_chars": settings.memory_summary_max_chars,
            "model": settings.memory_summary_model,
        }

        provider = OpenAICompatibleProvider()
        summarizer_failed = False
        captured_older = []

        def summarizer_wrapper(older, existing):
            nonlocal triggered, older_turns_count, summarizer_failed, captured_older
            triggered = True
            older_turns_count = len(older)
            captured_older = older
            try:
                res = summarize_turns(
                    provider,
                    older_turns=older,
                    existing_summary=existing,
                    config=summary_config,
                    runtime_config=runtime_config,
                )
                return res
            except Exception as e:
                summarizer_failed = True
                raise e

        payload = build_memory_payload(
            old_summary,
            new_turn={"user": user_message.strip(), "assistant": answer.strip()},
            token_budget=settings.memory_token_budget,
            recent_token_budget=settings.memory_recent_token_budget,
            max_turns=max_turns,
            keep_recent=settings.memory_keep_recent_turns,
            summarizer=summarizer_wrapper,
        )

        if summarizer_failed:
            degraded = True

        kept_turns_count = len(payload.get("turns", []))

        _, raw_turns = parse_memory(old_summary)
        all_turns = raw_turns + [{"user": user_message.strip(), "assistant": answer.strip()}]
        lc_msgs_before = []
        for t in all_turns:
            u = t.get("user") or ""
            a = t.get("assistant") or ""
            if u:
                lc_msgs_before.append(HumanMessage(content=u))
            if a:
                lc_msgs_before.append(AIMessage(content=a))
        tokens_before = count_tokens_approximately(lc_msgs_before)

        lc_msgs_after = []
        for t in payload.get("turns", []):
            u = t.get("user") or ""
            a = t.get("assistant") or ""
            if u:
                lc_msgs_after.append(HumanMessage(content=u))
            if a:
                lc_msgs_after.append(AIMessage(content=a))
        if payload.get("summary"):
            lc_msgs_after.append(SystemMessage(content=payload["summary"]))
        tokens_after = count_tokens_approximately(lc_msgs_after)

        rows_updated = db.query(SessionMemory).filter(
            SessionMemory.session_id == session_id,
            SessionMemory.version == old_version
        ).update({
            "summary": json.dumps(payload, ensure_ascii=False),
            "message_count": SessionMemory.message_count + 2,
            "version": SessionMemory.version + 1,
            "updated_at": datetime.now()
        }, synchronize_session=False)
        db.commit()

        if rows_updated > 0:
            break
        else:
            db.rollback()
            if attempt == 1:
                logger.warning(
                    f"SessionMemory concurrency update failed for session {session_id} after retry. Abandoning compression."
                )

    latency_ms = int((time.perf_counter() - start_time) * 1000)

    compaction_event = {
        "triggered": triggered,
        "older_turns": older_turns_count,
        "older_turns_list": captured_older,
        "kept_turns": kept_turns_count,
        "tokens_before": tokens_before,
        "tokens_after": tokens_after,
        "summarizer_model": summarizer_model,
        "degraded": degraded,
        "latency_ms": latency_ms
    }
    return compaction_event


def compact_session_memory_task(
    session_id: int,
    user_message: str,
    answer: str,
    max_messages: int,
    runtime_config: dict | None = None,
):
    """
    FastAPI 后台任务专用的包装器，新开 db Session 并在结束时关闭。
    """
    import logging

    from core.db.session import SessionLocal
    from core.runtime.memory_pipeline import run_memory_pipeline
    logger = logging.getLogger(__name__)
    db = SessionLocal()
    try:
        run_memory_pipeline(
            db=db,
            session_id=session_id,
            user_message=user_message,
            answer=answer,
            max_messages=max_messages,
            runtime_config=runtime_config,
        )
    except Exception as e:
        logger.warning(f"SessionMemory background compaction failed: {e}")
    finally:
        db.close()
