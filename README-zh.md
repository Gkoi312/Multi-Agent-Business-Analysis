# AgentHarness — 多智能体编排与评测平台

一个基于 `LangGraph + FastAPI` 的**可插拔领域技能、内置记忆管理、带评测框架的 AI Agent 基础设施平台**。

项目采用前后端分离架构：后端使用 `FastAPI` 提供 JSON API，前端使用 `React + Vite` 提供浏览器界面。首个领域应用为**企业尽调报告生成**。

## 项目定位

```
操作系统  :  应用程序  =  AI Harness  :  尽调 / 股评 / 法审 Agent
```

本项目不是某个具体的 Agent 应用，而是所有 Agent 应用共享的**基础设施层（Harness）**。它提供：

1. **Agent Runtime** — 编排引擎，管理状态、并行、中断、恢复
2. **Tool Integration** — 标准化的工具注册、调用、结果清洗管线
3. **Memory & Context** — 短期对话压缩、结构化工作记忆、长期向量记忆
4. **Human-in-the-Loop** — 审批流、反馈注入、中断/继续
5. **Observability** — Trace、Metrics、Events、Cost tracking
6. **Evaluation** — 离线评测（fixture 仿真）、在线监控、可靠性分析

## 项目亮点

- 基于 `LangGraph` 构建的**可插拔领域多智能体**工作流
- **Harness + Domain 分层架构**：引擎与业务逻辑解耦，换领域只需写 adapter
- **SqliteSaver 持久化检查点**：任务失败后从精确失败节点恢复，服务重启不丢进度，零 token 浪费
- 人工参与闭环的分析师审核与重生成（Human-in-the-Loop）
- 并行访谈（Fan-out）+ 并行报告撰写流水线
- **9 级搜索清洗管线**（URL 规范化 → 清洗 → 去重 → 近似去重 → 相关性 → 质量 → 结构化 → 安全守卫 → 格式化）
- **7 大搜索适配器**：Serper、Tavily、Bocha、SEC EDGAR、CNINFO、GitHub、Jina/Direct Reader
- **4.5K 行记忆系统**：增量压缩、SPDV 事实协调、上下文窗口管理、token 预算强制执行
- 中文报告输出：楷体排版、小四字号、两端对齐、CJK 字体 PDF
- 支持导出 `DOCX` 与 `PDF` 双格式报告
- 异步任务运行时：任务状态持久化、事件日志、失败重试、重启恢复
- 多模型提供方支持：`openai`、`google`、`groq`、`deepseek`

## 为什么做这个项目

这个项目关注的不是"单轮对话式回答"，而是如何让多智能体系统支持结构化的商业分析流程 — **并且让这套基础设施可以复用到其他领域**。

系统不会一次性直接生成最终答案，而是将整个过程拆分为分析师规划、人工审核、联网研究、访谈执行、章节撰写、报告整合与文件导出。所有这些编排逻辑都在 Harness 层以通用方式实现，尽调特有的 prompt 和 schema 在 Domain 层注入。

它适合作为下面几个方向的实践项目：

- 使用 `LangGraph` 进行多智能体编排
- 设计领域与引擎分离的 Agent 平台架构
- 实现持久化检查点与精确节点恢复（SqliteSaver）
- 构建记忆管理系统（事实协调、增量压缩、上下文组装）
- 在智能体工作流中加入人工审批环节
- 在 Web 应用中处理长耗时异步任务
- 结合 LLM 输出与传统文档导出的中文报告生成流程
- 构建 Agent 评测框架与可靠性分析体系

## 当前状态

- ✅ **Phase 1 完成**：Harness 核心层与领域层分离
- ✅ **Phase 2 完成**：工具集成 — ToolRegistry + ToolPipeline + 9 级清洗管线 + 7 大搜索适配器
- ✅ **Phase 3 完成**：记忆管理 — 增量压缩、事实协调、上下文组装、token 预算控制
- ✅ 完整 API + SPA 流程：注册/登录 → 提交公司信息 → 生成分析师 → 人工反馈 → 报告生成 → 导出
- ✅ SqliteSaver 持久化检查点 — 精确节点恢复，服务重启不丢进度
- ✅ 多模型提供方：`openai`、`google`、`groq`、`deepseek`
- 🚧 **Phase 4 进行中**：评测框架（Fixture 仿真、评分、可靠性分析）
- 📋 Phase 5：补充打磨（测试、多行业 Fixture、前端增强）

## 核心能力

1. **多智能体协作**
   - 主图负责分析师生成、并行访谈（Fan-out）与报告整合
   - 子图负责提问 → 搜索 → 回答 → 访谈保存 → 章节撰写
2. **人工参与闭环**
   - 在 `human_feedback` 节点暂停执行，支持"反馈 → 重生分析师 → 再次确认"循环
   - 前端显示分析师版本号（`v1`、`v2`、`v3` ...）
