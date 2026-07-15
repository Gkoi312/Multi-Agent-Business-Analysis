# Multi-Agent-Business-Analysis → AI Harness 改造计划书

> 版本：v1.0
> 日期：2026-07-10
> 目标：将当前「多智能体尽调应用」重构为「可插拔领域技能的多智能体编排与评测平台（AI Harness）」

> **执行进度备注（2026-07-15）**：本文档是最初的规划文档，正文内容保持不变。Phase 1-4（基础重构、工具集成、记忆管理、评测框架）均已按计划实施完成，428 个自动化测试通过，评测框架已产出真实 LLM 跑分结果。最新状态以 [README.md](../README.md) / [README-zh.md](../README-zh.md) 的 Current Status 章节为准，本文档仅作为历史设计记录保留。

---

## 1. 项目现状诊断

### 1.1 已有的资产

| 模块 | 文件 | 当前状态 | 可复用性 |
|------|------|---------|---------|
| Agent 编排 | `report_generator_workflow.py` | LangGraph StateGraph，含 fan-out、interrupt、conditional routing | ⭐⭐⭐⭐ 但和尽调业务耦合 |
| 子图模板 | `interview_workflow.py` | 提问→搜索→回答→写 section 循环 | ⭐⭐⭐⭐ 但 prompt 和工具写死 |
| 任务运行时 | `task_runtime.py` | 异步执行 + 状态持久化 + 事件流 + 重启恢复 | ⭐⭐⭐⭐⭐ 几乎可直接复用 |
| Skill 系统 | `skill_registry.py` + YAML packs | 可插拔行业技能包，含角色/研究/搜索策略映射 | ⭐⭐⭐⭐ 架构好但内容薄 |
| 数据模型 | `schemas/models.py` | Pydantic + TypedDict，含并行合并 reducer | ⭐⭐⭐⭐ 但混在一起没分层 |
| Prompt 管理 | `prompt_lib/prompt_locator.py` | Jinja2 模板化，含引用编排规则 | ⭐⭐⭐ 但全是尽调专用 |
| 前端 | React SPA | Dashboard + TaskDetail + 人工反馈 | ⭐⭐⭐ 功能完整但无通用组件 |
| 模型加载 | `utils/model_loader.py` | 多 provider 支持（OpenAI/Google/Groq） | ⭐⭐⭐⭐ 但无模型路由/fallback |
| 异常处理 | `exception/custom_exception.py` | 统一异常包装 + 上下文 | ⭐⭐⭐⭐ 可直接复用 |
| 可观测性 | `task_runtime.py` + `logger.py` | structlog + 事件流 | ⭐⭐⭐ 需要加强 trace 能力 |

### 1.2 核心缺口

| 缺口 | 严重度 | 说明 |
|------|:---:|------|
| **记忆管理** | 🔴 | 无上下文压缩、无结构化工作记忆、无长期记忆。`max_num_turns` 被硬编码为 1 就是直接后果 |
| **工具集成管线** | 🔴 | 搜索结果直接喂 LLM，无清洗/去重/结构化。工具和数据之间没有标准接口 |
| **评测框架** | 🔴 | 零 eval，无法度量报告质量、无法证明优化效果 |
| **领域与引擎分离** | 🟡 | workflow 代码和尽调业务逻辑耦合，换个场景就要重写 |
| **模型路由** | 🟡 | 所有节点用同一个模型，无便宜模型做简单任务、无模型 fallback |
| **MCP 协议** | 🟡 | 工具调用无标准协议，新增工具要改代码 |
| **测试** | 🔴 | 零自动测试 |

### 1.3 一句话判断

> 这是一个「AI 应用」，不是「AI 平台」。如果能跑通一次尽调就够了，那它完成了任务。但如果你要在简历上写「我搭建了一套 Agent 基础设施」，那它缺少通用性、可度量性和可扩展性。

---

## 2. Harness 的定义与目标

### 2.1 什么是 AI Harness

AI Harness 是 **模型和真实世界之间的基础设施层**。它不是某个具体的 Agent 应用，而是所有 Agent 应用共享的运行平台。类比：

```
操作系统  :  应用程序  =  AI Harness  :  尽调/股评/法审 Agent
```

一个合格的 AI Harness 至少包含以下能力：

1. **Agent Runtime** — 编排引擎，管理状态、并行、中断、恢复
2. **Tool Integration** — 标准化的工具注册、调用、结果清洗管线
3. **Memory & Context** — 短期对话压缩、结构化工作记忆、长期向量记忆
4. **Human-in-the-Loop** — 审批流、反馈注入、中断/继续
5. **Observability** — Trace、Metrics、Events、Cost tracking
6. **Evaluation** — 离线评测（fixture 仿真）、在线监控、可靠性分析
7. **Model Management** — 多 provider 路由、负载分配、fallback

### 2.2 本次改造的目标

**不在这次做：**
- 多租户 SaaS 化（那是产品化的事）
- 分布式任务队列（Redis/Celery）
- 企业级认证鉴权

**这次聚焦三个方向：**

```
方向一：领域与引擎分离  →  让 Harness 本身成为可复用的平台层
方向二：记忆管理与上下文压缩  →  让 Agent 能做多轮深度研究而不爆炸
方向三：工具集成与数据清洗  →  让搜索/浏览等工具有标准的输入输出管线
```

---

## 3. 目标架构

### 3.1 分层架构图

```
┌──────────────────────────────────────────────────────────────┐
│                    应用层 (Domain Apps)                        │
│   business-due-diligence  │  stock-analysis  │  legal-review  │
│   (每个应用 = skill_pack.yaml + prompts/ + fixtures/)          │
├──────────────────────────────────────────────────────────────┤
│                  Harness 核心层 (Agent Harness Core)           │
│                                                              │
│  ┌─────────────┐ ┌──────────────┐ ┌───────────────────────┐  │
│  │ Agent        │ │ Tool         │ │ Memory & Context      │  │
│  │ Runtime      │ │ Integration  │ │ Manager               │  │
│  │              │ │              │ │                       │  │
│  │ - 图编排      │ │ - 工具注册    │ │ - 对话压缩             │  │
│  │ - 状态管理    │ │ - 调用管线    │ │ - 结构化工作记忆        │  │
│  │ - Fan-out    │ │ - 数据清洗    │ │ - 覆盖率追踪           │  │
│  │ - Interrupt  │ │ - 结果归一    │ │ - Token 管理           │  │
│  └─────────────┘ └──────────────┘ └───────────────────────┘  │
│                                                              │
│  ┌─────────────┐ ┌──────────────┐ ┌───────────────────────┐  │
│  │ Human-in-    │ │ Observability│ │ Evaluation             │  │
│  │ the-Loop     │ │              │ │ Framework             │  │
│  │              │ │ - Task 状态机  │ │                       │  │
│  │ - 审批门      │ │ - Event 流   │ │ - Fixture 管理         │  │
│  │ - 反馈注入    │ │ - Trace 追踪 │ │ - Scorer 注册          │  │
│  │ - 版本追踪    │ │ - Cost 计量  │ │ - 可靠性分析            │  │
│  └─────────────┘ └──────────────┘ └───────────────────────┘  │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│                 基础设施层 (Infrastructure)                     │
│   Model Loader  │  DB (SQLite→PG)  │  File Storage  │  HTTP  │
└──────────────────────────────────────────────────────────────┘
```

### 3.2 新的目录结构

