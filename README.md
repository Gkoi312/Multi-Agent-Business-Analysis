# Multi-Agent Business Analysis

这是一个基于 **LangGraph + FastAPI + React** 的多智能体企业尽调报告生成系统。项目的核心不是“调用一个大模型写报告”，而是把尽调任务拆成多个专业分析师角色，通过搜索、访谈、记忆压缩、证据追踪、人工反馈和报告整合，形成一套可恢复、可观测、可评估的 Agent 工作流。

这份 README 主要用于面试讲解，重点说明项目架构、核心链路、模块边界和可以被追问的设计点。

## 一句话介绍

用户输入公司名称和关注点后，系统自动生成尽调分析师团队，暂停给用户审核；确认后，每个分析师并行执行多轮“提问 - 搜索 - 回答 - 压缩记忆”的研究流程，最后汇总成结构化报告，并导出 DOCX/PDF。

## 项目定位

项目按三层拆分：

```text
Frontend / API
  负责用户登录、任务提交、任务状态、人工反馈、报告下载

Domain: due_diligence
  负责尽调业务流程本身，包括 LangGraph 图结构、提示词、报告结构

Harness Core
  负责可复用基础设施，包括工具注册、搜索清洗、记忆压缩、上下文组装、观测、评估

Skill Packs
  以 Markdown 数据形式提供行业知识和分析师角色，目前主要是 AI 行业尽调
```

一个比较适合面试表达的类比：

```text
Harness 像 Agent 基础设施层
due_diligence 像具体业务应用
skills/ai 像业务知识插件
frontend/server 像产品交付层
```

## 核心能力

- 多智能体尽调流程：自动生成市场、技术、财务、竞争、风险、报告整合等分析视角。
- Human-in-the-loop：分析师团队生成后通过 LangGraph interrupt 暂停，等待用户反馈。
- 并行访谈：使用 LangGraph `Send` fan-out 为每个分析师启动独立访谈子图。
- 搜索工具体系：统一 `ToolRegistry`，支持 Serper、Tavily、Bocha、GitHub、SEC EDGAR、巨潮资讯等搜索/资料源。
- 搜索结果清洗：URL 规范化、文本清洗、精确去重、近似去重、相关性评分、质量评分、结构化事实提取、Prompt Injection 防护、XML 格式化。
- 记忆与上下文管理：每轮访谈后增量压缩为结构化事实，维护 WorkingMemory、RunningSummary、SourceRegistry，避免长上下文爆炸。
- 证据可追踪：搜索结果被分配稳定 source id，例如 `S1`、`S2`，压缩事实和报告引用围绕 source id 组织。
- 持久化任务运行时：任务状态和事件写入本地 `.runtime`，失败后可通过 retry 恢复。
- Checkpoint 恢复：优先使用 LangGraph `SqliteSaver` 保存图状态，服务重启或节点失败后可从上一个 checkpoint 继续。
- 可观测性：记录任务事件、节点耗时、Token 使用、估算成本，并提供 `/api/tasks/{id}/metrics`。
- 离线评估：包含压缩保真、搜索管线质量、来源可追踪、一致性与可靠性评估。

## 技术栈

- Backend: Python 3.11+, FastAPI, Uvicorn
- Agent Orchestration: LangGraph, LangChain
- LLM: OpenAI-compatible、Google Gemini、Groq、DeepSeek、Kimi
- Search/Browse: Tavily、Serper、Bocha、SEC EDGAR、CNINFO、GitHub Search、Jina/DirectReader
- Persistence: SQLite、JSON/JSONL runtime files、LangGraph checkpoint
- Report Export: python-docx、reportlab
- Frontend: React、Vite、React Router、TypeScript
- Tests/Eval: pytest、自定义 scorer、fixture-driven eval

## 目录结构

