# AgentHarness — 多智能体编排与评测平台

一个基于 `LangGraph + FastAPI` 的**多智能体尽调研究平台**，底层是一套可复用的记忆/工具/评测基础设施（Harness），承载单一但具体的领域（报告生成）。行业广度靠**可插拔技能包**（数据）实现，而不是新增领域（代码）。

项目采用前后端分离架构：后端使用 `FastAPI` 提供 JSON API，前端使用 `React + Vite` 提供浏览器界面。

## 项目定位

```
操作系统  :  应用程序        =  AI Harness  :  尽调研究
插件/配置  :  垂直行业知识    =  技能包      :  AI / 美妆 / 房地产 / ...
```

**Harness 层**是任何研究类工作流都能复用的、与业务无关的基础设施：

1. **Tool Integration** — 标准化的工具注册、调用、结果清洗管线
2. **Memory & Context** — 短期对话压缩、结构化工作记忆、上下文窗口管理、可复用的调研循环节点工厂（`harness/memory/nodes.py`）
3. **Observability** — Trace、Metrics、Events、Cost tracking
4. **Evaluation** — 离线评测（fixture 仿真）、在线监控、可靠性分析

**Domain 层**（`domains/due_diligence/`）承载真正意义上业务特有的东西：LangGraph 工作流形状、prompt、报告结构。**行业差异**（AI / 美妆 / 房地产 …）由第三条、正交的轴处理——**技能包**（`skills/<行业>/*.md`），渲染时作为 Markdown 数据注入通用 prompt 模板，新增一个行业理论上不需要改一行代码。

## 项目亮点

