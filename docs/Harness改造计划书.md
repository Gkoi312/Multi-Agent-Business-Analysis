# Multi-Agent-Business-Analysis → AI Harness 改造计划书

> 版本：v1.0
> 日期：2026-07-10
> 目标：将当前「多智能体尽调应用」重构为「可插拔领域技能的多智能体编排与评测平台（AI Harness）」

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

**目标：** 不依赖 quanlai-clone，从零为这个项目设计评测体系。

#### 4.6.1 要评测什么

| 维度 | 类型 | 分值 | 描述 |
|------|------|:---:|------|
| `report_completeness` | 静态检查 | 0/1 | 最终报告是否包含所有必需章节 |
| `source_traceability` | 静态检查 | 0/1/2 | 有多少声明能追溯到具体的 [n] 引用 |
| `factual_accuracy` | 混合（静态+LLM） | 0/1/2 | 报告中的事实是否与 fixture 搜索结果一致 |
| `analyst_diversity` | LLM-Judge | 0/1/2 | 分析师角色是否真正多样化 |
| `feedback_responsiveness` | LLM-Judge | 0/1/2 | 重新生成的分析师是否响应了反馈 |

#### 4.6.2 Fixture 设计

```python
# tests/fixtures/case_001_apple.json
{
    "case_id": "case_001_apple",
    "company_name": "Apple Inc.",
    "focus": "AI strategy and financial health",
    "research_query": "Conduct due diligence on Apple Inc., focusing on AI strategy",
    "industry_pack": "ai",
    
    "search_fixtures": {
        # Key → 匹配 agent 生成的 search query（用 fuzzy match）
        "apple ai strategy": {
            "results": [
                {
                    "url": "https://example.com/apple-intelligence-launch",
                    "title": "Apple Intelligence Launches with iOS 19",
                    "content": "In June 2025, Apple announced Apple Intelligence...  on-device AI processing... partnership with OpenAI..."
                },
                // ... 3-5 条高质量模拟结果
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
            "Apple Intelligence",
            "Services revenue",
            "on-device AI"
        ],
        "risk_themes": ["regulatory/DMA", "AI competition", "iPhone dependency"],
        "min_analyst_count": 3,
        "diverse_roles_expected": true
    }
}
```

#### 4.6.3 Scorer 示例

```python
# harness/evaluation/scorers/source_traceability.py

def score_source_traceability(report_text: str, fixture: dict) -> dict:
    """检查报告中每个事实声明是否有来源引用"""
    # 收集报告中所有 [n] 引用
    cited = set(int(m) for m in re.findall(r'\[(\d+)\]', report_text))
    
    # 检查 Sources 章节中对应条目是否存在
    sources_match = re.search(r'## Sources\n(.*?)(?:\n##|\Z)', report_text, re.DOTALL)
    if not sources_match:
        return {"status": "scored", "value": 0, "issues": ["Missing Sources section"]}
    
    sources_text = sources_match.group(1)
    source_entries = len(re.findall(r'^\s*\[\d+\]', sources_text, re.MULTILINE))
    
    # 交叉验证
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

**目标：** 能回答「这个系统到底有多可靠」。

```
任务：
4.1  实现 harness/evaluation/scorer.py（Scorer 基类 + 注册）
4.2  实现 3 个核心 scorer：
     - report_completeness.py（静态）
     - source_traceability.py（静态+LLM）
     - factual_accuracy.py（混合）
4.3  实现 harness/evaluation/fixtures.py（Fixture 加载 + fuzzy query match）
4.4  写 3 条 fixture（Apple / Tesla / Bytedance）
4.5  实现 harness/evaluation/runner.py（单 case + 批量）
4.6  实现 harness/evaluation/reliability.py（CV/σ 分析）
4.7  跑一次 baseline eval：3 case × 3 runs = 9 次实验
4.8  在 Phase 2+3 的改动上跑第二次 eval，对比 baseline
```

**验收标准：**
- 9 次实验全部完成，成功率 > 80%
- 产出第一份可靠性报告（Markdown 格式）
- 至少有一个维度的 CV < 0.3

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
| 搜索结果质量 | ⭐⭐ 原始结果直接喂 | ⭐⭐⭐⭐ 5 阶段清洗管线 |
| 上下文窗口管理 | ❌ 无 | ✅ 精确 token 估算 + 自动触发压缩 |
| 领域可插拔 | ❌ 尽调写死 | ✅ domain adapter 模式 |
| 工具标准化接口 | ❌ Tavily 写死 | ✅ ToolPipeline + Adapter |
| 评测体系 | ❌ 无 | ✅ 5 维度 × N 次重复实验 |
| 可靠性量化 | ❌ 无 | ✅ CV/σ/通过率报告 |
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