```text
.
├── backend/
│   ├── start_api.py                         # 后端启动入口
│   ├── server/                              # FastAPI 交付层
│   │   ├── api/main.py                      # FastAPI app、CORS、启动恢复
│   │   ├── api/routes/report_routes.py      # 登录、任务、反馈、重试、下载接口
│   │   ├── api/services/report_service.py   # API 与 LangGraph 工作流之间的服务层
│   │   ├── api/services/session_store.py    # Cookie session
│   │   ├── database/db_config.py            # SQLite 用户表
│   │   └── config.py                        # 环境变量和路径配置
│   ├── domains/
│   │   └── due_diligence/
│   │       ├── graph.py                     # 主报告生成图
│   │       ├── interview.py                 # 单个分析师访谈子图
│   │       ├── schemas.py                   # LangGraph state 定义
│   │       ├── memory_config.py             # 尽调领域记忆配置
│   │       └── prompts/                     # 分析师、访谈、报告提示词
│   ├── harness/                             # 可复用 Agent 基础设施
│   │   ├── tools/                           # 工具注册、搜索适配器、搜索清洗 pipeline
│   │   ├── memory/                          # 压缩、WorkingMemory、上下文组装、历史摘要
│   │   ├── observability/                   # 任务运行时、trace、metrics、logger
│   │   ├── evaluation/                      # 评估 scorer、可靠性分析、真实 eval 脚本
│   │   ├── models/                          # Agent/Memory 通用模型
│   │   ├── llm_loader.py                    # 多模型供应商加载
│   │   └── skill_registry.py                # Markdown skill pack 加载
│   ├── skills/
│   │   └── ai/                              # AI 行业尽调技能包
│   ├── tests/                               # 单元、集成、回归、评估相关测试
│   ├── .runtime/                            # 任务状态、事件、checkpoint
│   └── generated_report/                    # 生成的 DOCX/PDF
├── frontend/
│   ├── src/App.tsx                          # 路由
│   ├── src/api.ts                           # 前端 API client
│   ├── src/pages/                           # 登录、仪表盘、任务列表、任务详情、报告页
│   └── package.json
├── analyst-nuwa-skill/                      # 外部/实验性分析师技能资料
├── references/                              # 外部源码/实现参考
├── PPT/                                     # 项目展示材料
├── requirements.txt
├── pyproject.toml
└── .env.example
```

## 端到端业务流程

```text
用户注册/登录
  -> 提交公司名称、关注点、目标角色、分析师数量
  -> 创建异步任务
  -> LangGraph 主图开始执行
  -> 固定分类到 ai skill pack
  -> 加载 Markdown 技能包
  -> LLM 生成分析师团队
  -> interrupt_before("human_feedback") 暂停
  -> 用户审核分析师团队
  -> 有反馈：重新生成分析师
  -> 无反馈：进入研究阶段
  -> 为每个分析师并行启动访谈子图
  -> 每个分析师多轮搜索、回答、压缩、更新记忆
  -> 每个分析师写出自己的报告 section
  -> 主图汇总 introduction、body、conclusion
  -> 审查报告
  -> finalize_report
  -> 生成 DOCX/PDF
```

## LangGraph 主图

主图位于 `backend/domains/due_diligence/graph.py`，核心节点如下：

```text
START
  -> classify_company_type
  -> assemble_skills
  -> create_analyst
  -> human_feedback
      -> regenerate_analyst -> human_feedback   # 用户有修改意见
      -> plan_research                          # 用户确认
  -> start_interviews
      -> conduct_interview x N                  # Send fan-out 并行分析师访谈
  -> write_report
  -> write_introduction
  -> write_conclusion
  -> review_report
  -> finalize_report
  -> END
```

关键设计点：

- `human_feedback` 不是普通表单逻辑，而是 LangGraph checkpoint + interrupt 的暂停点。
- `conduct_interview` 是子图，每个分析师获得自己的 `analyst`、`skill_card`、`assigned_plan` 和状态。
- 多个并行分支合并回主图时，`ResearchGraphState` 使用 reducer 控制字段合并策略，例如 list 用 `operator.add`，标量用 `keep_latest`。

## 访谈子图

访谈子图位于 `backend/domains/due_diligence/interview.py`，每个分析师独立运行：

```text
START
  -> ask_question
  -> search_web
  -> generate_answer
  -> compress
  -> update_memory
  -> compact_history
      -> ask_question      # 继续下一轮
      -> save_interview    # 达到轮数或覆盖率足够
  -> write_section
  -> review_section
  -> END
```

这里有一个很重要的架构边界：

