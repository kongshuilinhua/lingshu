# Lingshu Agent

本地可运行的自定义智能体平台，提供账号体系、智能体管理、知识库 RAG、工具集成、发布审核与多轮会话的完整闭环。

---

## 项目概述

Lingshu Agent 是一个全栈智能体平台，后端基于 FastAPI + MySQL，前端基于 Vite + React 18，支持用户创建、配置和发布自定义 AI 智能体，并在聊天页面中进行多轮对话。

核心闭环：**用户注册 → 配置私有模型 → 创建智能体（绑定知识库 + 工具 + 提示词）→ 发布审核 → 内部市场复制 → 多轮聊天（支持深度思考 + RAG + 附件）。**

---

## 核心功能

### 账号与权限
- 本地注册/登录，JWT 鉴权
- 个人资料管理（名称、头像）
- `admin` / `user` 双角色：管理员审核智能体、查看成员列表；普通用户管理自己的智能体
- 邀请制加入工作空间（可选，通过 `INVITE_API_ENABLED` 开关）

### 模型配置
- **系统模型**：管理员预设的模型配置，供所有用户选择
- **用户私有模型**：每个用户可配置自己的模型供应商（base_url + api_key + 模型名），支持 OpenAI 兼容接口
- 模型能力标注：是否支持图片/文档/深度思考（reasoning），前端按能力展示对应 UI 开关
- 支持 DashScope（通义千问）、DeepSeek 或公网 HTTPS 的 OpenAI 兼容接口
- 用户可设置默认模型、测试模型连通性和多模态能力

### 智能体管理
- 创建/编辑/删除智能体，配置名称、头像、开场白、系统提示词
- 选择系统模型或用户私有模型
- 绑定知识库（多对多）
- 绑定工具（多对多）
- 草稿模式调试聊天（仅创建者可见）
- 发布 → 管理员审核 → 进入内部市场 → 其他用户可复制使用
- 版本快照：每次发布保存完整配置快照，支持版本列表查看
- 记忆画像（Memory Profile）：按用户 + 智能体维度存储用户偏好和事实

### 聊天交互
- 仅可选择已审核发布的智能体进行聊天
- SSE 流式输出，实时展示生成内容
- 支持 Markdown 渲染、表格、代码块复制
- 按模型能力可选开启单轮「深度思考」（reasoning）
- 多模态附件：支持图片上传/粘贴，TXT/MD/CSV/PDF/DOCX 文档解析为本轮上下文
- 会话管理：新建/切换/删除历史会话，标题自动生成
- 消息反馈（好评/差评 + 评论）

### RAG 检索增强
- 默认开启 RAG，支持单轮关闭 / RAG pill 标记
- **查询理解前置**：一次轻量 LLM 调用做问题重写/多轮指代补全 + 意图分类 + 路由 + 低置信澄清，任何失败降级为等价现状（可选 LangChain `with_structured_output` 解析，`QU_PARSER`）
- 检索管线：
  - **Parent-Child Chunk + 父块扩展（small-to-big）**：子块负责精确命中与排序，喂给 LLM 时换成对应父块全文并按 parent_id 去重（`RAG_PARENT_EXPANSION`）
  - **Dense Retrieval**：向量相似度检索（Embedding → Milvus/内存）
  - **中文 BM25**：关键词稀疏检索，与 Dense 互补
  - **RRF 融合**（Reciprocal Rank Fusion）：合并 Dense + BM25 排序结果
  - **可选 Rerank**：`qwen3-rerank` 模型精排
  - **Redis 缓存**：相同 query 在 TTL 内直接返回缓存结果
- **可选 CRAG 自纠检索**（LangGraph）：检索 → LLM 相关性评分 → 证据不足则改写查询重检索，有界轮数 + 失败回退原生单趟（`RAG_SELF_CORRECT`，默认关）
- 结构化引用来源展示
- 证据不足时拒绝回答（`RAG_REFUSE_WHEN_NO_EVIDENCE`）