```text
backend/
├── harness/                         # ← NEW: Harness 核心平台层
│   ├── __init__.py
│   ├── runtime/                     # Agent Runtime
│   │   ├── __init__.py
│   │   ├── graph_builder.py         # 通用图构建器
│   │   ├── fanout.py                # 并行 fan-out 协调
│   │   ├── state.py                 # 通用状态定义与 reducer
│   │   └── checkpoint.py           # Checkpoint 管理
│   │
│   ├── tools/                       # Tool Integration
│   │   ├── __init__.py
│   │   ├── registry.py             # 工具注册中心
│   │   ├── pipeline.py             # 工具调用管线（清洗/去重/结构化）
│   │   ├── search/                 # 搜索工具适配
│   │   │   ├── __init__.py
│   │   │   ├── tavily_adapter.py   # Tavily → 标准接口
│   │   │   └── cleaner.py          # 搜索结果清洗管线
│   │   └── browser/                # 浏览器工具（预留）
│   │
│   ├── memory/                      # Memory & Context
│   │   ├── __init__.py
│   │   ├── compressor.py           # 对话增量压缩器
│   │   ├── working_memory.py       # 结构化工作记忆
│   │   ├── context_window.py       # Token 计数与窗口管理
│   │   └── long_term.py            # 长期记忆（向量存储，预留）
│   │
│   ├── human_loop/                  # Human-in-the-Loop
│   │   ├── __init__.py
│   │   ├── gate.py                 # 审批门节点
│   │   └── feedback.py             # 反馈注入与追踪
│   │
│   ├── observability/              # 可观测性
│   │   ├── __init__.py
│   │   ├── task_runtime.py         # ← 从 app/api/services/ 迁入并通用化
│   │   ├── tracer.py              # 节点级 trace 记录
│   │   └── metrics.py             # Token/Cost 计量
│   │
│   ├── evaluation/                  # 评测框架
│   │   ├── __init__.py
│   │   ├── runner.py              # 评测 Runner
│   │   ├── scorer.py              # Scorer 基类与注册
│   │   ├── fixtures.py            # Fixture 管理
│   │   └── reliability.py         # 可靠性分析（CV/σ/通过率）
│   │
│   └── models/                     # Harness 通用数据模型
│       ├── __init__.py
│       ├── agent.py               # Agent/Analyst 基类
│       ├── state.py               # 通用 State 定义
│       ├── task.py                # Task 模型
│       └── events.py              # Event 模型
│
├── domains/                        # ← NEW: 领域应用层（可插拔）
│   ├── __init__.py
│   ├── base.py                     # 领域适配基类
│   ├── due_diligence/              # 尽调领域
│   │   ├── __init__.py
│   │   ├── config.py              # 领域配置（max_analysts, sections...）
│   │   ├── prompts/               # 领域 prompt 模板
│   │   │   ├── analysts.py
│   │   │   ├── interview.py
│   │   │   ├── report.py
│   │   │   └── review.py
│   │   ├── graph.py               # 领域主图（组装 harness 组件）
│   │   └── schemas.py             # 领域专用数据模型
│   └── stock_analysis/             # 股评领域（预留）
│
├── skills/                         # Skill Packs（已有，扩展）
│   ├── ai/
│   ├── internet/
│   ├── manufacturing/
│   └── ...
│
├── app/                            # 保留：Web 应用层
│   ├── api/                        # FastAPI 路由（已有，微调）
│   └── ...
│
└── tests/                          # ← NEW: 测试
    ├── harness/
    │   ├── test_memory/
    │   ├── test_tools/
    │   └── test_runtime/
    ├── domains/
    │   └── test_due_diligence/
    └── fixtures/                    # Eval fixtures
        ├── case_001_apple.json
        ├── case_002_tesla.json
        └── ...
```

---

## 4. 分层改造方案

### 4.1 Agent Runtime 层

**目标：** 让图编排逻辑不再感知尽调业务，成为可配置的模板。

**当前问题（以 `report_generator_workflow.py` 为例）：**

```python
# 当前：节点名称和业务耦合
builder.add_node("create_analyst", self.create_analyst)
builder.add_node("human_feedback", self.human_feedback)
builder.add_node("write_report", self.write_report)
# "create_analyst" / "write_report" 是尽调概念，不是通用概念
```

**改造后：**

```python
# harness/runtime/graph_builder.py

class AgentGraphTemplate:
    """通用的 Agent 编排图模板。
    
    提供三种标准图模式：
    - plan_execute: 规划 → 人工审核 → 并行执行 → 汇总
    - debate: 多方辩论 → 裁判裁决
    - research: 递归研究 → 产出
    """
    
    def __init__(self, mode: str, domain: "DomainAdapter"):
        self.mode = mode
        self.domain = domain  # 领域适配器注入，不写死
    
    def build(self) -> StateGraph:
        if self.mode == "plan_execute":
            return self._build_plan_execute()
        elif self.mode == "debate":
            return self._build_debate()
        # ...
    
    def _build_plan_execute(self) -> StateGraph:
        """规划→审核→执行→汇总 模板"""
        builder = StateGraph(self.domain.state_schema)
        
        # 节点由 domain 提供具体实现，harness 只定义图结构
        builder.add_node("plan", self.domain.plan_node)
        builder.add_node("review_gate", HumanReviewGate())  # 通用人工审核节点
        builder.add_node("execute", self.domain.execute_node)
        builder.add_node("assemble", self.domain.assemble_node)
        
        builder.add_edge(START, "plan")
        builder.add_edge("plan", "review_gate")
        builder.add_conditional_edges("review_gate", self._review_router, ...)
        # ...
        return builder.compile(
            interrupt_before=["review_gate"],
            checkpointer=self.checkpoint,
        )
```

**改动范围：**
- 新建 `harness/runtime/`，提取通用图模式
- 将 `report_generator_workflow.py` 中的节点实现迁到 `domains/due_diligence/graph.py`
- `report_routes.py` 不再直接调用 `ReportService` → 改为通过 domain adapter

---

### 4.2 工具集成与数据清洗层（Tool Integration）

**目标：** 每个工具调用都经过标准管线：`去重 → 清洗 → 相关性过滤 → 结构化提取 → 格式化`。

**当前问题（`interview_workflow.py:_search_web`）：**

```python
# 当前：搜索结果直接格式化塞给 LLM
search_docs = self.tavily_search.invoke(search_query.search_query)
# ...直接 format 成 <Document> 标签
formatted_docs.append(f'<Document href="{href}"/>\n{content}\n</Document>')
```

搜索返回的原始内容含：重复结果、SEO 垃圾、无关页面、HTML 残留。LLM 要在噪音中找信号。

**改造后：**

```python
# harness/tools/pipeline.py

class ToolPipeline:
    """工具输出处理管线。
    
    每个工具注册时绑定一条管线。管线由多个 Stage 组成，
    数据依次通过每个 Stage，最终输出清洗后的结构化结果。
    """
    
    def __init__(self, stages: list["ProcessingStage"]):
        self.stages = stages
    
    def process(self, raw_output: Any, ctx: "ToolContext") -> "CleanedOutput":
        data = raw_output
        trace = []
        for stage in self.stages:
            before = len(str(data))
            data = stage(data, ctx)
            after = len(str(data))
            trace.append({
                "stage": stage.name,
                "before_bytes": before,
                "after_bytes": after,
                "reduction_pct": round((1 - after/before)*100, 1) if before else 0,
            })
        return CleanedOutput(data=data, trace=trace)


# harness/tools/search/cleaner.py

class DeduplicateStage(ProcessingStage):
    """去重：URL 精确匹配 + 标题 Jaccard 相似度"""
    name = "dedup"
    def __call__(self, docs, ctx):
        seen_urls = set()
        unique = []
        for doc in docs:
            url = doc.get("url", "")
            if url and url in seen_urls:
                continue
            # 标题相似度检查（>0.85 视为重复）
            title = doc.get("title", "")
            if any(self._similarity(title, u.get("title","")) > 0.85 for u in unique):
                # 保留更长的
                existing = next(u for u in unique if self._similarity(title, u.get("title","")) > 0.85)
                if len(doc.get("content","")) > len(existing.get("content","")):
                    unique.remove(existing)
                    unique.append(doc)
                continue
            seen_urls.add(url)
            unique.append(doc)
        return unique

class RelevanceFilterStage(ProcessingStage):
    """相关性过滤：关键词初筛 + LLM 精判"""
    name = "relevance"
    def __call__(self, docs, ctx):
        target = ctx.target_entity  # 目标公司名
        # 第一轮：关键词过滤（>70% 的噪声在这里被剔除）
        keyword_pass = [
            d for d in docs
            if target.lower() in (d.get("title","") + d.get("content","")).lower()
            or self._keyword_density(d.get("content",""), target) > 0.01
        ]
        # 第二轮：如果结果还很多，用 cheap LLM 做 binary 判断
        if len(keyword_pass) > 10:
            keyword_pass = self._llm_filter(keyword_pass, target, ctx.cheap_llm)
        return keyword_pass

class StructureFactsStage(ProcessingStage):
    """结构化提取：从每篇文档提取关键事实"""
    name = "structure"
    def __call__(self, docs, ctx):
        for doc in docs:
            content = doc.get("content", "")
            doc["structured"] = {
                "numbers": self._extract_numbers(content),
                "dates": self._extract_dates(content),
                "entities": self._extract_entities(content),
                "sentiment": self._classify_sentiment(content),
                "summary_2sent": self._summarize(content, ctx.cheap_llm),
            }
        return docs


# 预设管线
SEARCH_PIPELINE = ToolPipeline([
    DeduplicateStage(),
    CleanTextStage(min_length=100),
    RelevanceFilterStage(threshold=0.6),
    StructureFactsStage(),
    FormatDocumentStage(),
])
```

**集成方式（改动 `interview_workflow.py`:**）

```python
# 改造前：
search_docs = self.tavily_search.invoke(query)
# → 直接进 LLM

# 改造后：
from harness.tools.pipeline import SEARCH_PIPELINE
from harness.tools.search.tavily_adapter import TavilyAdapter

tool = TavilyAdapter(api_key=...)
raw_results = tool.search(query)
cleaned = SEARCH_PIPELINE.process(raw_results, ctx=ToolContext(
    target_entity=state["company_name"],
    cheap_llm=self.cheap_llm,
))
# → 清洗后的结构化数据进 LLM
```