- `ask_question`、`search_web`、`generate_answer`、`write_section` 是业务节点，依赖尽调提示词。
- `compress`、`update_memory`、`compact_history`、继续/停止路由来自 `harness.memory.nodes`，是可复用基础设施。

## Skill Pack 机制

技能包在 `backend/skills/ai/`，每个 Markdown 文件描述一种分析师能力：

```text
competition-analyst.md
financial-analyst.md
market-analyst.md
tech-analyst.md
risk-analyst.md
report-integrator.md
domain-memory.md
```

加载方式：

1. `SkillRegistry` 扫描 `backend/skills/<industry>/`。
2. 解析 Markdown frontmatter 和正文。
3. `create_analyst` 将完整技能目录注入分析师生成提示词。
4. 每个分析师绑定一个 `skill_id`。
5. 访谈时对应 `skill_card.body` 被注入该分析师的系统提示词。

这个设计让“行业知识”尽量以数据形式扩展，而不是每加一个行业就改业务代码。

## 工具与搜索管线

工具层位于 `backend/harness/tools/`。

核心对象：

- `ToolRegistry`：统一注册和发现 search/browse 工具。
- `SearchTool`：所有搜索适配器遵循的接口。
- `SearchDocument`：搜索结果进入 pipeline 后的统一内部类型。
- `ToolPipeline`：按 stage 处理搜索结果，并返回 trace。

完整搜索处理链路：

```text
LLM 生成搜索 query
  -> 根据 source_type / policy 选择搜索后端
  -> SearchTool.search 返回 SearchDocument[]
  -> CanonicalizeURLStage
  -> CleanTextStage
  -> ExactDeduplicateStage
  -> NearDuplicateStage
  -> RelevanceScoreStage
  -> QualityScoreStage
  -> StructureFactsStage
  -> OutputGuardStage
  -> FormatDocumentStage
  -> 输出可注入 LLM 的 <Document> XML
```

搜索后端选择逻辑：

- 财报、SEC、10-K、10-Q 等 source type 优先走 `sec_edgar`。
- 公告、巨潮相关 source type 优先走 `cninfo`。
- 普通 Web 搜索按 Serper、Tavily、Bocha、GitHub 等可用顺序选择。
- 对 top 搜索结果会尝试 DirectReader/Jina 进行 deep-read，获取更完整页面内容。

## 记忆与上下文管理

记忆层位于 `backend/harness/memory/`。它解决的是多轮 Agent 研究中的两个问题：

1. 历史消息越来越长，直接塞进模型会超上下文或浪费 token。
2. 搜索事实会重复、冲突、更新，需要结构化维护。

核心组件：

- `IncrementalCompressor`：每轮 Q&A 后提取结构化事实。
- `WorkingMemory`：当前事实账本，是后续 coverage/gap/risk/conflict 的主要真相源。
- `FactReconciler`：基于 subject/predicate/value/period 做事实合并、更新、冲突判断。
- `RunningSummaryManager`：将旧消息压缩成运行摘要。
- `HistoryCompactor`：判断何时压缩历史消息。
- `ContextAssembler`：按照 token budget 组装 LLM 输入，不直接修改 checkpoint 中的原始消息。
- `SourceRegistry`：维护 `S1 -> URL/title/retrieved_at` 的映射。

一轮访谈后的数据流：

```text
search_web
  -> 生成 source_registry: S1/S2/...
  -> 当前轮搜索材料进入 context

generate_answer
  -> 专家回答

compress
  -> 将当前轮问答和来源压缩成 CompressedTurn
  -> 事实必须引用合法 source id

update_memory
  -> WorkingMemory.ingest_compressed_turn
  -> 生成 memory_snapshot

compact_history
  -> 必要时更新 RunningSummary
  -> 后续 LLM 调用由 ContextAssembler 投影上下文
```

面试中可以强调：checkpoint state 是事实记录，ContextAssembler 只是“投影”出本次 LLM 输入，不会原地破坏历史消息。

## 后端 API

主要接口定义在 `backend/server/api/routes/report_routes.py`。