### 知识库管理
- 创建知识库（名称 + 描述）
- 支持文本直接录入和文件上传（TXT/MD/CSV/PDF/DOCX；可选 LangChain DocumentLoaders 接入 HTML，`INGEST_LANGCHAIN_LOADERS`）
- **异步入库**：上传请求立即返回（状态 `indexing`），分块/向量化/落库经后台执行，状态机 `indexing → indexed/failed`；前端按状态轮询刷新
- 文档入库 → 文本提取 → 分段存储为 parent-child chunk → 写入向量库 + MySQL
- **失败重试**：失败文档可一键「重新索引」，原子状态守卫防并发重复执行
- **崩溃恢复**：进程重启时复位卡在 `indexing` 的文档为 `failed`，允许重试
- **扫描件/空 PDF 明确报错**：提取不到文本时直接标失败并提示，不静默入库成空
- 文档列表、删除文档（同步清理 MySQL + 向量数据）
- 整库重建（reindex）：异步重建全部文档索引，返回 running job
- 支持自定义分段策略（层级分段 / 自定义分块参数 / 预览）
- 索引作业状态查询（通过 Redis）

### 工具集成
- **内置工具**：系统预置，所有用户可用（如 web_search）
- **用户自定义 HTTP 工具**：CRUD 管理，支持 GET/POST 方法
  - 配置请求头、查询参数、请求体 Schema
  - 认证方式：API Key（Header/Query）、Bearer Token、Basic Auth
  - 响应路径提取（JSONPath）
  - 超时设置
- 工具测试：填入参数即时测试工具连通性
- 绑定到智能体，Agent 在对话中按需调用
- 运行记录（Run/RunStep）追踪每次工具调用

### 会话记忆
- Session Summary 记忆：最近窗口 + 旧轮次 LLM 增量摘要，超阈值时把较旧对话压成摘要、保留最近若干轮原文，摘要失败降级保留旧摘要（`MEMORY_SUMMARY_*`）
- Memory Profile：用户级记忆画像，跨会话持久化
- 发布快照隔离：草稿聊天使用当前配置，已发布聊天使用发布时的快照配置

### 工作流引擎
- 基于节点的可视化工作流执行（Start → Knowledge → Tool → LLM → Answer）
- 每个节点产生 RunStep 记录，可追溯执行路径
- 支持自定义工作流节点顺序

### 评测与可观测
- 提供 `eval/rag_cases.jsonl` 评测数据集
- 运行脚本：`python eval/run_rag_eval.py --mock`（mock 模式免 API 调用快速验证）
- **ragas 离线评测**（可选）：`python eval/run_ragas_eval.py --live` 真实检索+生成后计算 faithfulness / answer_relevancy / context_precision
- **LangSmith 全链路追踪**（可选）：设 `LANGSMITH_TRACING=true` + key 后，LangChain/LangGraph 调用自动上报

---

## 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| 后端框架 | FastAPI (Python 3.11) | 异步 API，Uvicorn 服务器 |
| 前端 | Vite + React 18 + JavaScript + CSS | 固定端口 `127.0.0.1:5174` |
| 数据库 | MySQL 8.0 | SQLAlchemy ORM，20+ 张表 |
| 向量存储 | Milvus / 内存回退 | `LINGSHU_VECTOR_BACKEND` 切换 |
| 缓存 | Redis | RAG 缓存 + 索引作业状态 |
| LLM 网关 | OpenAI 兼容接口 | DashScope / DeepSeek / 自定义 |
| Embedding | OpenAI 兼容接口 | 默认 `text-embedding-v4` |
| Rerank | OpenAI 兼容接口 | 默认 `qwen3-rerank` |
| LangChain 生态（可选，flag 控制） | LangChain / LangGraph / LangSmith / ragas | 结构化输出抽取、CRAG 自纠检索、全链路追踪、RAG 离线评测；默认走原生实现，开关切换 |
| 中文分词 | jieba | BM25 检索的中文分词 |
| BM25 检索 | rank-bm25 | 关键词稀疏检索，与 Dense 互补 |
| 文档解析 | PyPDF / stdlib (zipfile+xml) | PDF + DOCX，纯 Python 无额外依赖 |
| 网络搜索 | DuckDuckGo HTML | 可选，`WEB_SEARCH_ENABLED` 开关 |
| 测试 | PyTest | 单元测试 + 集成测试 + RAG 评测 |
| 容器化 | Dockerfile.api + docker-compose.yml | 一键部署 |

---

## 项目结构