**改动范围：**
- 新建 `harness/tools/`
- `interview_workflow.py` 的 `_search_web` 方法改为走管线
- 前端不做改动

---

### 4.3 记忆管理与上下文压缩层（Memory & Context）⭐ 核心新增

**目标：** 让 Agent 能进行多轮研究而不因上下文窗口爆炸而截断。

**当前问题：**

看 `interview_workflow.py:_should_continue`:

```python
def _should_continue(state):
    max_turns = int(state.get("max_num_turns", 1) or 1)  # 默认 1！
    turn_count = int(state.get("turn_count", 0) or 0)
    return "ask_question" if turn_count < max_turns else "save_interview"
```

`max_num_turns` 被 `report_service.py` 硬编码为 1。**这不是设计选择，是因为没有压缩的前提下，第二轮开始上下文就会爆炸。** Token 增长曲线：

```
Turn 1: 系统prompt(~500) + Q1(~200) + search_results(~3000) + A1(~800)  = ~4500 tokens
Turn 2: 以上全部 + Q2(~200) + search_results(~3000) + A2(~800)           = ~8500 tokens
Turn 3: 以上全部 + Q3(~200) + search_results(~3000) + A3(~800)           = ~12500 tokens
                                                                           ↑ 直接爆掉
```

有了压缩后：

```
Turn 1: 系统prompt(~500) + Q1 + search_results(~3000) + A1            = ~4500 tokens
Turn 2: compressed_turn1(~600) + Q2 + search_results(~3000) + A2      = ~4300 tokens
Turn 3: compressed_turns1-2(~900) + Q3 + search_results(~3000) + A3   = ~4700 tokens
                                                                        ↑ 稳定不增长
```

#### 4.3.1 压缩策略设计

采用 **逐轮增量式结构化压缩**，不是简单截断：

```python
# harness/memory/compressor.py

class IncrementalCompressor:
    """逐轮增量压缩器。
    
    核心思路：每轮结束后，把上一轮的完整 Q&A 压缩为结构化摘要。
    永远保留「最新 1 轮完整内容」+「旧轮的结构化摘要」。
    """
    
    def __init__(self, llm):  # 用 cheap model
        self.llm = llm
    
    def compress_turn(self, turn: InterviewTurn) -> CompressedTurn:
        """将一轮访谈压缩为结构化摘要"""
        prompt = f"""
将以下分析师-专家的问答压缩为结构化摘要。只提取事实，不保留对话形式。

分析师问题：{turn.question}
搜索结果摘要：{turn.search_summary}
专家回答：{turn.answer}

提取为 JSON：
{{
    "question_intent": "分析师真正想搞清楚的问题（一句话）",
    "key_findings": ["具体事实1", "具体事实2", ...],  // 最多5条
    "numbers_mentioned": [{{"value": ..., "unit": ..., "context": "..."}}],
    "evidence_quality": "high|medium|low",
    "sources_cited": ["url1", "url2"],
    "unanswered": "这轮没搞清楚的问题"
}}
"""
        result = self.llm.invoke(prompt)
        return CompressedTurn(**parse_json(result.content))
    
    def merge_compressed(self, old: list[CompressedTurn]) -> MergedMemory:
        """合并多轮压缩结果为累积记忆，用代码（非 LLM）做统计"""
        all_facts = []
        all_risks = []
        coverage = {"business_model": 0, "growth": 0, "risk": 0, "competition": 0}
        
        for turn in old:
            all_facts.extend(turn.key_findings)
            # 简单规则分类，不需要 LLM
            for fact in turn.key_findings:
                lowered = fact.lower()
                if any(w in lowered for w in ["revenue", "profit", "margin", "pricing"]):
                    coverage["business_model"] += 1
                elif any(w in lowered for w in ["growth", "scale", "expand", "market share"]):
                    coverage["growth"] += 1
                elif any(w in lowered for w in ["risk", "regulation", "compliance", "threat"]):
                    coverage["risk"] += 1
                elif any(w in lowered for w in ["compet", "rival", "moat", "market"]):
                    coverage["competition"] += 1
        
        return MergedMemory(
            total_facts=len(all_facts),
            coverage=coverage,
            knowledge_gaps=self._detect_gaps(coverage),
            risk_flags=[f for f in all_facts if "risk" in f.lower() or "threat" in f.lower()],
        )
```

#### 4.3.2 上下文窗口管理器

```python
# harness/memory/context_window.py

class ContextWindowManager:
    """上下文窗口管理器。
    
    职责：
    1. 精确估算当前上下文 token 数（适配不同模型的 tokenizer）
    2. 在 token 预算紧张时自动触发压缩
    3. 确保 system prompt + 工作记忆 + 最新 N 轮 的总 token 不超限
    """
    
    def __init__(self, model_name: str, max_tokens: int = None):
        self.model_name = model_name
        # 不同模型的上下文窗口大小
        self.limits = {
            "gpt-4o": 128000,
            "gpt-4o-mini": 128000,
            "deepseek-v3": 65536,
            "gemini-2.0-flash": 1048576,
        }
        self.max_tokens = max_tokens or self.limits.get(model_name, 8192)
        # 保守预留：system prompt + 输出 buffer
        self.reserved = 2000
    
    def estimate_tokens(self, text: str) -> int:
        """用 tiktoken 或字符/4 估算"""
        try:
            import tiktoken
            enc = tiktoken.encoding_for_model(self.model_name)
            return len(enc.encode(text))
        except Exception:
            return len(text) // 4  # 保守估算
    
    def should_compress(self, messages: list, working_memory: dict, 
                        system_prompt: str) -> bool:
        """当前上下文是否超过安全线"""
        total = sum(self.estimate_tokens(str(m)) for m in messages)
        total += self.estimate_tokens(str(working_memory))
        total += self.estimate_tokens(system_prompt)
        safe_limit = self.max_tokens - self.reserved
        return total > safe_limit * 0.7  # 70% 就触发
    
    def build_context(self, system_prompt: str, working_memory: MergedMemory,
                      recent_messages: list, compressed_turns: list[CompressedTurn]
                      ) -> list:
        """组装发送给 LLM 的最终上下文"""
        parts = [
            SystemMessage(content=system_prompt),
            SystemMessage(content=f"## 目前研究进度\n{working_memory.format()}"),
        ]
        if compressed_turns:
            parts.append(SystemMessage(
                content=f"## 前几轮研究摘要\n{self._format_compressed(compressed_turns)}"
            ))
        parts.extend(recent_messages)
        return parts
```

#### 4.3.3 结构化工作记忆

```python
# harness/memory/working_memory.py

@dataclass
class WorkingMemory:
    """Agent 的结构化工作记忆。
    
    这是 Agent 在研究过程中不断更新的「认知状态」。
    它与对话消息不同——对话是时序的，工作记忆是结构化的。
    """
    
    # 已收集的事实，按主题归类
    facts: dict[str, list[str]] = field(default_factory=lambda: {
        "business_model": [],
        "growth": [],
        "risk": [],
        "competition": [],
        "financials": [],
    })
    
    # 每条事实的来源
    fact_sources: dict[str, str] = field(default_factory=dict)
    
    # 覆盖缺口：哪些方面还没有足够数据
    knowledge_gaps: list[str] = field(default_factory=list)
    
    # 风险信号聚合
    risk_flags: list[dict] = field(default_factory=list)
    
    # 访谈进度
    turns_completed: int = 0
    total_facts_collected: int = 0
    
    def add_fact(self, category: str, fact: str, source: str):
        self.facts.setdefault(category, []).append(fact)
        self.fact_sources[fact] = source
        self.total_facts_collected += 1
    
    def update_gaps(self):
        """根据已收集事实自动推断缺口"""
        thresholds = {"business_model": 3, "growth": 3, "risk": 3, "competition": 2}
        self.knowledge_gaps = [
            cat for cat, threshold in thresholds.items()
            if len(self.facts.get(cat, [])) < threshold
        ]
    
    def format(self) -> str:
        """格式化为 LLM 可读的简短摘要"""
        lines = [f"已收集 {self.total_facts_collected} 条事实，完成 {self.turns_completed} 轮访谈"]
        for cat, facts in self.facts.items():
            if facts:
                lines.append(f"\n{cat}: {len(facts)} 条")
                for f in facts[-3:]:  # 只展示最近 3 条
                    lines.append(f"  - {f[:100]}")
        if self.knowledge_gaps:
            lines.append(f"\n待补充: {', '.join(self.knowledge_gaps)}")
        return "\n".join(lines)
```

#### 4.3.4 集成到子图