- **Harness / Domain / 技能包三层分离**：通用基础设施（记忆、工具管线、checkpoint、评测）不含业务逻辑；Domain 层承载工作流结构；技能包承载行业知识,作为数据存在
- 基于 `LangGraph` 构建的多智能体尽调工作流
- **SqliteSaver 持久化检查点**：任务失败后从精确失败节点恢复，服务重启不丢进度，零 token 浪费
- 人工参与闭环的分析师审核与重生成（通过 LangGraph 原生 `interrupt_before` + checkpointer 实现：在节点前暂停、注入反馈、恢复执行）
- 并行访谈（Fan-out）+ 并行报告撰写流水线
- **9 级搜索清洗管线**（URL 规范化 → 清洗 → 去重 → 近似去重 → 相关性 → 质量 → 结构化 → 安全守卫 → 格式化）
- **7 大搜索适配器**：Serper、Tavily、Bocha、SEC EDGAR、CNINFO、GitHub、Jina/Direct Reader
- **4.5K 行记忆系统**：增量压缩、SPDV 事实协调、上下文窗口管理、token 预算强制执行
- 中文报告输出：楷体排版、小四字号、两端对齐、CJK 字体 PDF
- 支持导出 `DOCX` 与 `PDF` 双格式报告
- 异步任务运行时：任务状态持久化、事件日志、失败重试、重启恢复
- 多模型提供方支持：`openai`、`google`、`groq`、`deepseek`
- **三层评测框架**：组件级 Scorer（压缩保真度、管线质量）+ 集成一致性校验 + 端到端评分，基于真实 LLM-Judge 跑分并产出可靠性报告（CV/σ）

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
- ✅ **Phase 4 完成**：评测框架 — 3 个 Scorer（压缩保真度/管线质量/来源可追溯）+ 7 条一致性校验规则 + Fixture 驱动的 N 次重复可靠性分析
- ✅ 387 个自动化测试全部通过（`backend/tests/`）
- ✅ 删除了未接入生产的通用 `harness/runtime/` 图模板层和 `harness/human_loop/`——只被一个 mock domain 冒烟测试验证过，从未真正驱动过尽调工作流；把其中真正通用、不含业务语义的部分（compress/update_memory/compact_history/continue-router）抽成了 `harness/memory/nodes.py`，现在被真实的访谈图直接调用
- ✅ 删除了 3 个从未被实例化过的 Pydantic 类型（`SkillRef`/`SourcePolicy`/`DomainMemoryRef`）和一个全仓零调用的死方法
- ✅ 把 `ModelLoader`、`SkillRegistry`、结构化日志、共享异常类型从 `app/` 搬进了 `harness/`——这些本来就是零 HTTP 语义、零尽调业务语义的纯基础设施，之前窝在 web 层只是历史遗留，不是设计选择
- ✅ 彻底修掉了"harness 反向依赖 app"的问题：`harness/observability/{task_runtime,tracer}.py` 不再 import 任何 `app.*`。`TaskRuntime`/`NodeTracer` 现在接受一个可选的 `runtime_dir` 构造参数，默认走 `harness/paths.py` 里自包含的解析逻辑（直接读同名 `RUNTIME_DIR` 环境变量，而不是 import `app.config`）——是真正的依赖注入，不是简单挪个文件位置
- ✅ `app/` 改名为 `server/`——清理完上面那些基础设施之后，这里只剩 FastAPI 路由、数据库、配置，"app"这个名字太模糊（仓库里还有 `frontend/`，那个某种意义上也算"app"）
- ✅ 改名过程中顺手发现并修复了一个真实存在、此前一直静默失败的 bug:`domains/due_diligence/graph.py` 构造 `SkillRegistry` 时用的路径深度算错了一层(`parents[3]` 应为 `parents[2]`，是 Phase 1 重构多加了一层目录之后没同步改索引留下的)。`SkillRegistry.load_skill_pack()` 在目录不存在时会静默返回空列表而不报错，所以这个 bug 导致**每次真实运行时 `ai` 技能包的 Markdown 内容大概率从未真正传到过 LLM,系统一直在用兜底的通用 domain memory**。已验证修复:`load_skill_pack("ai")` 现在能正确加载 3 个角色技能 + 3 条领域记忆
- ✅ 全仓死函数扫描(基于 AST,交叉核对了所有源文件和测试文件):删掉了约 15 个全仓零调用的函数/方法,大多是被遗弃的"异步双胞胎"方法(`acompress_completed_turn`、`acompact_history`、`acompute_new_summary`、`_agenerate_summary`),外加 `harness/evaluation/runner.py` 里整个没人用的 `EvalRunner` 类——真正在跑的评测脚本 `run_real_evals.py` 一直是直接调用 scorer,从没走过这个类(保留了确实在用的 `EvalRunResult`)。排除在扫描结果外的:FastAPI 路由处理函数和框架回调方法(`@app.on_event`、`HTMLParser.handle_*`),这些只是"看起来没人调用",实际是框架按约定调用,不是按名字调用
- ✅ 把 `harness/models/state.py`(一个只有 10 行的函数)并进了 `harness/models/__init__.py`,把 `harness/logger/` 和 `harness/paths.py` 合并进了 `harness/observability/`(它们真正的、唯一的消费者)——刻意没有建一个 `harness/utils/` 大杂烩目录,因为那样等于重新制造了一次 `app` 当初那种"名字模糊、什么都往里塞"的问题;`exceptions.py`、`llm_loader.py`、`skill_registry.py` 继续留在 harness 顶层,因为每一个都对应一个说得清楚的能力,不是杂物
- ✅ 修复了一个让相关性打分全程空转的接线 bug:`InterviewState` 和基于 `Send` 的 fan-out payload 从未把 `company_name`/`focus` 传到 `ToolContext`,导致 `RelevanceScoreStage` 每次都走"无目标"提前退出分支(固定 0.5 分,不做任何过滤)。逐行走查清洗管线时还发现了几处 CJK 盲区:关键词密度打分、日期抽取、内容指纹全都用空格分词,会把一整段中文文本压成一个"单词"——已改成 CJK 感知(按字符分词)、补充了中文日期格式、并给一直是空列表的垃圾域名黑名单加了种子数据
- ✅ 把 `evidence_quality` 从"模型自报"改成了机械派生——由独立来源 ID 的数量决定(2+ → high,1 → medium,0 → low),压缩器和事实协调器共用同一个辅助函数。同时修复了跨轮次的来源 ID 去重:同一个 URL 在后续轮次重新出现时,现在会复用已有的 `source_id` 而不是重新分配一个,此前这会人为拉高独立来源计数,让同一个来源伪装成多来源互证
- ✅ 删除了从未被真正使用过的工具调用(tool-calling)相关脚手架(`ToolContextPruner`,以及 `HistoryCompactor`/`RunningSummaryManager` 里的 tool-call 边界补全逻辑)——本仓库目前没有任何 domain 采用 LangChain 式的工具调用(搜索就是普通函数调用),这套机制从未在生产环境中被真正触发过;在对应位置留了注释,说明未来如果接入 ReAct 式工具调用 agent 需要把这层保护补回来。同一轮走查中顺手修了一个真实 bug:`HistoryCompactor` 的轮次边界切分在切分索引为奇数时,可能把一个问题和它的答案拆开
- ✅ 删除了未被使用的模型驱动事实协调路径(`FactReconciler.reconcile_from_model_output`)——`WorkingMemory` 实际只调用代码驱动的 SPDV `reconcile()`。让 SPDV 事实匹配具备 CJK 感知能力:旧的按词切分分词器会把一整句中文压成一个 token,导致近似重复的中文事实永远无法通过 Jaccard 兜底匹配上
- ✅ 修复了访谈 fan-out 合并时的 `InvalidUpdateError`——给 `ResearchGraphState` 里所有标量字段都加上了 `keep_latest` reducer:并行访谈分支写回共享 state 时,每个字段都需要显式的合并策略,不能只给 list 类型字段配置
- ✅ 修复了访谈过早终止的问题:`ask_question` 之前看到的是完整的累积事实列表,而不是仅剩的知识缺口,导致模型容易过早判断"调研已完成"
- ✅ 历史压缩现在会把自己那次摘要 LLM 调用的 token 开销以 `llm_metrics` 形式上报(此前这部分开销对 token 统计完全不可见);上下文组装也不再重复计费:已经被折叠进 running summary 的消息会从近期原始消息中排除,前面轮次已经引用过的来源也不会在后续轮次被再次整段嵌入
- ✅ 新增 A/B 压缩对比脚本(`backend/scripts/run_compression_comparison.py`),对同一个任务分别跑"完全启用压缩" vs "完全关闭压缩"——关闭组现在连 `compact_history` 一起跳过,而不是只 no-op 掉逐轮的 `compress` 节点,这样"无压缩"基线才是真正干净的,不会混入截断裁剪带来的收益——以此衡量压缩对 token/成本的真实影响
- 📋 Phase 5 下一步：补一个真正有内容的第二个技能包（如 `beauty/`），端到端验证"行业靠数据扩展、不靠代码"这条主张；CI 集成

