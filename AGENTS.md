# Lingshu Agent 项目开发规范

## 项目概述
Lingshu Agent 是 FastAPI(Python 3.11,后端)+ Vite/React 18(前端)的全栈 AI 智能体平台:账号、智能体管理、知识库 RAG、工具集成、发布审核、多轮会话。详见 `README.md`。

## 回复语言
所有回复一律用中文。

## 测试与 lint 命令(改完代码必跑)
- **后端测试**(mock LLM,无需真实 API key / DB):
  `LINGSHU_MOCK_LLM=true LINGSHU_VECTOR_BACKEND=memory uv run python -m pytest tests/ --timeout=60`
- **后端 lint**(CI 等价规则,仅 E+F):
  `uv run --with ruff ruff check api/ core/ tests/ eval/ --select E4,E7,E9,F --ignore=E501`
- **前端构建**:`npm run build`(在 `frontend/`)
- **发布检查**:`uv run python scripts/release_check.py --with-frontend`

## 端口约定(不可变)
- 后端 API:`http://127.0.0.1:8000`
- 前端 dev:`http://127.0.0.1:5174`(`--strictPort`)

## 文档与产物
- 生成的调研/设计文档放 `~/docs/`(按课题 `NN-课题名`),不进项目仓库。
- 测试素材(图片/PDF/DOCX 等)放 `~/docs/`,绝不提交进 git(二进制进历史不可逆)。
- 项目自身文档(specs/README/AGENTS.md)留在仓库原位。
- 提交前自查:`git diff --stat --cached` 出现 `docs/` 或 `Bin` 时先停下确认。

## 关键路径
- 工作流引擎:`core/runtime/workflow.py`(DAG 游标执行)+ `core/runtime/graph.py`(图编排纯逻辑:Condition/HumanApproval/validate_graph)
- 工具分发:`core/services/tools.py`(type: builtin / http / agent / mcp)
- LLM 网关:`core/integrations/llm.py`(OpenAI 兼容,ReAct tool_calls)
- MCP client:`core/integrations/mcp_client.py`(stdio JSON-RPC)
- 模型接入:`core/services/user_models.py`(BYOK + probe-models 拉取)
- 数据模型:`core/db/models.py`

## 工作约定
- 不主动 `git commit`/`push`,除非明确要求。
- 提交前先跑 lint + 测试,stage 仅相关文件,绝不提交 secrets。
- 不猜 URL;不引入未确认的库。