```
langchain/
├── api/                            # FastAPI 应用层
│   ├── main.py                     #   40+ API 端点 + SSE 流式聊天
│   ├── deps.py                     #   依赖注入（当前用户/工作空间）
│   └── schemas.py                  #   Pydantic 请求/响应模型
├── core/                           # 核心业务层
│   ├── config.py                   #   Pydantic Settings 配置管理
│   ├── db/                         #   数据库
│   │   ├── models.py               #     20+ SQLAlchemy 模型（User→Feedback）
│   │   ├── base.py                 #     declarative base
│   │   └── session.py              #     会话工厂 + init_db
│   ├── integrations/               #   外部集成
│   │   ├── llm.py                  #     OpenAI 兼容 LLM 网关（chat + embedding）
│   │   └── vector_store.py         #     向量存储抽象（Milvus + 内存回退）
│   ├── runtime/                    #   运行时引擎
│   │   └── workflow.py             #     WorkflowRunner：节点式工作流执行
│   ├── security/                   #   安全
│   │   ├── auth.py                 #     JWT 创建/验证
│   │   ├── api_keys.py             #     API Key 加密存储
│   │   └── permissions.py          #     角色鉴权（admin/user）
│   └── services/                   #   业务服务
│       ├── agents.py               #     智能体 CRUD + 发布/审核/复制
│       ├── knowledge.py            #     知识库 CRUD + 文档入库/索引/分段
│       ├── rag.py                  #     RAG 检索管线（Dense+BM25+RRF+Rerank+Cache）
│       ├── rag_cache.py            #     Redis RAG 缓存
│       ├── tools.py                #     工具 CRUD + 执行 + 测试
│       ├── models.py               #     系统模型管理
│       ├── user_models.py          #     用户私有模型管理
│       ├── memory.py               #     会话记忆 + Memory Profile
│       ├── prompt_templates.py     #     提示词模板管理
│       ├── uploads.py              #     文件上传管理
│       ├── web_search.py           #     网络搜索（DuckDuckGo）
│       └── bootstrap.py            #     首次启动初始化（默认工具/模型/工作空间）
├── frontend/                       # React 18 前端
│   └── src/                        #   Vite + JavaScript + CSS
├── eval/                           # 评测
│   ├── rag_cases.jsonl             #   RAG 评测数据集
│   └── run_rag_eval.py             #   评测运行脚本
├── scripts/                        # 工具脚本
│   ├── check_markdown_links.py     #   Markdown 链接检查
│   ├── check_text_encoding.py      #   文本编码检查
│   └── release_check.py            #   发布前检查
├── tests/                          # 测试
│   ├── conftest.py                 #   测试配置（隔离 MySQL/模拟向量库）
│   ├── test_api_knowledge.py       #   知识库 API 测试
│   ├── test_knowledge_service.py   #   知识库服务测试
│   ├── test_models.py              #   模型配置测试
│   ├── test_platform_api.py        #   平台 API 集成测试
│   ├── test_rag_eval.py            #   RAG 评估测试
│   └── test_vector_store.py        #   向量存储测试
├── .env.example                    # 环境变量模板
├── requirements.txt                # Python 依赖
├── Dockerfile.api                  # API 容器镜像
└── docker-compose.yml              # 一键部署编排
```

---

## 环境变量