3. **异步任务可观测**
   - 任务状态持久化在 `.runtime/tasks.json`
   - 任务事件写入 `.runtime/task_events.jsonl`
4. **双格式报告导出**
   - 输出目录：`generated_report/<report_name>_<timestamp>/`
   - 输出格式：`.docx` 和 `.pdf`
5. **可插拔领域技能包**
   - YAML 驱动的技能包系统（角色技能 + 研究技能 + 搜索策略 + 领域记忆）
   - 当前支持 `ai` 行业，可扩展至 `manufacturing`、`beauty`、`fmcg`、`internet` 等

## 技术栈

- Python 3.11+
- FastAPI / Uvicorn
- LangGraph / LangChain / `langgraph-checkpoint-sqlite`
- Serper / Tavily / Bocha / SEC EDGAR / CNINFO / GitHub — 多后端搜索
- Jina Reader / Direct Reader — URL 转文本浏览
- SQLAlchemy + SQLite 用户账户存储
- `python-docx` + `reportlab`（CJK TTFont）报告导出
- `structlog` 结构化日志
- Jinja2 模板化 Prompt 管理
- React / Vite / React Router

## 项目结构

```text
.
├── backend/
│   ├── start_api.py                    # API 入口
│   ├── harness/                        # 🆕 Harness 核心平台层
│   │   ├── runtime/                    # Agent Runtime（通用图构建器、fan-out、checkpoint）
│   │   ├── tools/                      # Tool Integration（工具注册、调用管线、搜索适配器）
│   │   ├── memory/                     # Memory & Context（压缩器、工作记忆、上下文窗口）
│   │   ├── human_loop/                 # Human-in-the-Loop（审核门、反馈注入）
│   │   ├── observability/              # 可观测性（任务运行时、trace、metrics）
│   │   ├── evaluation/                 # 评测框架（Runner、Scorer、Fixture、可靠性分析）
│   │   └── models/                     # 通用数据模型（Agent、State、Task、Event）
│   ├── domains/                        # 🆕 领域应用层（可插拔）
│   │   ├── base.py                     # DomainAdapter 基类
│   │   └── due_diligence/              # 尽调领域
│   │       ├── graph.py                # 领域主图
│   │       ├── interview.py            # 访谈子图
│   │       ├── schemas.py              # 领域专用 State 定义
│   │       ├── config.py               # 预留：领域配置
│   │       └── prompts/                # 领域 Prompt 模板
│   ├── skills/                         # 行业技能包（YAML 驱动）
│   │   ├── ai/
│   │   ├── beauty/
│   │   ├── fmcg/
│   │   ├── internet/
│   │   └── manufacturing/
│   ├── app/                            # Web 应用层
│   │   ├── api/                        # FastAPI 路由 + Service
│   │   ├── services/                   # 技能注册等
│   │   ├── utils/                      # 模型加载等
│   │   ├── exception/                  # 统一异常处理
│   │   ├── logger/                     # 结构化日志
│   │   └── database/                   # 用户认证数据库
│   ├── tests/                          # 🆕 测试目录
│   │   ├── harness/                    # Harness 核心测试
│   │   └── fixtures/                   # 评测 Fixture
│   ├── .runtime/
│   ├── generated_report/
│   └── users.db
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   ├── pages/
│   │   ├── hooks/
│   │   ├── api.ts
│   │   └── types.ts
│   ├── package.json
│   └── vite.config.ts
├── docs/
│   └── Harness改造计划书.md
└── README-zh.md
```

### 分层架构

```
┌──────────────────────────────────────────────────────────────┐
│                    应用层 (Domain Apps)                        │
│   business-due-diligence  │  stock-analysis  │  legal-review  │
│   (每个应用 = skill_pack.yaml + prompts/ + graph.py)           │
├──────────────────────────────────────────────────────────────┤
│                  Harness 核心层 (Agent Harness Core)           │
│                                                              │
│  ┌─────────────┐ ┌──────────────┐ ┌───────────────────────┐  │
│  │ Agent        │ │ Tool         │ │ Memory & Context      │  │
│  │ Runtime      │ │ Integration  │ │ Manager               │  │
│  └─────────────┘ └──────────────┘ └───────────────────────┘  │
│  ┌─────────────┐ ┌──────────────┐ ┌───────────────────────┐  │
│  │ Human-in-    │ │ Observability│ │ Evaluation             │  │
│  │ the-Loop     │ │              │ │ Framework             │  │
│  └─────────────┘ └──────────────┘ └───────────────────────┘  │
├──────────────────────────────────────────────────────────────┤
│                 基础设施层 (Infrastructure)                     │
│   Model Loader  │  DB (SQLite)  │  File Storage  │  HTTP     │
└──────────────────────────────────────────────────────────────┘
```