### 真实评测结果（deepseek-chat，重复 3 次）

| 指标 | 结果 |
|---|---|
| 压缩事实保留率 | 86%（CV 0.091，稳定） |
| 压缩幻觉率 | 0% |
| 管线去重召回率 / 精确率 | 100% / 75% |
| 来源可追溯性（纯正则） | 3/3 个 Fixture 用例判定正确 |

完整数据见 `backend/eval_results/`（原始运行记录 + `reliability_report.md`）。

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
5. **可插拔行业技能包**
   - Markdown 驱动的技能包系统（角色技能 + 研究技能 + 搜索策略 + 领域记忆），渲染时注入通用 prompt 模板
   - 目前只有 `ai` 真正有内容；`manufacturing`/`beauty`/`fmcg`/`internet` 是规划中的方向，尚未创建

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
│   ├── harness/                        # 🆕 Harness 核心平台层（不含业务逻辑）
│   │   ├── tools/                      # Tool Integration（工具注册、调用管线、搜索适配器）
│   │   ├── memory/                     # Memory & Context（压缩器、工作记忆、通用节点工厂 nodes.py）
│   │   ├── observability/              # 可观测性（任务运行时、trace、metrics、logger.py、paths.py）
│   │   ├── evaluation/                 # 评测框架（Scorer、Fixture、可靠性分析；EvalResult 保留,EvalRunner 已删）
│   │   ├── models/                     # 通用数据模型（Agent、State、Task、Event）
│   │   ├── exceptions.py               # ResearchAnalystException——共享异常包装
│   │   ├── skill_registry.py           # 加载 skills/<行业>/*.md 技能包
│   │   └── llm_loader.py               # 多 provider LLM 加载（openai/google/groq/deepseek）
│   ├── domains/                        # 🆕 领域应用层（工作流结构）
│   │   └── due_diligence/              # 尽调领域——目前唯一的领域
│   │       ├── graph.py                # 领域主图
│   │       ├── interview.py            # 访谈子图
│   │       ├── schemas.py              # 领域专用 State 定义
│   │       └── prompts/                # 领域 Prompt 模板（渲染时注入 skill_card）
│   ├── skills/                         # 行业技能包（Markdown 驱动;目前只有 ai/ 有内容）
│   │   └── ai/
│   ├── server/                         # Web 服务层——只做 HTTP 交付（原名 app/）
│   │   ├── api/                        # FastAPI 路由 + Service
│   │   ├── database/                   # 用户认证数据库
│   │   └── config.py                   # 环境变量配置（CORS、runtime 目录……）
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
│              技能包层（数据 — 行业知识）                          │
│      skills/ai/*.md   │   skills/beauty/*.md（规划中）          │
├──────────────────────────────────────────────────────────────┤
│              Domain 层（代码 — 工作流结构）                       │
│    domains/due_diligence/（图结构、prompt、报告规格）             │
├──────────────────────────────────────────────────────────────┤
│              Harness 核心层（不含业务逻辑）                        │
│  ┌─────────────┐ ┌──────────────┐ ┌───────────────────────┐  │
│  │ Tool         │ │ Memory &     │ │ Observability          │  │
│  │ Integration  │ │ Context      │ │                       │  │
│  └─────────────┘ └──────────────┘ └───────────────────────┘  │
│  ┌───────────────────────────────────────────────────────┐   │
│  │ Evaluation Framework                                    │   │
│  └───────────────────────────────────────────────────────┘   │
├──────────────────────────────────────────────────────────────┤
│                 基础设施层 (Infrastructure)                     │
│   Model Loader  │  DB (SQLite)  │  File Storage  │  HTTP     │
└──────────────────────────────────────────────────────────────┘
```

图编排本身（图的搭建、基于 `Send` 的 fan-out、`interrupt_before` + checkpointer 实现的人工审核）直接用 LangGraph 原生能力写在 domain 层，没有单独一层"通用 runtime 模板"。早期确实做过一次尝试（`harness/runtime/`，一个参数化的 `AgentGraphTemplate`），但在确认这个项目真正的扩展轴是"同一个领域下换技能包"而不是"多个结构迥异的领域"之后，这套代码因为没有第二个真实消费者而被删除，而不是留着当摆设。

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
- **Domain 层** (`backend/domains/`)：领域业务逻辑（图结构 + prompt + 报告规格）。新增行业走技能包，不改这一层；新增结构不同的领域（股评、法审）才需要手写新的 `graph.py`
- **Server 层** (`backend/server/`)：只做 HTTP 交付（API 路由、Service 编排、数据库）；跨切面基础设施已经不在这里了，全部在 `harness/`
- **Skills** (`backend/skills/`)：Markdown 技能包，纯数据驱动，不含代码

### 新增一个行业（技能包——常见情况）

1. 在 `backend/skills/<行业>/` 下创建新目录
2. 参照 `skills/ai/*.md` 编写角色技能 Markdown 文件
3. 不需要改 domain 代码——`skill_card.body` 会被直接注入 `domains/due_diligence/prompts/interview.py` 里已有的 prompt 模板

### 新增一个领域（结构完全不同的工作流——少见情况）

目前没有辅助脚手架(早期做过一版通用 `AgentGraphTemplate`,因为没有第二个真实消费者已被删除,见"后续规划")。真正要新增一个结构不同的领域(比如辩论结构的法审,而不是尽调式报告),现在需要参照 `domains/due_diligence/graph.py` 手写一个新的 `StateGraph`,放在 `backend/domains/<name>/` 下,并复用 `harness/` 里已经领域无关的部分(工具、记忆、可观测性、评测)。

### 改动注意事项

- 状态字段优先集中在 domain 层的 `schemas.py` 中维护，通用类型放在 `harness/models/`
- 所有任务状态变更统一通过 `harness/observability/task_runtime.py` 处理
- Prompt 模板放在对应 domain 的 `prompts/` 目录，不要放在 harness 层

## 局限性

- 当前存储方案偏本地开发和演示场景
- 用户认证依赖本地 SQLite，而不是生产级身份系统
- 任务状态和事件采用文件持久化，而不是独立的任务队列或数据库后端
- 报告质量高度依赖模型选择、提示词质量和外部搜索结果
- 目前只有 `ai` 一个技能包真正有内容;行业广度(美妆、房地产……)在架构上是支持的(skill_card 内容会注入通用 prompt 模板),但还没有用第二个技能包验证过
- 报告的宏观结构和搜索 source_type 路由表目前仍然直接写死在 domain 的 prompt 文本里,不是技能包配置——真正差异很大的行业(比如房地产尽调关心的是不动产登记而不是 SEC 文件)现在还需要改 domain 层的 prompt 代码,不能只加一个技能包解决

## 后续规划

| Phase | 内容 | 状态 |
|:---:|------|:---:|
| 1 | **基础重构**：Harness 层与 Domain 层分离 | ✅ 完成 |
| 2 | **工具集成与数据清洗**：9 级搜索清洗管线 + 多后端适配器 | ✅ 完成 |
| 3 | **记忆管理与上下文压缩**：增量压缩 + 事实协调 + 上下文组装 | ✅ 完成 |
| 4 | **评测框架**：Fixture 仿真 + 组件/集成/端到端 Scorer + 可靠性分析 | ✅ 完成 |
| 5 | **补充打磨**：补第二个领域验证可插拔性 + CI 集成 + 前端评测可视化 | 📋 计划中 |

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