```env
# 安全密钥
JWT_SECRET=replace-with-a-long-random-secret
API_KEY_ENCRYPTION_KEY=          # 可选，用于加密存储用户 API Key

# 数据库
DATABASE_URL=mysql+pymysql://lingshu:lingshu@192.168.150.101:3306/lingshu_agent

# LLM 网关（三选一即可，优先使用 DASHSCOPE_API_KEY）
OPENAI_API_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1
DASHSCOPE_API_KEY=
OPENAI_MODEL=qwen-plus
OPENAI_EMBEDDING_MODEL=text-embedding-v4

# DeepSeek（可选）
DEEPSEEK_API_KEY=
DEEPSEEK_MODEL=deepseek-chat

# 向量后端（memory 或 milvus）
LINGSHU_VECTOR_BACKEND=memory
MILVUS_URI=http://192.168.150.101:19530
MILVUS_COLLECTION=lingshu_chunks

# RAG 参数
RAG_TOP_K=4                       # 最终返回文档数
RAG_DENSE_TOP_K=12                # Dense 检索候选数
RAG_BM25_TOP_K=12                 # BM25 检索候选数
RAG_RRF_K=60                      # RRF 融合参数
RAG_RERANK_ENABLED=true           # 是否启用 Rerank
RAG_RERANK_MODEL=qwen3-rerank     # Rerank 模型
RAG_CACHE_TTL_SECONDS=3600        # RAG 缓存过期时间
RAG_REFUSE_WHEN_NO_EVIDENCE=true  # 证据不足时拒答
RAG_PARENT_EXPANSION=true         # 父块扩展（small-to-big）

# 会话记忆摘要
MEMORY_SUMMARY_ENABLED=true       # 旧轮次 LLM 增量摘要
MEMORY_SUMMARY_MAX_CHARS=800
MEMORY_KEEP_RECENT_TURNS=3

# LangChain 生态（均默认关/原生，按需开启）
QU_PARSER=native                  # native | langchain（查询理解结构化输出）
RAG_SELF_CORRECT=false            # LangGraph CRAG 自纠检索
INGEST_LANGCHAIN_LOADERS=false    # LangChain DocumentLoaders（HTML 等）
LANGSMITH_TRACING=false           # LangSmith 全链路追踪（需 LANGSMITH_API_KEY）

# 网络搜索
WEB_SEARCH_ENABLED=true
WEB_SEARCH_PROVIDER=duckduckgo_html

# 其他
INVITE_API_ENABLED=false          # 邀请 API 开关
UPLOAD_MAX_BYTES=31457280         # 上传文件大小上限（30MB）
```

---

## 快速启动

### 基础环境

```powershell
uv --version
uv python install 3.11
uv venv --python 3.11
uv pip install -r requirements.txt
node --version   # 确认 >= 18（https://nodejs.org/）
```

### 基础设施（用 Docker 只跑数据库，API 和前端手动跑）

```powershell
# 启动 mysql + redis + milvus
docker compose up -d mysql redis milvus
```

```env
# .env 中的连接地址指向 docker-compose 映射的本地端口
DATABASE_URL=mysql+pymysql://lingshu:lingshu@localhost:3306/lingshu_agent
REDIS_URL=redis://localhost:6380/0
LINGSHU_VECTOR_BACKEND=milvus
MILVUS_URI=http://localhost:19530
```

MySQL / Redis / Milvus / MinIO 可以继续使用已有虚拟机或 Docker；uv 只管理本地 Python 解释器和依赖，不接管这些外部服务。如果没有 Docker，也可以单独安装这些服务，或者开发阶段用内存向量模式（`LINGSHU_VECTOR_BACKEND=memory`），不装 Milvus 和 Redis 也能跑。

### 后端

```powershell
Copy-Item .env.example .env          # 编辑 .env，填写 DASHSCOPE_API_KEY
uv run uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload
```

### 前端

```powershell
cd frontend
npm install
npm run dev   # http://127.0.0.1:5174
```

首次启动后端时会自动创建数据库表、默认工作空间和系统模型。

### 端口约束

| 服务 | 地址 | 说明 |
|------|------|------|
| Backend API | `http://127.0.0.1:8000` | 不可变 |
| Frontend Dev | `http://127.0.0.1:5174` | 不可变（`--strictPort`） |

### 生产模式防呆

默认配置面向本地开发。需要部署到生产环境时，先显式设置：

```env
LINGSHU_DEPLOYMENT_MODE=production
```

生产模式会在启动时阻断以下不安全组合：默认或过短的 `JWT_SECRET`、未配置 `API_KEY_ENCRYPTION_KEY`、`LINGSHU_VECTOR_BACKEND=memory`、未启用 Redis/Celery、`CORS_ORIGINS=*`、默认数据库口令、默认 MinIO 凭据、`LINGSHU_MOCK_LLM=true`。

这个边界用于区分本地开发与生产部署：开发环境保持开箱即用，生产环境必须显式配置持久化、队列、密钥和外部依赖。

---

## 主要 API