## 快速开始

### 1. 安装后端依赖

```bash
python -m venv .venv

# Windows PowerShell
.venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

### 2. 配置环境变量

在项目根目录创建 `.env`，或复制 `.env.example` 后再填写实际值：

```env
# 必填：LLM 提供方
LLM_PROVIDER=openai

# 通用模型参数
LLM_MODEL_NAME=qwen-plus
LLM_TEMPERATURE=0
LLM_MAX_OUTPUT_TOKENS=4096

# 当 LLM_PROVIDER=openai 时使用
OPENAI_BASE_URL=
OPENAI_API_KEY=your-openai-key

# 当 LLM_PROVIDER=google 时使用
GOOGLE_API_KEY=

# 当 LLM_PROVIDER=groq 时使用
GROQ_API_KEY=

# 必填：联网搜索
TAVILY_API_KEY=your-tavily-key

# 后端运行根目录
APP_ROOT=backend

# 允许前端开发域名跨域访问
FRONTEND_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

> 请不要把 `.env` 中的真实密钥提交到版本库。

### 3. 启动后端 API

```bash
python backend/start_api.py
```

### 4. 启动前端

```bash
cd frontend
npm install
npm run dev
```

然后在浏览器中打开 `http://localhost:5173`。

## 使用流程

1. 通过前端页面注册并登录
2. 在"创建尽职调查任务"页面中填写：
   - `company_name`（必填）
   - `focus`（可选）
   - `target_role`（可选）
   - `industry_pack`：行业技能包（从 `GET /api/skill-packs` 获取可选值，当前支持 `ai`）
3. 系统进入 `running_generation`，先执行到 `human_feedback` 中断点并生成分析师草案
4. 任务状态变为 `awaiting_feedback`，你可以在任务详情页查看分析师方案并提交反馈
5. 如果提交了反馈，系统进入 `running_feedback`，重生分析师后回到 `awaiting_feedback`
6. 如果反馈为空，系统将其视为已确认，并继续执行研究、访谈和报告生成
7. 当任务状态变为 `completed` 后，可以下载生成的 `DOCX` / `PDF` 文件，并查看风险指标与建议摘要

## 端到端流程

```text
用户输入 → 公司类型分类 → 技能包装配 → 分析师草案 → 人工反馈循环
→ 研究规划 → 并行访谈(Fan-out) → 报告章节合并 → 最终报告整合 → DOCX/PDF 导出
```

## 工作流概览

### 智能体架构

```text
+------------------+      +------------------+      +------------------+
| React + Vite SPA |----->+ FastAPI JSON API +----->+    TaskRuntime   |
+------------------+      +------------------+      +------------------+
                                                       |
                                                       v
                                             +------------------+
                                             |   ReportService  |
                                             +------------------+
                                                       |
                                                       v
                                             +------------------+
                                             | Domain Adapter   |
                                             | (due_diligence)  |
                                             +------------------+
                                                       |
                                                       v
                                             +------------------+
                                             |  classify +      |
                                             |  assemble skills |
                                             +------------------+
                                                       |
                                                       v
                                             +------------------+
                                             |  create_analyst  |
                                             +------------------+
                                                       |
                                                       v
                                             +------------------+
                                             |  human_feedback  |
                                             | (interrupt point)|
                                             +------------------+
                                                       |
                          +----------------------------+---------------------------+
                          |                            |                           |
                          v                            v                           v
                +------------------+         +------------------+       +------------------+
                | conduct_interview|         | conduct_interview|       | conduct_interview|
                |   Analyst 1      |         |   Analyst 2      |       |   Analyst N      |
                +------------------+         +------------------+       +------------------+
                          |                            |                           |
                          +----------------------------+---------------------------+
                                                       |
                                                       v
                           每位分析师执行的访谈子图：
                           ask_question → search_web → generate_answer
                               → save_interview → write_section → review_section
                                                       |
                                                       v
                    +------------------+   +------------------+   +------------------+
                    |   write_report   |   |write_introduction|   | write_conclusion |
                    +------------------+   +------------------+   +------------------+
                              \                   |                    /
                               \                  |                   /
                                \                 |                  /
                                 +----------------------------------+
                                 |         review_report            |
                                 +----------------------------------+
                                                   |
                                                   v
                                 +----------------------------------+
                                 |         finalize_report          |
                                 +----------------------------------+
                                                   |
                                                   v
                                 +----------------------------------+
                                 |            save_report           |
                                 +----------------------------------+
                                           |                 |
                                           v                 v
                                        +------+         +------+
                                        | DOCX |         | PDF  |
                                        +------+         +------+
```

### 任务状态流转