| Endpoint | Method | 说明 |
|---|---:|---|
| `/health` | GET | 健康检查 |
| `/api/auth/signup` | POST | 注册，并写入 session cookie |
| `/api/auth/login` | POST | 登录 |
| `/api/auth/logout` | POST | 登出 |
| `/api/auth/me` | GET | 当前用户 |
| `/api/reports` | POST | 创建尽调任务并后台启动生成 |
| `/api/tasks` | GET | 当前用户任务列表 |
| `/api/tasks/{task_id}` | GET | 任务详情 |
| `/api/tasks/{task_id}/events` | GET | 任务事件 |
| `/api/tasks/{task_id}/metrics` | GET | Token、耗时、成本指标 |
| `/api/tasks/{task_id}/feedback` | POST | 提交分析师反馈并继续图执行 |
| `/api/tasks/{task_id}/retry` | POST | 失败任务从 checkpoint 重试 |
| `/api/tasks/{task_id}/files/{file_name}` | GET | 下载报告文件 |

任务状态大致流转：

```text
pending
  -> running_generation
  -> awaiting_feedback
  -> running_feedback
  -> completed

任何运行阶段失败:
  -> failed
  -> retry 后回到 running_generation 或 running_feedback
```

## 前端结构

前端位于 `frontend/`，主要页面：

- `/login`：登录。
- `/signup`：注册。
- `/dashboard`：提交公司尽调任务。
- `/tasks`：任务列表。
- `/tasks/:taskId`：任务详情、分析师反馈、事件、指标。
- `/tasks/:taskId/report`：报告结果页。

前端通过 `frontend/src/api.ts` 访问后端，默认 API 地址为：

```text
http://localhost:8000/api
```

可通过 `VITE_API_BASE_URL` 覆盖。

## 运行方式

### 1. 安装后端依赖

```bash
python -m venv .venv

# Windows PowerShell
.venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env`，至少配置：

```env
LLM_PROVIDER=openai
LLM_MODEL_NAME=gpt-4o-mini
LLM_TEMPERATURE=0
LLM_MAX_OUTPUT_TOKENS=8192

OPENAI_API_KEY=your_key
OPENAI_BASE_URL=

TAVILY_API_KEY=your_tavily_key

APP_ROOT=backend
FRONTEND_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
SESSION_COOKIE_NAME=session_id
SESSION_COOKIE_SECURE=false
SESSION_COOKIE_SAMESITE=lax
```

可选搜索/API：

```env
SERPER_API_KEY=
BOCHA_API_KEY=
GITHUB_API_TOKEN=
JINA_API_KEY=
DEEPSEEK_API_KEY=
MOONSHOT_API_KEY=
GOOGLE_API_KEY=
GROQ_API_KEY=
```

### 3. 启动后端

```bash
python backend/start_api.py
```

后端默认监听：

```text
http://localhost:8000
```

### 4. 启动前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听：

```text
http://localhost:5173
```

## 测试与评估

常用测试命令：

```bash
python -m pytest backend/tests -q
```

按模块测试：

```bash
python -m pytest backend/tests/harness -q
python -m pytest backend/tests/domains -q
```

评估相关目录：

```text
backend/harness/evaluation/
backend/tests/fixtures/
backend/eval_results/
```

项目里已有的评估维度包括：

- 压缩保真度：压缩后事实是否保留，是否幻觉。
- 搜索管线质量：去重、过滤、质量评分是否符合预期。
- 来源可追踪：报告或事实是否能映射回合法 source id。
- 可靠性分析：重复 eval 后计算均值、方差、CV、稳定性。

## 面试重点讲法

### 1. 为什么用 LangGraph

这个项目不是一次性 chain，而是有状态工作流：

- 要暂停等待用户反馈。
- 要并行运行多个分析师。
- 要失败后从 checkpoint 恢复。
- 要在多个节点之间传递复杂状态。

LangGraph 的 `StateGraph`、`interrupt_before`、`Send`、checkpointer 和 reducer 正好匹配这些需求。

### 2. Harness 和 Domain 为什么分层

`due_diligence` 只关心尽调流程和提示词；搜索工具、记忆压缩、上下文预算、评估、观测不应该写死在业务图里。

这样做的好处：

- 工具 pipeline 可以复用到别的 Agent 任务。
- Memory 节点可以作为通用图节点工厂复用。
- 业务层提示词变化不会影响底层观测和评估。
- 面试时能讲清楚“什么是业务复杂度，什么是基础设施复杂度”。