```python
# domains/due_diligence/graph.py 中改造后的 interview 子图

class InterviewSubGraph:
    def __init__(self, llm, tool_registry, compressor: IncrementalCompressor):
        self.llm = llm
        self.tools = tool_registry
        self.compressor = compressor
        self.window_mgr = ContextWindowManager(model_name=...)
    
    def build(self):
        builder = StateGraph(InterviewState)
        
        builder.add_node("ask_question", self._ask)
        builder.add_node("search_web", self._search)
        builder.add_node("generate_answer", self._answer)
        builder.add_node("compress", self._compress)          # ← NEW
        builder.add_node("update_memory", self._update_memory) # ← NEW
        builder.add_node("save_interview", self._save)
        builder.add_node("write_section", self._write)
        
        builder.add_edge(START, "ask_question")
        builder.add_edge("ask_question", "search_web")
        builder.add_edge("search_web", "generate_answer")
        builder.add_edge("generate_answer", "compress")        # 每轮结束先压缩
        builder.add_edge("compress", "update_memory")          # 再更新工作记忆
        builder.add_conditional_edges(
            "update_memory",
            self._should_continue,  # 现在可以安全地 >1 轮了
            ["ask_question", "save_interview"],
        )
        builder.add_edge("save_interview", "write_section")
        # ...
    
    def _compress(self, state):
        """压缩当前轮次，形成结构化摘要"""
        current_turn = InterviewTurn(
            question=state["messages"][-2].content,  # 分析师问题
            answer=state["messages"][-1].content,     # 专家回答
            search_summary=self._summarize_context(state.get("context", [])),
        )
        compressed = self.compressor.compress_turn(current_turn)
        
        # 累积压缩历史
        compressed_history = state.get("compressed_turns", [])
        compressed_history.append(compressed)
        
        # 合并多轮记忆
        merged = self.compressor.merge_compressed(compressed_history)
        
        return {
            "compressed_turns": compressed_history,
            "working_memory": merged,
        }
```

**改动范围：**
- 新建 `harness/memory/`
- `interview_workflow.py` 中插入 `compress` 和 `update_memory` 节点
- `models.py` 中 `InterviewState` 增加 `compressed_turns` 和 `working_memory` 字段
- `max_num_turns` 默认值从 1 改为 3

---

### 4.4 人机协同层（Human-in-the-Loop）

**目标：** 人工审核不再是尽调专属的 `human_feedback` 节点，而是 Harness 的通用能力。

```python
# harness/human_loop/gate.py

class HumanReviewGate:
    """通用人工审核门。
    
    在任何 graph 中插入此节点后，流程自动在此暂停，
    等待外部通过 API 注入审核结果。
    """
    
    def __init__(self, review_target: str, version_key: str = "review_version"):
        """
        review_target: 审核什么（"analysts" / "plan" / "draft"）
        version_key: 版本号 state key
        """
        self.review_target = review_target
        self.version_key = version_key
    
    def __call__(self, state: dict) -> dict:
        """暂停节点——不做任何事，只标记等待审核"""
        return {"_review_target": self.review_target}
    
    @staticmethod
    def build_router(feedback_key: str, approved_next: str, revise_next: str):
        """构建审核路由函数"""
        def router(state: dict) -> str:
            feedback = (state.get(feedback_key, "") or "").strip()
            return revise_next if feedback else approved_next
        return router
```

---

### 4.5 可观测性层（Observability）

**目标：** 将现有的 `task_runtime.py` 通用化，并增加 Trace 能力。

**当前 `task_runtime.py` 已经做得不错：** 状态持久化 + 事件流 + 恢复 + 重试。主要改进：

```python
# harness/observability/tracer.py

class NodeTracer:
    """节点级 Trace 记录器。
    
    记录每个 graph node 的：
    - 输入/输出摘要
    - 耗时
    - LLM 调用次数和 token
    - 错误信息（如有）
    
    写入 `.runtime/traces/{task_id}.jsonl`
    """
    
    def trace_node(self, task_id: str, node_name: str, 
                   input_summary: str, output_summary: str,
                   duration_ms: int, llm_calls: list[dict],
                   error: str = None):
        ...
```

---

### 4.6 评测框架层（Evaluation）

**目标：** 从零为 Harness 平台设计三层评测体系——组件（模块质量）→ 集成（链路正确性）→ 端到端（报告质量）。

**核心理念：** 评价一个 Agent 平台，不能只看最终报告好不好，必须逐层证明每个基础设施组件本身是正确的。记忆压缩是否丢事实？清洗管线是否误杀有用信息？Checkpoint 恢复后状态是否一致？——这些才是 Harness 级别的质量保证。

#### 4.6.1 三层评测架构

```
        ┌──────────────────────────────┐
        │  Layer 3  端到端评测 (Agent)   │  报告完整性 / 来源可追溯 /
        │  回答「报告好不好」            │  事实准确性 / 多样性 / 反馈响应
        ├──────────────────────────────┤
        │  Layer 2  集成评测 (链路)      │  压缩→记忆→写作 链路一致性
        │  回答「组件协作对不对」        │  搜索→清洗→摘要 端到端可追溯
        ├──────────────────────────────┤
        │  Layer 1  组件评测 (模块)  ⭐  │  压缩保真度 / 清洗管线质量 /
        │  回答「每个模块本身可靠吗」    │  Checkpoint可靠性 / 上下文组装合规
        └──────────────────────────────┘
```

只有 Layer 3 的评测是「肤浅的」——你只能知道最终输出不好，但不知道是压缩丢了信息、清洗误杀了来源、还是模型本身幻觉。Layer 1+2 让你能**定位到具体是哪个组件出了问题**。

#### 4.6.2 Layer 1：组件级评测 —— 每个 Harness 模块的独立质量 ⭐核心

这是计划书 v1.0 完全缺失的部分，也是 Harness 平台区别于普通 AI 应用的关键。

---

##### ① 记忆压缩评测 `CompressionEvaluator`

**要回答的问题：** 压缩后关键事实丢了吗？压缩虚构了不存在的事实吗？evidence_quality 标注准吗？

**已有数据基础：** `IncrementalCompressor.compress_completed_turn()` → `CompressedTurn`，包含 `facts` (list[MemoryFact])、`numbers_mentioned`、`unanswered`、`compression_error`。

```
指标                   定义                                  方法             目标
───────────────────────────────────────────────────────────────────────────────
token_ratio           tokens(压缩后)/tokens(原始对话)          直接计算          < 30%
fact_retention        标注事实在压缩facts中的出现比例           LLM-Judge逐条比对  > 90%
hallucination_rate    压缩facts中有多少在原始对话中找不到       LLM-Judge反向验证  < 5%
number_retention      数值(金额/百分比/日期)在压缩中的保留率    正则提取+比对      > 95%
error_rate            compression_error 非空的概率             统计              < 5%
quality_accuracy      evidence_quality标注与人工标注一致率      vs golden set     > 85%
latency_p50_ms        压缩耗时中位数                          计时              记录baseline
```

**Fixture 设计（最小 10 条，覆盖不同领域和复杂度）：**

```python
# tests/fixtures/compression/case_001_tesla_q3.json
{
    "case_id": "comp_tesla_q3",
    "domain": "due_diligence",
    "original_turn": {
        "question": "Tesla Q3 2025 的营收和毛利率是多少？与市场预期的差距如何？",
        "search_summary": "Tesla Q3 2025 revenue was $25.18B (+8% YoY), slightly below "
                         "the $25.47B consensus. Gross margin reached 19.8%, beating "
                         "estimates of 17.2%. Net income was $2.18B. Free cash flow: $2.7B.",
        "answer": "特斯拉2025年Q3营收251.8亿美元（同比+8%），略低于市场预期的254.7亿美元。"
                 "毛利率19.8%超预期（预期17.2%），净利润21.8亿美元，自由现金流27亿美元。"
    },
    "labeled_facts": [
        "Q3 2025 revenue: $25.18 billion",
        "Revenue growth: 8% YoY",
        "Revenue consensus: $25.47B — actual missed by $290M",
        "Gross margin: 19.8% vs consensus 17.2%",
        "Net income: $2.18B",
        "Free cash flow: $2.7B"
    ],
    "labeled_numbers": [
        {"value": 25.18, "unit": "billion_usd", "context": "Q3 2025 revenue"},
        {"value": 254.7, "unit": "billion_usd", "context": "consensus revenue"},
        {"value": 19.8, "unit": "percent", "context": "gross margin"},
        {"value": 17.2, "unit": "percent", "context": "consensus margin"},
        {"value": 2.18, "unit": "billion_usd", "context": "net income"},
        {"value": 2.7, "unit": "billion_usd", "context": "free cash flow"}
    ],
    "expected_evidence_quality": "high",
    "expected_unanswered": []  // all questions answered by the search results
}
```