| 模块 | 端点 | 说明 |
|------|------|------|
| Auth | `POST /api/auth/register` `POST /api/auth/login` `GET/PATCH /api/auth/me` | 注册/登录/个人资料 |
| Workspace | `GET /api/workspaces/current` `GET /api/workspaces/members` | 工作空间与成员 |
| Invites | `GET/POST /api/workspaces/invites` | 邀请管理 |
| System Models | `GET /api/models` `POST/PATCH/DELETE /api/admin/models` | 系统模型管理 |
| User Models | `GET/POST/PATCH/DELETE /api/user-models` `POST /api/user-models/{id}/test` | 用户私有模型 |
| Agents | `GET/POST /api/agents` `GET/PATCH/DELETE /api/agents/{id}` | 智能体管理 |
| Publish | `POST /api/agents/{id}/publish` | 发布（需审核） |
| Review | `GET /api/admin/agent-reviews` `POST .../{id}/approve` `POST .../{id}/reject` | 审核 |
| Market | `GET /api/market/agents` `POST /api/market/agents/{id}/copy` | 内部市场 |
| Workflow | `GET/PATCH /api/agents/{id}/workflow` | 工作流配置 |
| Chat | `POST /api/agents/{id}/chat/stream` | SSE 流式聊天 |
| Sessions | `GET /api/agents/{id}/sessions` `GET/PATCH/DELETE /api/sessions/{id}` | 会话管理 |
| Runs | `GET /api/runs/{id}` `GET /api/runs/{id}/steps` | 运行记录 |
| Feedback | `POST /api/messages/{id}/feedback` | 消息反馈 |
| Knowledge | `GET/POST /api/knowledge-bases` `POST .../{id}/documents` `POST .../{id}/index` `POST .../documents/{id}/reindex` `DELETE ...`| 知识库管理（含失败文档重试） |
| Tools | `GET/POST /api/tools` `PATCH/DELETE /api/tools/{id}` `POST .../{id}/test` | 工具管理 |
| Prompt Templates | `GET/POST /api/prompt-templates` | 提示词模板 |
| Uploads | `POST /api/uploads` | 文件上传 |
| Search | `GET /api/search/test` | 网络搜索测试 |
| Health | `GET /api/health` | 健康检查（数据库/Redis/向量库/模型探活） |

---

## 架构

```
用户浏览器 (React 18)
    │  HTTP / SSE
    ▼
FastAPI (api/main.py)
    │
    ├─→ Auth (JWT 鉴权)
    ├─→ CRUD API (Agents / Knowledge / Tools / Models)
    └─→ Chat Stream
          │
          ▼
     WorkflowRunner (core/runtime/workflow.py)
          │
          ├─→ [Start]     接收用户输入 + 附件 + 记忆
          ├─→ [Knowledge]  RAG 检索 (Dense + BM25 + RRF + Rerank)
          ├─→ [Tool]       执行绑定的 HTTP 工具
          ├─→ [LLM]        调用 LLM (OpenAI 兼容接口) 生成回答
          └─→ [Answer]     输出最终回答 + 引用来源
          │
          ├─→ MySQL (消息/Session/Run/RunStep 持久化)
          ├─→ Milvus / 内存 (向量检索)
          ├─→ Redis (RAG 缓存)
          └─→ LLM Provider (DashScope / DeepSeek / 自定义)
```

---

## 测试

```powershell
# 发布检查必须使用 Python 3.11；本地推荐通过 uv 免激活运行。
uv run python scripts/release_check.py --with-frontend

# 全量测试需指向一次性测试库（切勿用生产库），或安装 Docker 供 testcontainers 启动 MySQL
$env:TEST_DATABASE_URL = "mysql+pymysql://lingshu:lingshu@<host>:3306/lingshu_agent_test"
$env:DATABASE_URL = $env:TEST_DATABASE_URL
$env:LINGSHU_MOCK_LLM = "true"; $env:LINGSHU_VECTOR_BACKEND = "memory"
uv run python -m pytest tests/ --timeout=60
```

- **CI（GitHub Actions）**：`CI`(发布检查) + `Lint & Test`(ruff + compile + pytest) 两条流水线。
- `scripts/release_check.py` 在没有 `TEST_DATABASE_URL` 时也会运行不依赖数据库的轻量 pytest 子集，避免发布检查只做编译不跑测试。
- CI 自带 MySQL service，并运行完整测试集；本机未提供测试库或 Docker 时可先运行 `scripts/release_check.py` 的轻量测试集。
- MySQL 约束迁移逐项检查列、索引和触发器；权限不足时启动失败，避免数据库停留在约束缺失状态。

---

## 许可证

MIT