### 3. 为什么做记忆压缩

多轮搜索访谈如果直接保留所有消息，会带来三个问题：

- Token 成本快速膨胀。
- LLM 输入中重复内容太多，回答质量下降。
- 事实冲突无法结构化管理。

所以项目采用：

```text
原始消息保留在 checkpoint
每轮新增事实进入 WorkingMemory
旧消息按需进入 RunningSummary
每次调用模型前由 ContextAssembler 重新组装上下文
```

### 4. 如何保证证据可追踪

搜索结果被代码分配 source id，而不是让模型自己编 URL。压缩事实只能引用 registry 中存在的 source id。报告写作时再通过 registry 生成引用说明。

这能减少：

- 模型编造来源。
- 同一个 URL 在不同轮次被重复计算成多个独立来源。
- 报告结论无法回溯到材料。

### 5. 如何处理失败和恢复

系统有两层恢复：

- `TaskRuntime` 保存任务状态和事件，服务重启后将 running 任务标记为 failed，并保留 failed_stage。
- LangGraph checkpointer 保存图执行状态，retry 时优先用原 thread_id 从 checkpoint 继续。

所以失败后不是重新跑完整报告，而是尽量从上一个成功节点恢复。

## 可被追问的问题

可以让 GPT 面试官围绕这些问题提问：

1. 主图和访谈子图分别承担什么职责？
2. 为什么 `human_feedback` 要用 LangGraph interrupt，而不是普通 API 状态？
3. `Send` fan-out 并行后，多个分支如何合并状态？
4. `ResearchGraphState` 中哪些字段用 `operator.add`，哪些字段用 `keep_latest`，为什么？
5. Harness 层为什么不能依赖 server 层？
6. Skill Pack 为什么选择 Markdown，而不是写死在代码里？
7. 搜索 pipeline 中为什么需要 near-duplicate 和 output guard？
8. 为什么 source id 要由代码生成，而不是让 LLM 生成？
9. WorkingMemory、CompressedTurn、RunningSummary 三者有什么区别？
10. ContextAssembler 为什么不能直接修改 checkpoint 中的 messages？
11. 如果一个节点失败，retry 如何避免重复消耗已经完成节点的 token？
12. 如何衡量压缩是否丢失关键信息？
13. 如果要扩展到法律尽调或股票分析，要改哪些层？
14. 当前系统的本地 JSON/SQLite 存储在生产环境有什么风险？
15. 你会如何把这个系统改造成多租户生产服务？

## 已知限制和改进方向

- 当前主要 skill pack 是 `ai`，行业扩展能力已经设计，但还需要更多真实行业包验证。
- `classify_company_type` 目前固定返回 `ai`，还没有真正做行业分类。
- 本地文件型任务状态适合 demo/开发，不适合多实例生产部署。
- 用户认证是简单 Cookie session + SQLite，本质是轻量实现。
- 报告质量依赖外部搜索质量、模型能力和 prompt 稳定性。
- `pyproject.toml` 仍有历史包名残留，`include = ["app*"]` 与当前 `server/harness/domains` 结构不完全一致；开发运行主要依赖 `backend/start_api.py` 切换工作目录。
- 部分历史辅助 README 存在编码损坏，根 README 已按当前代码重新整理。

## 一段适合面试开场的介绍

这个项目是一个企业尽调场景下的多 Agent 报告生成系统。用户提交公司和关注点后，系统会先生成一组专业分析师角色，并通过人工反馈节点让用户确认；之后每个分析师并行执行多轮研究访谈，每轮会生成搜索 query、调用搜索工具、清洗证据、生成专家回答，并把这一轮压缩成结构化工作记忆。最后主图把各分析师 section 汇总成完整报告，做审查和导出。

我在架构上把它拆成了 Harness、Domain、Skill Pack 和 Server/Frontend 四层。Harness 放通用能力，比如工具注册、搜索清洗、记忆压缩、上下文预算、观测和评估；Domain 只放尽调业务图和提示词；Skill Pack 用 Markdown 承载行业知识。这样做的目标是让业务流程清晰，同时让底层 Agent 基础设施可以复用、测试和评估。