**评测流程（自动化）：**
```python
class CompressionEvaluator:
    def evaluate(self, fixture: dict, compressor: IncrementalCompressor) -> CompressionMetrics:
        # 1. 跑压缩
        result = compressor.compress_completed_turn(
            question=fixture["original_turn"]["question"],
            answer=fixture["original_turn"]["answer"],
            search_summary=fixture["original_turn"]["search_summary"],
        )
        # 2. 计算 token_ratio（已有 ContextWindowManager.estimate_tokens）
        original_tokens = window_mgr.estimate_tokens(
            fixture["original_turn"]["question"] + 
            fixture["original_turn"]["answer"]
        )
        compressed_tokens = window_mgr.estimate_tokens(result.format())
        
        # 3. 比对 facts（LLM-Judge，每条标注事实判"出现/未出现/部分出现"）
        retention = self._judge_fact_retention(result.facts, fixture["labeled_facts"])
        
        # 4. 反向查幻觉（compressed facts 中哪些在原文找不到对应）
        hallucinations = self._detect_hallucinations(result.facts, fixture["original_turn"])
        
        # 5. 数值提取比对（不需要 LLM，正则即可）
        number_hits = self._match_numbers(result.numbers_mentioned, fixture["labeled_numbers"])
        
        return CompressionMetrics(...)
```

---

##### ② 数据清洗管线评测 `PipelineEvaluator`

**要回答的问题：** 管线去掉的真是噪声吗？留下的真是有效信息吗？每个 Stage 贡献了多少？

**已有数据基础：** `ToolPipeline.run_with_trace()` 已返回 `list[StageTrace]`（`stage`、`duration_ms`、`input_count`、`output_count`、`reduction_pct`、`dropped_count`）。**数据已在收集，就差一个评测聚合器和标注 fixture！**

```
指标                      定义                                  方法               目标
──────────────────────────────────────────────────────────────────────────────────
per_stage_reduction_pct   每个Stage的文档削减率                 已有(StageTrace)   记录baseline
per_stage_latency_ms      每个Stage的耗时                       已有(StageTrace)   记录baseline
total_noise_reduction     1 - len(cleaned_text)/len(raw_text)   直接计算           40-60%
dedup_precision           被标记为重复的文档中真正的重复比例      人工标注标签        > 95%
dedup_recall              所有真正重复中被管线发现的比例          人工标注标签        > 90%
relevance_precision_at_10 Top-10结果中真正相关的比例             LLM-Judge逐条      > 80%
false_drop_rate           被管线丢弃的文档中有多少其实有用         LLM逆向检查        < 10%
```

**Fixture 设计（带标签的搜索结果集）：**

```python
# tests/fixtures/pipeline/search_mixed_quality.json
{
    "case_id": "pipe_mixed_001",
    "target_entity": "Apple Inc.",
    "raw_results": [
        {
            "url": "https://techcrunch.com/2025/06/apple-intelligence-ios19",
            "title": "Apple Intelligence Launches with iOS 19 — Full Breakdown",
            "content": "In June 2025, Apple announced Apple Intelligence... on-device processing... "
                       "partnership with OpenAI... 15 billion parameters... privacy-first approach...",
            "label": "relevant",
            "expected_dedup": "unique"
        },
        {
            "url": "https://techcrunch.com/2025/06/apple-intelligence-ios19?utm_source=twitter",
            "title": "Apple Intelligence Launches with iOS 19 — Full Breakdown",
            "content": "In June 2025, Apple announced Apple Intelligence... (shorter version)",
            "label": "relevant",
            "expected_dedup": "duplicate_of_above"  // same canonical URL
        },
        {
            "url": "https://spam-blog.example/buy-iphone-cheap",
            "title": "Buy iPhone 16 Cheap!!! Best Deal 2025 Click Here",
            "content": "iPhone iPhone iPhone... BUY NOW... LIMITED OFFER... cheap cheap cheap...",
            "label": "spam"
        },
        {
            "url": "https://finance.yahoo.com/quote/AAPL",
            "title": "Apple Inc. (AAPL) Stock Price, News & Valuation",
            "content": "Apple stock closed at $198.25. Market cap: $3.2T. P/E ratio: 31.2...",
            "label": "relevant",
            "expected_dedup": "unique"
        },
        {
            "url": "https://en.wikipedia.org/wiki/Apple",
            "title": "Apple (fruit) — Wikipedia",
            "content": "An apple is a round, edible fruit produced by an apple tree... Malus domestica...",
            "label": "irrelevant"
        }
    ],
    "expected_kept_indices": [0, 3],
    "expected_dropped_reasons": {
        1: "duplicate_url",
        2: "low_quality",
        4: "low_relevance"
    }
}
```

**评测流程：**

```python
class PipelineEvaluator:
    def evaluate(self, fixture: dict, pipeline: ToolPipeline) -> PipelineMetrics:
        docs = [SearchDocument(**d) for d in fixture["raw_results"]]
        ctx = ToolContext(target_entity=fixture["target_entity"])
        
        # 1. 跑管线（已有trace）
        cleaned, trace = pipeline.run_with_trace(docs, ctx)
        
        # 2. 统计已有的逐Stage指标
        per_stage = {
            t.stage: {"reduction_pct": t.reduction_pct, "latency_ms": t.duration_ms,
                       "dropped": t.dropped_count, "warnings": t.warning_count}
            for t in trace
        }
        
        # 3. 交叉比对预期保留/丢弃（不需要LLM）
        kept_indices = [i for i, d in enumerate(cleaned) if not d.dropped_reason]
        dropped_indices = [i for i, d in enumerate(cleaned) if d.dropped_reason]
        
        dedup_correct = sum(1 for i in dropped_indices 
                           if fixture["expected_dropped_reasons"].get(i) == cleaned[i].dropped_reason)
        dedup_precision = dedup_correct / len(dropped_indices) if dropped_indices else 1.0
        
        return PipelineMetrics(
            per_stage=per_stage,
            dedup_precision=dedup_precision,
            kept_match=(set(kept_indices) == set(fixture["expected_kept_indices"])),
            ...
        )
```

---

##### ③ Trace / Checkpoint 可靠性评测 `RuntimeReliability`

**要回答的问题：** Checkpoint 靠谱吗？重启后状态一致吗？事件丢了吗？LLM 调用都有 token 记录吗？

**已有数据基础：** `TaskRuntime` + `NodeTracer` + `MetricsCollector`。**这些可以做成完全确定性的纯代码测试，不需要 LLM。**

```
指标                      定义                                      方法                  目标
────────────────────────────────────────────────────────────────────────────────────────
checkpoint_roundtrip      中断→恢复→状态逐字段 diff=0                 注入中断+恢复+逐字段比对  100%
event_stream_completeness 所有必须事件存在 + turn序号连续             收集events+序列校验       100%
llm_metrics_completeness  每个LLM调用节点都有 token/latency 记录      遍历TraceEntry检查        100%
source_id_continuity      所有 source_registry 的 S-n 序号连续无gap   遍历检查                  100%
state_consistency         turns_completed==len(compressed_turns) 等   规则校验                  100%
error_recovery_rate       模拟N次崩溃→恢复，成功次数/N                自动化重复测试            > 95%
```

**评测流程（纯代码，确定性断言）：**

```python
class RuntimeReliabilityEvaluator:
    """全部不需要LLM，在CI中每次PR都跑"""
    
    async def test_checkpoint_state_roundtrip(self, graph, checkpointer):
        """中断→恢复→状态完全一致"""
        thread = {"configurable": {"thread_id": "eval_checkpoint_test"}}
        config = {"configurable": {"thread_id": "eval_checkpoint_test"}}
        
        # 跑到 compact_history 之后
        graph.update_state(config, {"max_num_turns": 2, "company_name": "TestCo"})
        async for event in graph.astream({"messages": []}, config):
            if event.get("node") == "compact_history":
                state_before = dict(graph.get_state(config).values)
                break
        
        # 模拟进程崩溃（新的 graph 实例，复用 checkpointer）
        graph2 = graph.compile(checkpointer=SqliteSaver.from_conn_string(":memory:"))
        state_after = dict(graph2.get_state(config).values)
        
        # 逐字段 diff
        diff = self._deep_diff(state_before, state_after)
        assert diff == {}, f"State mismatch after checkpoint roundtrip: {diff}"
    
    async def test_event_stream_no_gaps_no_dupes(self, events_file: str):
        """事件流：必须事件不缺 + turn序号连续 + 无重复"""
        events = [json.loads(line) for line in open(events_file)]
        
        # 必须事件
        required = ["router.search.completed", "compress.completed", "memory.updated"]
        for event_type in required:
            assert any(e["event"] == event_type for e in events), f"Missing: {event_type}"
        
        # turn 序号连续
        compress_events = [e for e in events if e["event"] == "compress.completed"]
        turns = sorted(e["payload"]["turn"] for e in compress_events)
        assert turns == list(range(1, len(turns) + 1)), f"Turn gap in events: {turns}"
        
        # 无重复事件（同event+同turn只能出现一次）
        seen = set()
        for e in compress_events:
            key = (e["event"], e["payload"]["turn"])
            assert key not in seen, f"Duplicate event: {key}"
            seen.add(key)
    
    async def test_llm_metrics_completeness(self, tracer: NodeTracer):
        """每个LLM调用节点都有完整的 token 记录"""
        llm_nodes = {"ask_question", "generate_answer", "write_section"}
        for entry in tracer.read_all():
            if entry.node_name in llm_nodes:
                assert entry.total_tokens > 0, \
                    f"{entry.node_name} ({entry.trace_id}): zero tokens recorded"
                assert entry.duration_ms > 0, \
                    f"{entry.node_name} ({entry.trace_id}): no latency recorded"
                if entry.error is None:
                    assert entry.total_prompt_tokens > 0, \
                        f"{entry.node_name}: prompt tokens missing"
                    assert entry.total_completion_tokens > 0, \
                        f"{entry.node_name}: completion tokens missing"
```

