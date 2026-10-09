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
- **用户私有模型**：每个用户可配置连接协议、base_url、api_key 和模型名，支持 OpenAI-compatible Chat Completions 与 Anthropic Messages 原生接口
- 模型能力标注：是否支持图片/文档/深度思考（reasoning），前端按能力展示对应 UI 开关
- 支持 DashScope（通义千问）、DeepSeek 等公网 HTTPS 兼容接口；选择 Anthropic 协议时可接入 Claude 或提供 Messages 协议的网关
- 用户可设置默认模型、测试模型连通性和多模态能力
- OpenAI 协议继续使用 Bearer 与 `/chat/completions`；Anthropic 使用 `x-api-key`、`anthropic-version: 2023-06-01` 与 `/v1/messages`，Base URL 可填写根地址或 `/v1`。模型列表按协议拉取，Anthropic 支持分页。
- 两种协议统一处理图片、工具调用及结果回填、文本流式输出；Anthropic 思考块和工具结果会保留协议所需的原始签名，记忆/查询理解等 LangChain 辅助调用也使用相同协议。
- Anthropic 默认使用厂商温度，不发送温度覆盖；显式原生深度思考请求使用 adaptive thinking，需所选模型支持。Anthropic 预设默认关闭该能力，避免对旧模型作错误声明。
- Anthropic 系统模型读取服务器 `ANTHROPIC_API_BASE` 与 `ANTHROPIC_API_KEY`；私有模型读取各自加密保存的连接信息。旧模型记录默认保持 OpenAI 协议，无需数据库迁移。
- 协议依据：[Anthropic Messages](https://platform.claude.com/docs/en/api/http/messages/create)、[流式事件](https://platform.claude.com/docs/en/build-with-claude/streaming)。

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

### 市场中的 MCP 服务
- Agent 启动只提供已绑定 MCP 的名称、用途、主机域名和可用工具数量，不连接全部 MCP 或展开工具定义。模型调用内置 `tool_search`，在 Agent 已绑定的工具白名单内按中英文关键词检索；排序优先匹配具体能力，而非仅按服务名称或通用动作词取字母顺序的前几个工具。命中工具的 Schema 加入下一轮请求，实际调用时才连接 MCP。各服务通过同一 MCP 工具适配器接入：声明、参数和结果按 MCP 协议映射，调用决策由模型完成；运行层不加入某个服务的业务规则或改写模型回复。搜索、加载、执行都检查当前工作区权限；配置或工具定义改变后需重新搜索。
- Skill 在同一市场入口创建、导入 Markdown/ZIP、共享、停用和保存新版本。Agent 可绑定多个固定版本；启动上下文只注入名称、用途和版本，`load_skill` 按需加载正文，`read_skill_file` 再读取引用文件。发布快照固定 Skill 版本，发布后的引用可复制到智能体副本。
- Skill 的 `scripts/` 文件不会在导入或加载时执行。管理员批准对应版本且部署了 `SKILL_SANDBOX_IMAGE` 后，`run_skill_script` 才能在 Docker 沙箱运行 Python/JavaScript：禁用网络、只读包目录、临时工作目录、资源和输出限制。未安装 Docker 或镜像未配置时返回明确错误，不直接执行后端主机上的脚本。
- 侧栏统一使用「市场」入口，包含「发现」和「我的资源」两个视图。发现页提供智能体、MCP 和 Skill 能力；我的资源管理已接入 MCP、Skill、工具、知识库和提示词。Builder 的 MCP 和提示词入口都跳转到这里。
- MCP 查询使用当前工作区已有服务、已发现的工具及部署者提供的模板。发现页在模板为空时仍显示已接入服务，支持按名称和工具搜索；不会自动导入外部公共目录。
- 「发现」用于浏览能力摘要、查看模板和接入新服务；「我的资源」用于检测、认证、编辑、共享和移除已接入服务，以及管理 Skill 版本。两者引用同一资源记录，切换视图不会复制连接。认证标签区分未配置、待授权、已配置和检测已验证；最新检测失败时保留工具缓存，但不继续显示旧的认证已验证状态。
- 工具管理统一在「市场 → 我的资源 → 工具」，侧栏不再重复提供工具页。平台联网搜索由 Agent 配置页的 `tool_policy.web_search_enabled` 开关控制，默认关闭，随发布快照固定；开启后模型按需调用 `web_search`，不会在每条消息开始时强制搜索。聊天页不再有临时开关，请求不能绕过 Agent 禁用设置。
- 可添加公网 HTTPS 的 Streamable HTTP 或旧版 HTTP+SSE 服务；stdio 运行在后端所在机器。部署者设置 `MCP_ALLOW_CUSTOM_STDIO=true` 后，管理员可在网页填写启动命令、参数数组、环境变量，或导入 `{"mcpServers":{"name":{"command":"python","args":["server.py"],"env":{}}}}` 格式的配置。保存后检测连接即可获取工具。修改环境变量会加密保存；编辑时不回显变量值，未修改的值会沿用。
- 自定义 stdio 是启动后端机器上的程序，只对受信任管理员开放，默认关闭；普通成员可在智能体中绑定已共享的服务。关闭自定义配置时，管理员仍可从部署者的 `MCP_STDIO_TEMPLATES_JSON` 中选择固定命令模板。程序、依赖和路径需要在后端所在机器上可用；登记本身不自动安装软件。
- 服务登记后点击「检测连接」，缓存 Tools、Resources、Prompts 清单。智能体 Builder 可同时绑定多个 MCP 服务，并逐个勾选可交给模型使用的工具；现有内置/HTTP 工具继续并用。
- 远程服务支持无认证、Bearer、管理员完成的浏览器 OAuth 授权和 Client Credentials。Bearer、stdio 环境变量、OAuth 客户端密钥及令牌使用 `API_KEY_ENCRYPTION_KEY` 加密，接口不回显密钥。浏览器 OAuth 支持动态注册，也可填写预注册应用的 Client ID/Secret。开发环境回调默认 `http://127.0.0.1:8000/api/mcp/oauth/callback`；生产需显式配置 `MCP_OAUTH_REDIRECT_URL`，并在应用提供方登记完全一致的地址。`MCP_CLIENT_METADATA_URL` 可选，启用时应指向本部署公开的 `/api/mcp/oauth/client-metadata` 地址。
- [GitHub 远程 MCP](https://github.com/github/github-mcp-server/blob/main/docs/host-integration.md) 不支持动态客户端注册：使用 `https://api.githubcopilot.com/mcp/` 时，选择 Bearer 并填写有效 PAT，或选择 OAuth 并填写自己已注册 GitHub App/OAuth App 的 Client ID、Secret。仅选择 OAuth 不会自动创建 GitHub 应用。
- 草稿使用当前 MCP 选择；发布快照记录服务及工具白名单。停用服务会立即停止调用。复制已发布智能体时，仅复制已共享、同工作区可用的 MCP 绑定。
- 官方 MCP Python SDK v2 使用 Python 3.11；其依赖要求已同步升级 FastAPI/Pydantic。生产连接统一走官方 SDK，旧手写 stdio 客户端暂留作兼容测试。

### 会话记忆
- 当前会话的近期用户/助手消息始终按原角色传入模型，并按用户、工作区、智能体、会话及本轮消息 ID 隔离；本轮消息不重复，后续并发消息不混入。Builder 中「近期上下文消息数」控制窗口，「历史摘要压缩」仅控制更早对话的摘要保留，不再把关闭摘要当成单轮聊天。近期原文不再拼入系统提示词，摘要和用户长期画像分别处理。
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
API_KEY_ENCRYPTION_KEY=          # 保存用户模型/工具/MCP 密钥前必填；API 与 worker 使用同一固定密钥

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
| MCP Catalog | `GET/POST /api/mcp/servers` `PATCH/DELETE /api/mcp/servers/{id}` `POST .../{id}/probe` | 市场中的 MCP 服务接入与能力检测 |
| MCP Bindings | `GET/PUT /api/agents/{id}/mcp-bindings` | 为智能体选择多个 MCP 服务及其工具 |
| MCP OAuth | `POST /api/mcp/servers/{id}/oauth/start` `GET /api/mcp/oauth/callback` | 管理员授权工作区远程服务 |
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