```text
pending（待开始）
  |
  v
running_generation（生成中）
  |
  +--> failed（失败） --(POST /api/tasks/{task_id}/retry)--> running_generation
  |
  v
awaiting_feedback（待反馈）
  |
  +--> (反馈非空) → running_feedback（处理反馈中）→ awaiting_feedback
  |
  +--> (反馈为空) → running_feedback → completed（已完成）
  |
  +--> failed --(POST /api/tasks/{task_id}/retry)--> running_feedback
```

## API 概览

### 页面路由

- `GET /`：登录页
- `GET /signup`：注册页
- `GET /dashboard`：创建尽调任务页
- `GET /tasks`：任务列表页
- `GET /tasks/{task_id}`：任务详情页

### 任务与报告接口

- `POST /api/auth/signup`：注册用户
- `POST /api/auth/login`：用户登录
- `POST /api/auth/logout`：退出登录
- `GET /api/auth/me`：获取当前登录用户
- `GET /api/skill-packs`：获取可用行业技能包列表
- `POST /api/reports`：创建并启动报告任务
- `GET /api/tasks`：当前用户任务列表（JSON）
- `GET /api/tasks/{task_id}`：任务详情（JSON）
- `GET /api/tasks/{task_id}/events`：任务事件流（JSON）
- `POST /api/tasks/{task_id}/feedback`：提交反馈并继续工作流
- `POST /api/tasks/{task_id}/retry`：重试失败任务
- `GET /api/tasks/{task_id}/files/{file_name}`：下载任务输出文件

## 数据与产物

- `users.db`：SQLite 用户账户数据库
- `.runtime/tasks.json`：任务状态持久化文件
- `.runtime/task_events.jsonl`：任务事件日志
- `generated_report/`：生成的报告文件
- `logs/`：应用日志

## 开发说明

### 代码组织原则

- **Harness 层** (`backend/harness/`)：通用基础设施，不包含任何业务逻辑。新增通用能力（工具管线、记忆管理、评测框架）放这里
- **Domain 层** (`backend/domains/`)：领域业务逻辑。新增领域（股评、法审）只需实现 `DomainAdapter` 基类 + 提供 prompts
- **App 层** (`backend/app/`)：Web 应用层（API 路由、Service 编排、数据库）。依赖 harness + domains
- **Skills** (`backend/skills/`)：YAML 技能包，纯配置驱动

### 新增领域应用

1. 在 `backend/domains/` 下创建新的 domain 目录
2. 实现 `domains/base.py` 中的 `DomainAdapter` 接口
3. 提供领域专用 `schemas.py`、`prompts/` 和 `graph.py`
4. 在 `backend/skills/` 下创建对应的 `skill_pack.yaml`

### 改动注意事项

- 状态字段优先集中在 domain 层的 `schemas.py` 中维护，通用类型放在 `harness/models/`
- 所有任务状态变更统一通过 `harness/observability/task_runtime.py` 处理
- Prompt 模板放在对应 domain 的 `prompts/` 目录，不要放在 harness 层
- 旧版路径（`app/schemas/models.py`、`app/prompt_lib/`、`app/workflows/`）已标记为 DEPRECATED，仅做 re-export

## 局限性

- 当前存储方案偏本地开发和演示场景
- 用户认证依赖本地 SQLite，而不是生产级身份系统
- 任务状态和事件采用文件持久化，而不是独立的任务队列或数据库后端
- 报告质量高度依赖模型选择、提示词质量和外部搜索结果
- 当前自动化测试覆盖还比较有限（Phase 4-5 重点改善）

## 后续规划

| Phase | 内容 | 状态 |
|:---:|------|:---:|
| 1 | **基础重构**：Harness 层与 Domain 层分离 | ✅ 完成 |
| 2 | **工具集成与数据清洗**：9 级搜索清洗管线 + 多后端适配器 | ✅ 完成 |
| 3 | **记忆管理与上下文压缩**：增量压缩 + 事实协调 + 上下文组装 | ✅ 完成 |
| 4 | **评测框架**：Fixture 仿真 + 5 维度 Scorer + 可靠性分析 | 🚧 下一步 |
| 5 | **补充打磨**：单元测试 + 多行业 Fixture + 前端可视化增强 | 📋 计划中 |

## 常见问题

### 1. 提示 `TAVILY_API_KEY is missing`

`TAVILY_API_KEY` 是必填项，因为访谈生成依赖实时联网搜索。

### 2. 页面中任务失败

可以通过任务接口查看失败阶段：

- `running_generation` 失败通常是模型配置或搜索配置问题
- `running_feedback` 失败通常与线程状态或模型调用异常有关

你可以通过 `POST /api/tasks/{task_id}/retry` 重试失败任务。

### 3. Token 使用量显示 `N/A`

这意味着当前模型提供方或调用路径没有返回 usage 元数据，但不会影响报告生成和下载。

### 4. 文件下载失败

下载接口会校验 `task_id`、`file_name` 与当前用户之间的关系，以避免跨任务下载。