---

##### ④ 上下文组装评测 `ContextAssemblyEvaluator`

**要回答的问题：** 在 token 预算约束下，是否优先保留了最重要的信息？

**已有数据基础：** `ContextAssembler.assemble()` → `ContextAssemblyResult`，包含 `total_tokens` 和 `token_breakdown`（分段 token 计数：`system_prompt`、`working_memory`、`recent_messages`、`search_digest` 等）。

```
指标                   定义                              方法               目标
────────────────────────────────────────────────────────────────────────────────
budget_compliance      total_tokens ≤ safe_limit          直接检查           100%
degradation_rate       触发 ContextBudgetExceeded 的频率   统计              < 5%
priority_order         在预算压力下，删除优先级是否按:     构造超预算          验证
                       long_term → digest → wm → summary   场景验证
                       → older_messages → (never user msg)
```

---

#### 4.6.3 Layer 2：集成评测 —— 组件串联后的链路正确性

##### ① 压缩→记忆→写作 链路

```
  Q&A → compress_completed_turn() → CompressedTurn
       → WorkingMemory.ingest_compressed_turn() → WorkingMemory
       → write_section() → 报告段落（含 [Sn] 引用）
```

**一致性校验（纯代码规则）：**

| 规则 | 断言 | 说明 |
|------|------|------|
| turns_completed 一致 | `wm.turns_completed == len(compressed_turns)` | 记忆进度与压缩历史同步 |
| fact来源可追溯 | 每个 fact.source_ids ⊆ source_registry keys | 所有事实能找到原始URL |
| S-n ID连续无gap | source_registry key按S1,S2,...Sn排序 | 引用序号不出错 |
| knowledge_gaps收敛 | 第N+1轮的gaps ≤ 第N轮的gaps | 多轮研究应减少未知 |
| 报告引用有效 | 报告中的 [Sn] 引用 ≤ source_registry 最大序号 | 不引用不存在的来源 |

##### ② 搜索→清洗→摘要→回答 链路

```
  search_query → search_backend.search() → raw SearchDocument[]
       → pipeline.run_with_trace() → cleaned SearchDocument[]
       → search_digest_builder.build() → SearchDigest
       → context → generate_answer → 专家回答
```

**端到端可追溯校验：**

| 规则 | 断言 | 说明 |
|------|------|------|
| SearchDigest.source_ids ⊆ source_registry keys | 摘要引用的source都在registry中 | 不丢引用 |
| 管线最终产物与原始输入关联 | cleaned中每个doc可追溯到raw中某个 | 不凭空产生 |
| 跨轮 source_registry 累加不覆盖 | 第N轮新增的S-n序号 > 第N-1轮最大序号 | interview.py 已修复 |

---

#### 4.6.4 Layer 3：端到端评测 —— 回答「报告好不好」

这是原 4.6.1 的内容，保留并补充 3 个 Harness 特有能力相关的维度：

| 维度 | 类型 | 分值 | 描述 |
|------|------|:---:|------|
| `report_completeness` | 静态检查 | 0/1 | 最终报告是否包含所有必需章节 |
| `source_traceability` | 静态检查 | 0/1/2 | 有多少声明能追溯到具体的 [S{n}] 引用 |
| `factual_accuracy` | 混合（静态+LLM） | 0/1/2 | 报告中的事实是否与 fixture 搜索结果一致 |
| `analyst_diversity` | LLM-Judge | 0/1/2 | 分析师角色是否真正多样化 |
| `feedback_responsiveness` | LLM-Judge | 0/1/2 | 重新生成的分析师是否响应了反馈 |
| **`compression_fidelity`** ⭐ | LLM-Judge | 0/1/2 | **3轮 vs 1轮报告：结论是否一致但更丰富（不丢信息）** |
| **`cleaning_effect`** ⭐ | 混合 | 0/1/2 | **清洗后 vs 不清洗直接喂LLM：事实密度是否提升** |
| **`multi_turn_depth`** ⭐ | 代码分析 | 0/1/2 | **第N轮比第N-1轮多发现了什么新事实（多轮价值证明）** |

---

#### 4.6.5 Fixture 设计

**端到端 Fixture（Layer 3）：**

```python
# tests/fixtures/end_to_end/case_001_apple.json
{
    "case_id": "case_001_apple",
    "company_name": "Apple Inc.",
    "focus": "AI strategy and financial health",
    "industry_pack": "ai",
    "search_fixtures": {
        "apple ai strategy": {
            "results": [
                {
                    "url": "https://techcrunch.com/2025/06/apple-intelligence-ios19",
                    "title": "Apple Intelligence Launches with iOS 19",
                    "content": "In June 2025, Apple announced Apple Intelligence... on-device AI processing... partnership with OpenAI..."
                }
            ]
        },
        "apple financial revenue services": { "results": [...] },
        "apple risk regulatory eu dma": { "results": [...] }
    },
    "expected": {
        "required_sections": [
            "Company Overview", "Business Breakdown", "Scale & Growth",
            "Risk Assessment", "Final Recommendations", "Sources"
        ],
        "key_facts_must_include": [
            "Apple Intelligence", "Services revenue", "on-device AI"
        ],
        "risk_themes": ["regulatory/DMA", "AI competition", "iPhone dependency"],
        "min_analyst_count": 3,
        "diverse_roles_expected": true
    }
}
```

**组件评测 Fixture（Layer 1）** 已在 4.6.2 各子节定义，统一存放：

```text
tests/fixtures/
├── compression/              # 压缩评测
│   ├── case_001_tesla_q3.json
│   ├── case_002_apple_ai.json
│   └── ...
├── pipeline/                 # 管线评测
│   ├── search_mixed_quality.json
│   ├── search_all_relevant.json
│   ├── search_all_noise.json
│   └── ...
└── end_to_end/               # 端到端评测
    ├── case_001_apple.json
    ├── case_002_tesla.json
    └── case_003_bytedance.json
```

---

#### 4.6.6 Scorer 基类与注册

```python
# harness/evaluation/scorer.py
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

@dataclass
class ScoreResult:
    dimension: str
    value: float          # 0-N 归一化分数
    max_value: float
    normalized: float     # value / max_value, 0-1
    status: str           # "pass" | "partial" | "fail"
    details: str = ""
    issues: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

class Scorer(ABC):
    """Scorer 基类。每个评测维度一个子类。"""
    
    @property
    @abstractmethod
    def dimension(self) -> str: ...
    
    @property
    @abstractmethod
    def layer(self) -> str: ...  # "component" | "integration" | "end_to_end"
    
    @abstractmethod
    def score(self, result: Any, fixture: dict | None = None) -> ScoreResult: ...

# 全局 Scorer 注册表
SCORER_REGISTRY: dict[str, Scorer] = {}

def register_scorer(scorer: Scorer) -> None:
    SCORER_REGISTRY[scorer.dimension] = scorer
```

**示例：压缩保真度 Scorer**

```python
# harness/evaluation/scorers/compression_fidelity.py

class CompressionFidelityScorer(Scorer):
    dimension = "compression_fidelity"
    layer = "component"
    
    def score(self, result: CompressedTurn, fixture: dict) -> ScoreResult:
        labeled = fixture["labeled_facts"]
        compressed_fact_texts = [f.text for f in result.facts]
        
        # LLM-Judge: 每条标注事实是否在压缩facts中出现
        found = 0
        for fact in labeled:
            judge = self._llm_judge(fact, compressed_fact_texts)
            if judge["present"]:
                found += 1
        
        retention = found / len(labeled) if labeled else 1.0
        
        # 反向：压缩facts中有多少是原文没有的（幻觉）
        hallucinations = self._detect_hallucinations(result.facts, fixture["original_turn"])
        hallu_rate = len(hallucinations) / len(result.facts) if result.facts else 0
        
        normalized = retention * (1 - hallu_rate)  # 综合分
        
        return ScoreResult(
            dimension=self.dimension,
            value=round(normalized * 2, 1),
            max_value=2,
            normalized=normalized,
            status="pass" if normalized >= 0.85 else ("partial" if normalized >= 0.7 else "fail"),
            details=f"Retention: {retention:.0%}, Hallucination: {hallu_rate:.0%}",
            issues=[f"Missing fact: {labeled[i]}" for i in range(len(labeled)) 
                    if labeled[i] not in [f for f, found_flag in zip(labeled, [True]*len(labeled)) 
                                          if found_flag]],
            evidence={"retention": retention, "hallucination_rate": hallu_rate, 
                      "found": found, "total": len(labeled)}
        )
```

**示例：来源可追溯 Scorer（纯代码，不需要 LLM）：**

```python
# harness/evaluation/scorers/source_traceability.py

def score_source_traceability(report_text: str, fixture: dict) -> dict:
    """检查报告中每个事实声明是否有来源引用"""
    cited = set(int(m) for m in re.findall(r'\[(\d+)\]', report_text))
    
    sources_match = re.search(r'## Sources\n(.*?)(?:\n##|\Z)', report_text, re.DOTALL)
    if not sources_match:
        return {"status": "scored", "value": 0, "issues": ["Missing Sources section"]}
    
    sources_text = sources_match.group(1)
    source_entries = len(re.findall(r'^\s*\[\d+\]', sources_text, re.MULTILINE))
    
    orphan_citations = [n for n in cited if n > source_entries]
    unused_sources = [n for n in range(1, source_entries+1) if n not in cited]
    
    if not orphan_citations and not unused_sources:
        return {"status": "scored", "value": 2, "details": "All citations traceable"}
    elif orphan_citations:
        return {"status": "scored", "value": 0, 
                "issues": [f"Orphan citations: {orphan_citations}"]}
    else:
        return {"status": "scored", "value": 1, 
                "issues": [f"Unused source entries: {unused_sources}"]}
```

---

#### 4.6.7 Runner 与可靠性分析

```python
# harness/evaluation/runner.py

@dataclass
class EvalRunResult:
    run_id: str
    case_id: str
    scores: list[ScoreResult]
    trace: dict[str, Any]         # 全链路trace数据
    duration_ms: int
    error: str | None = None

class EvalRunner:
    """评测 Runner：单case + 批量 + N次重复"""
    
    def __init__(self, scorers: list[Scorer] | None = None):
        self.scorers = scorers or list(SCORER_REGISTRY.values())
    
    def run_single(self, case: dict, *, layers: list[str] | None = None) -> EvalRunResult:
        """跑单个 case，可选择只跑某些 layer 的 scorer"""
        ...
    
    def run_batch(self, cases: list[dict], repeats: int = 3, 
                  layers: list[str] | None = None) -> list[EvalRunResult]:
        """N个case × M次重复 → 可靠性分析"""
        ...


# harness/evaluation/reliability.py

@dataclass
class ReliabilityReport:
    """可靠性分析报告"""
    dimension_stats: dict[str, dict]   # {dim: {mean, std, cv, pass_rate, n_runs}}
    overall_pass_rate: float
    high_variance_dims: list[str]      # CV > 0.3 的维度
    per_case_summary: list[dict]
    
    def format_markdown(self) -> str:
        """生成 Markdown 格式的可靠性报告"""
        ...

def analyze_reliability(results: list[EvalRunResult]) -> ReliabilityReport:
    """对 N 次重复实验做 CV/σ/通过率分析"""
    # 按 dimension 分组，计算 mean/std/cv
    ...
```

---

#### 4.6.8 状态一致性自动校验（Layer 2 补充）

这些纯代码规则可以在每次 run 结束后自动执行，作为「内置 health check」：

```python
# harness/evaluation/consistency_checks.py

CONSISTENCY_CHECKS: list[dict] = [
    {
        "name": "turns_completed_matches_compressed",
        "check": lambda state: (
            WorkingMemory.from_dict(state.get("working_memory", {})).turns_completed
            == len(state.get("compressed_turns", []))
        ),
        "severity": "error",
    },
    {
        "name": "source_ids_sequential",
        "check": lambda state: _check_source_ids_sequential(state.get("source_registry", {})),
        "severity": "error",
    },
    {
        "name": "all_fact_sources_in_registry",
        "check": lambda state: _check_fact_sources(state),
        "severity": "error",
    },
    {
        "name": "knowledge_gaps_non_increasing",
        "check": lambda state: _check_gaps_convergence(state),
        "severity": "warning",
    },
    {
        "name": "llm_metrics_present",
        "check": lambda state: len(state.get("llm_metrics", [])) > 0,
        "severity": "warning",
    },
]

def run_consistency_checks(state: dict) -> list[dict]:
    """在每次run/write_section之后自动运行"""
    results = []
    for check in CONSISTENCY_CHECKS:
        try:
            passed = check["check"](state)
            if not passed:
                results.append({"check": check["name"], "passed": False, 
                                "severity": check["severity"]})
        except Exception as e:
            results.append({"check": check["name"], "passed": False, 
                            "severity": check["severity"], "error": str(e)})
    return results
```

---

#### 4.6.9 实施路线

```
优先级    评测项                          工作量   已有基础
────────────────────────────────────────────────────────
P0  │ Pipeline trace 聚合报告            0.5天   run_with_trace 已在收集逐Stage数据
P0  │ Checkpoint roundtrip 测试          1天     纯代码, TaskRuntime + NodeTracer 已有
P0  │ 状态一致性校验规则                  0.5天   纯代码, 数据模型齐全
P0  │ Scorer 基类 + 注册机制             0.5天   零基础, 架构简单
P1  │ 压缩保真度 (10条fixture + LLM-Judge) 2天   compressor已有, 需构造标注集
P1  │ 管线去重/相关性 precision (5条fixture) 1.5天 pipeline已有, 需标注数据+LLM-Judge
P1  │ source_traceability scorer         0.5天   纯正则, 不需要LLM
P2  │ 端到端3轮vs1轮对比 (3条fixture)     2天     复用已有流程
P2  │ 可靠性报告自动生成 (CV/σ分析)       1天     已有metrics+traces数据
P2  │ Runner 批量+重复执行                1天     EvalRunner 框架
P3  │ 集成链路校验规则                    1天     已有数据模型
P3  │ 扩充fixture至15条                  2天     分批构造
```

**最小可行方案（1周内可交付）：**
1. `harness/evaluation/` 目录 + `scorer.py` 基类 + 注册机制
2. Pipeline trace → 自动产出 `pipeline_metrics.json`
3. 5条压缩fixture → 人工标注事实 → 自动跑 retention/hallucination 分数
4. Checkpoint roundtrip test → CI 中自动跑
5. `source_traceability` + `report_completeness` 两个纯代码 scorer
6. 产出第一份 `reliability_report.md`

---

## 5. 实施路线图

### Phase 1：基础重构（约 1 周）

**目标：** 把现有代码拆成 harness 层和 domain 层，不改变功能。

```
任务：
1.1  创建 harness/ 目录结构（所有空文件 + __init__.py）
1.2  将 task_runtime.py 迁入 harness/observability/，去除尽调相关字段
1.3  将 models.py 拆分：
     - harness/models/ ← 通用类型（Task, Event, AgentState...）
     - domains/due_diligence/schemas.py ← 尽调专用类型
1.4  将 prompt_locator.py 拆分：
     - domains/due_diligence/prompts/ ← 尽调 prompts
     - harness/ 中不存 prompt（prompt 属于 domain）
1.5  创建 domains/base.py（DomainAdapter 基类）
1.6  创建 domains/due_diligence/，将现有 workflow + prompt 迁入
1.7  确保迁完后系统仍能正常启动和运行
```

**验收标准：** `python backend/start_api.py` 正常启动，完整流程可走通，功能无变化。

### Phase 2：工具集成与数据清洗（约 1 周）

**目标：** 搜索结果经过清洗管线，消除 70%+ 的噪声。

```
任务：
2.1  实现 harness/tools/pipeline.py（ToolPipeline + ProcessingStage 基类）
2.2  实现 harness/tools/search/cleaner.py（5 个 Stage）
2.3  实现 harness/tools/search/tavily_adapter.py（Tavily → 标准接口）
2.4  改造 interview_workflow._search_web() 走管线
2.5  手动测试：对比清洗前后的搜索结果质量
```

**验收标准：** 搜索结果清洗后体积减少 40-60%，信息密度提升。LLM 回答中的事实引用准确率提升（主观评估）。

### Phase 3：记忆管理与上下文压缩（约 1.5 周）⭐

**目标：** `max_num_turns` 从 1 提升到 3，上下文窗口不爆炸。

```
任务：
3.1  实现 harness/memory/compressor.py（IncrementalCompressor）
3.2  实现 harness/memory/working_memory.py（WorkingMemory）
3.3  实现 harness/memory/context_window.py（ContextWindowManager）
3.4  在 models.py 中给 InterviewState 增加字段：
     - compressed_turns: list
     - working_memory: dict
3.5  改造 interview 子图，插入 compress + update_memory 节点
3.6  修改默认 max_num_turns = 3
3.7  手动测试：跑一次 3 轮访谈，检查：
     - 每轮 token 不爆炸
     - 压缩摘要信息不丢失
     - 工作记忆覆盖度追踪准确
```

**验收标准：**
- 3 轮访谈后 Token 增长 < 30%（vs 当前 3 倍增长）
- 压缩摘要保留关键事实（人工抽查 5 条事实是否可在压缩后的摘要中找到）
- `max_num_turns=3` 能稳定运行不出错

### Phase 4：Eval 框架（约 1.5 周）

**目标：** 三层评测体系落地——能回答「每个模块本身可靠吗」（Layer 1）、「组件协作对不对」（Layer 2）、「最终报告好不好」（Layer 3）。

```
任务：
4.1  创建 harness/evaluation/ 目录结构
     - scorer.py（Scorer 基类 + ScoreResult + SCORER_REGISTRY）
     - runner.py（EvalRunner：单case + 批量 + N次重复）
     - fixtures.py（Fixture 加载 + fuzzy query match）
     - reliability.py（CV/σ/通过率分析 → Markdown报告）
     - consistency_checks.py（状态一致性自动校验规则）

4.2  实现 Layer 1 Scorer（4个）：
     4.2.1 compression_fidelity.py —— 压缩保真度（LLM-Judge）
     4.2.2 pipeline_quality.py —— 管线质量（纯代码聚合StageTrace + LLM-Judge标注比对）
     4.2.3 checkpoint_reliability.py —— Checkpoint可靠性（纯代码，CI自动跑）
     4.2.4 context_assembly.py —— 上下文组装合规（纯代码）

4.3  实现 Layer 2 集成校验（2个）：
     4.3.1 memory_consistency.py —— 压缩→记忆→写作 链路规则校验
     4.3.2 search_traceability.py —— 搜索→清洗→摘要 端到端可追溯

4.4  实现 Layer 3 Scorer（2个）：
     4.4.1 source_traceability.py（纯正则，不需要LLM）
     4.4.2 report_completeness.py（静态检查必需章节）

4.5  写 Fixture（15条）：
     - compression/: 5条（Tesla Q3 / Apple AI / Bytedance / Tesla risk / 空白搜索）
     - pipeline/: 5条（混合质量 / 全相关 / 全噪声 / 重复URL / 中文内容）
     - end_to_end/: 3条（Apple / Tesla / Bytedance）

4.6  写 Checkpoint 可靠性自动化测试（CI集成）

4.7  跑 baseline eval：3 case × 3 runs = 9 次实验，产出可靠性报告

4.8  对比 Phase 2+3 改动前后的 eval 分数变化
```

**验收标准：**
- `harness/evaluation/` 目录包含 scorer.py + runner.py + fixtures.py + reliability.py + consistency_checks.py
- 6 个 Scorer 全部可运行，单 case 评测时间 < 5min
- 9 次实验全部完成，成功率 > 80%
- 产出第一份可靠性报告（Markdown 格式）
- Layer 1 至少有一个维度 CV < 0.3
- 状态一致性校验在 CI 中每次 PR 都跑

### Phase 5：补充与打磨（约 1 周）

```
任务：
5.1  给 harness/ 核心模块写 15-20 个单元测试
5.2  写第二组 fixture（2-3 个不同行业的 case）
5.3  写 README 更新，包括架构图和 Harness 定位说明
5.4  录 2 分钟 Demo 视频
5.5  前端 TaskDetail 页增加「压缩摘要」和「工作记忆」可视化
```

---

## 6. 关键设计决策

### 6.1 为什么不用 LangGraph 的 Built-in Memory？

LangGraph 有 `MemoryStore`，但它目前是 key-value 的简单存储，不支持：
- 结构化工作记忆的增量更新
- 逐轮结构化压缩（它只能存不能压缩）
- 覆盖率追踪和缺口检测

我们的 Memory 层是在 LangGraph state 之上的语义层，是互补关系——LangGraph 管状态持久化，我们管信息密度。

### 6.2 压缩用哪个模型？

**Cheap Model。** 压缩不需要推理能力，只需要提取事实。用 `gpt-4o-mini` 或 `deepseek-chat` 即可。单轮压缩成本 < ¥0.01。

### 6.3 数据清洗为什么用管线模式而不是 Agent 模式？

管线模式（可预测、可调试、可计量）比 Agent 模式（让 LLM 自己决定怎么洗）更适合这个场景。洗数据是机械工作，不是推理工作。管线每步可单独开关、单独测试、单独计量。

### 6.4 为什么要保留 domains/ 而不是全改 skill pack？

Skill Pack（YAML）负责**配置**——prompt 模板、search policy、question templates。Domain（Python）负责**行为**——图结构定义、特殊节点逻辑。两者不是替代关系：

- `skills/ai/skill_pack.yaml` = 这个行业的分析师长什么样、搜索什么源
- `domains/due_diligence/graph.py` = 这个领域的工作流怎么编排

---

## 7. 预期成果

### 7.1 改造后的能力矩阵

| 能力 | 改造前 | 改造后 |
|------|:---:|:---:|
| 支持多轮深度访谈 | ❌ max_turns=1 | ✅ max_turns=3，稳定不爆上下文 |
| 搜索结果质量 | ⭐⭐ 原始结果直接喂 | ⭐⭐⭐⭐ 9 阶段清洗管线 |
| 上下文窗口管理 | ❌ 无 | ✅ 精确 token 估算 + 自动触发压缩 |
| 领域可插拔 | ❌ 尽调写死 | ✅ domain adapter 模式 |
| 工具标准化接口 | ❌ Tavily 写死 | ✅ ToolPipeline + 7个Adapter |
| 评测体系 | ❌ 无 | ✅ 三层评测（组件→集成→端到端） |
| 组件级质量度量 | ❌ 无 | ✅ 压缩保真度 / 管线质量 / Checkpoint可靠性 |
| 可靠性量化 | ❌ 无 | ✅ CV/σ/通过率报告 |
| 状态一致性自动校验 | ❌ 无 | ✅ CI集成的纯代码健康检查 |
| 测试覆盖 | ❌ 0 | ✅ 15-20 个核心测试 |

### 7.2 简历定位建议

**项目名：** AgentHarness — 多智能体编排与评测平台

**一句话：** 一个可插拔领域技能、内置记忆管理、带评测框架的 AI Agent 基础设施平台。

**技术亮点：**
- 自研逐轮增量式结构化压缩：3 轮深度访谈 Token 增长 < 30%
- 5 阶段工具调用管线（去重→清洗→过滤→结构化→格式化）
- 结构化工作记忆 + 覆盖率追踪 + 缺口检测
- 3 态评分评测框架 + Fixture 仿真 + CV/σ 可靠性分析
- 可插拔领域适配（尽调/股评/法审），YAML 技能包驱动
- LangGraph StateGraph 编排 + Human-in-the-Loop + 异步任务运行时

---

## 8. 风险与应对

| 风险 | 概率 | 影响 | 应对 |
|------|:---:|:---:|------|
| 压缩丢信息 | 中 | 高 | 保留最新 1 轮完整内容兜底；人工抽查验证 |
| 数据清洗管道过长导致延迟 | 低 | 中 | 每步记录耗时，超 5s 的 Stage 异步化 |
| 重构引入回归 bug | 中 | 高 | Phase 1 只迁不写新逻辑；迁完后全流程回归测试 |
| Eval fixture 不够真实 | 中 | 中 | 从真实 Tavily 结果采样来写 fixture，而非纯手编 |
| 时间不够 | 中 | — | Phase 1-3 是核心，Phase 4-5 可以压缩 |

---

## 附录：改造后的完整数据流

```
用户提交公司名
  │
  ▼
FastAPI 路由 → TaskRuntime.create_task()
  │
  ▼
DomainAdapter.start()
  │
  ├─ AgentRuntime.build_graph()     ← harness 提供图模板
  │   ├─ domain.plan_node()         ← domain 提供业务逻辑
  │   ├─ HumanReviewGate()          ← harness 提供审核门
  │   └─ FanOut → N × InterviewSubGraph  ← harness 提供 fan-out
  │       │
  │       ├─ ask_question           ← domain prompt
  │       ├─ search_web             ← ToolPipeline (harness)
  │       │   ├─ DeduplicateStage
  │       │   ├─ RelevanceFilterStage
  │       │   ├─ StructureFactsStage
  │       │   └─ FormatDocumentStage
  │       ├─ generate_answer        ← domain prompt
  │       ├─ compress               ← IncrementalCompressor (harness)
  │       ├─ update_memory          ← WorkingMemory (harness)
  │       └─ write_section          ← domain prompt
  │
  ├─ write_report / intro / conclusion  ← domain prompts
  ├─ ReviewReport()                     ← harness 提供审查
  ├─ finalize_report
  └─ save_report (DOCX + PDF)
  │
  ▼
TaskRuntime → 状态更新 → 前端展示/下载
```
