# Memory & Context Layer

让 Agent 能做多轮深度研究而不因上下文窗口爆炸而截断。核心策略：**逐轮增量式结构化压缩**——每轮 Q&A 结束后提炼事实，后续轮次只看摘要、不重放历史。

## 目录

- [架构概述](#架构概述)
- [目录结构](#目录结构)
- [问题背景](#问题背景)
- [模块说明](#模块说明)
- [数据流](#数据流)
- [关键设计决策](#关键设计决策)
- [测试覆盖](#测试覆盖)
- [Round 2 修复清单](#round-2-修复清单)
- [Round 3 修复清单](#round-3-修复清单)
- [已知限制](#已知限制)


## 架构概述

```
                     ┌────────────────────────────────┐
                     │    Graph State (Checkpoint)     │
                     │    - 原始 messages (不变)        │
                     │    - tool calls & results       │
                     │    - compressed_turns[]         │
                     │    - working_memory dict        │
                     │    - memory_snapshot dict       │
                     │    - running_summary cursor     │
                     │    - search_digest dict         │
                     │    - source_registry dict       │
                     │    - workflow_events[]          │
                     └────────────┬───────────────────┘
                                  │ 投影 (projection only)
                                  │ NEVER mutates checkpoint
                                  ▼
                     ┌────────────────────────────────┐
                     │      ContextAssembler           │
                     │  ┌──────────────────────────┐  │
                     │  │ 1. ToolContextPruner      │  │
                     │  │    清理旧工具结果 (副本)    │  │
                     │  │ 2. 构建 enriched system   │  │
                     │  │    prompt                 │  │
                     │  │ 3. 注入 research summary  │  │
                     │  │    (压缩后的历史轮次)       │  │
                     │  │ 4. 注入 WorkingMemory     │  │
                     │  │ 5. 保留近期原始 messages   │  │
                     │  │ 6. 注入 SearchDigest      │  │
                     │  │ 7. 注入 long-term facts   │  │
                     │  │ 8. 验证 token 预算        │  │
                     │  │ 9. 超限时按优先级收缩      │  │
                     │  │ 10. 超限无法满足→抛异常    │  │
                     │  └──────────────────────────┘  │
                     └────────────┬───────────────────┘
                                  │ ContextAssemblyResult
                                  ▼
                     ┌────────────────────────────────┐
                     │        LLM Input                │
                     └────────────────────────────────┘
```

**核心原则**：Checkpoint State = 真相源，Context Assembler = 投影。永远不在原地修改 canonical messages。


## 目录结构

```
backend/
├── harness/
│   ├── memory/
│   │   ├── README.md              # 本文件
│   │   ├── __init__.py            # 统一导出
│   │   ├── policies.py            # 统一配置 + MemoryDomainConfig
│   │   ├── compressor.py          # IncrementalCompressor
│   │   ├── working_memory.py      # WorkingMemory (MemoryFact)
│   │   ├── context_window.py      # ContextWindowManager
│   │   ├── running_summary.py     # RunningSummaryManager
│   │   ├── history_compactor.py   # HistoryCompactor
│   │   ├── context_editing.py     # ToolContextPruner
│   │   ├── context_assembler.py   # ContextAssembler
│   │   ├── fact_reconciler.py     # FactReconciler (SPDV matching)
│   │   └── search_digest.py       # SearchDigestBuilder (SourceRecord)
│   └── models/
│       ├── __init__.py
│       └── memory.py              # Dataclass + 辅助函数
├── domains/
│   └── due_diligence/
│       ├── memory_config.py       # DUE_DILIGENCE_MEMORY_CONFIG
│       ├── interview.py           # InterviewGraphBuilder (wired)
│       └── schemas.py             # InterviewState (expanded)
└── tests/
    └── harness/
        ├── test_memory_models.py        # 39 tests
        ├── test_memory_core.py          # 91 tests
        ├── test_compressor.py           # 24 tests
        ├── test_memory_regression.py    # 113 tests
        └── test_interview_memory_integration.py  # 24 tests (Round 3)
```


## 模块说明

### policies.py — 统一配置

| 符号 | 类型 | 说明 |
|------|------|------|
| `TokenBudget` | `@dataclass` | 上下文各分区 token 预算（含 `min_current_turn`, `execution_summary`） |
| `CompactionPolicy` | `@dataclass` | 历史压缩触发策略 |
| `ToolPruneConfig` | `@dataclass` | 工具结果清理配置 |
| `ContextWindowConfig` | `@dataclass` | ContextWindowManager 配置 |
| `MemoryDomainConfig` | `@dataclass` | **Round 3** — 领域配置（类别、别名、策略），从 Domain 注入 Harness |
| `VALID_PRIMARY_CATEGORIES` | `frozenset[str]` | 合法 primary category 白名单 |

### models/memory.py — 数据模型

核心模型：

| 模型 | 用途 |
|------|------|
| `MemoryFact` | 结构化事实（含 SPDV、revision_history、生命周期） |
| `FactLedger` | 完整事实集合（all_facts + active_fact_ids + operations） |
| `CoveragePolicy` | 覆盖率策略对象（required_for_full_report / required_for_early_stop） |
| `CompressedTurn` | 单轮压缩结果（facts 为主要真相源） |
| `MergedMemory` | 多轮累积统计快照（只读，从 WorkingMemory 生成） |
| `RunningSummary` | 增量摘要游标 |
| `SearchDigest` | 搜索摘要（含 SourceRecord 注册表） |
| `SourceRecord` | 来源元数据（source_id → URL/标题/检索时间） |
| `ToolPruneResult` | 工具清理结果 |
| `ContextAssemblyResult` | 组装后上下文 |
| `ContextBudgetExceeded` | 超预算异常 |
| `TokenCounter` | 类型安全 token 计数（count_text / count_message / count_messages） |

### context_window.py — Token 估算与管理

- `safe_limit` 公开属性（不再使用私有 `_safe_limit()`）
- 模型名归一化、CJK 字符检测
- `verify_assembly()` 组装后验证

### compressor.py — 逐轮压缩与多轮合并

- **Round 3**: 压缩 prompt 显示仅代码生成的 source ID（S1, S2, …）
- **Round 3**: `_parse_compressed_turn()` 校验 source ID 必须在 registry 中
- **Round 3**: 模型返回 URL 或未知 ID 被拒绝并记录 warning
- **Round 3**: `compress_completed_turn()` 接收 `source_registry` 参数
- **Round 3**: `domain_config` 参数动态构造压缩 prompt 中的类别
- 新压缩 prompt 输出结构化 `facts`（含 SPDV）
- `_parse_compressed_turn()` 将模型输出解析为 MemoryFact 列表
- 向后兼容：fallback 创建 CompressedTurn 时仍支持 `key_findings=`

### working_memory.py — 结构化认知状态

- **facts 是唯一真相源** — coverage、gaps、risks、conflicts 全部动态派生
- `independent_source_count()` 真正去重
- `CoveragePolicy` 统一控制所有覆盖率判断
- `unresolved_conflicts` 是动态属性（从 `conflicts_with` 派生）
- `to_merged_memory()` 生成只读统计快照
- **Round 3**: `ingest_compressed_turn()` 每轮只摄入最新一轮事实（不重新遍历）

### fact_reconciler.py — 事实生命周期（SPDV 匹配）

- **Round 3**: 候选匹配要求 subject **AND** predicate 相同（不再仅 subject+period）
- **Round 3**: `_same_predicate()` 支持 `MemoryDomainConfig.predicate_aliases`
- **Round 3**: `_can_resolve()` 移除 `updated_at` 判断 — 仅依据 evidence_quality、来源、修正语义
- **Round 3**: `_periods_are_distinct()` 明确区分不同时间点 → ADD
- **Round 3**: ADD 永远生成新 UUID，不使用模型提供的 ID
- **Round 3**: `_resolve_spdv()` 返回 ADD 时正确创建新事实
- **Round 3**: UPDATE 保留原 fact_id + revision_history（含 previous_unit/previous_period）
- value 相同/语义等价 → NONE
- 新事实更完整或来源质量更高 → UPDATE（保留原 fact_id + revision_history）
- value 不同且无法裁定 → CONFLICT
- 明确新旧替代 → INVALIDATE
- 严格 ID 验证 — UPDATE/INVALIDATE/CONFLICT 目标不存在则拒绝

### context_editing.py — 工具结果清理

- **修复**：`len(candidates) <= keep` 时不清理（不会清除 ≤ keep 个工具消息）
- `clear_tool_inputs` 正确清理 AI tool-call args
- 不修改 canonical messages

### context_assembler.py — 上下文组装

- **严格预算**：返回 `total_tokens <= safe_limit` 或抛 `ContextBudgetExceeded`
- **Round 3**: 真正接入 Interview Graph — 所有 LLM 节点统一走 `_assemble_llm_messages()`
- system prompt 使用自己的 token budget
- 超大单条消息截断
- search digest、long-term facts、working memory、execution_summary 全部参与缩减
- shrink 后重新计算并验证
- 不删除当前用户消息（min_current_turn 预算）

### search_digest.py — 搜索结果轻量化

- `SourceRecord` 保存 source_id → URL/标题/检索时间
- 模型只看到 source ID；引用阶段代码映射 URL
- **Round 3**: 真正的 token 预算执行 — 逐项添加（query → IDs → snippets → claims）
- **Round 3**: `tokens_after` = 实际序列化 payload 的 token count + 格式开销
- **Round 3**: 保证 `tokens_after <= max_tokens`

### running_summary.py — 增量摘要游标

- 稳定消息 ID（不依赖列表 index；使用 role + content + tool_call_id + tool name）
- 幂等：同一条消息在不同位置有相同 ID
- Tool call 边界保护
- **Round 3**: `max_summary_tokens` 真正强制执行 — 模型输出后验证、重新压缩、截断

### domains/due_diligence/memory_config.py — 领域配置（Round 3 新增）

- `DUE_DILIGENCE_MEMORY_CONFIG`：类别、别名、覆盖率策略的单一真相源
- Harness 组件只接收此配置，不硬编码 "business_model" 等类别


## 数据流

```
Interview Graph 一轮周期 (Round 3)
│
├─ _assemble_llm_messages()  ← ContextAssembler 组装上下文
│   ├── WorkingMemory.from_dict(state["working_memory"])
│   ├── RunningSummary.from_dict(state["running_summary"])
│   ├── compressed_turns → CompressedTurn 对象
│   ├── search_digest → SearchDigest 对象
│   └── ContextAssembler.assemble()
│
├─ ask_question       ← 使用 assembled messages（不是 state["messages"]）
│   └──→ 返回消息带持久化 UUID
│
├─ search_web         ← 使用 assembled messages + 构建 source registry
│   └──→ source_registry (S1 → URL 映射)
│   └──→ search_digest (token-budgeted)
│
├─ generate_answer    ← 使用 assembled messages（含 search digest）
│   └──→ 返回消息带持久化 UUID
│
├─ compress           ← compress_completed_turn(source_registry=...)
│   └──→ compressed_turns[] 累积（source_ids 为 S1, S2）
│
├─ update_memory      ← WorkingMemory.ingest_compressed_turn(最新一轮)
│   └──→ working_memory = WorkingMemory.to_dict()  ← SOLE TRUTH SOURCE
│   └──→ memory_snapshot = MergedMemory.to_dict()  ← read-only stats
│
├─ compact_history    ← should_compact_history() → RunningSummary 更新
│   └──→ running_summary cursor 更新（仅摘要旧消息）
│
└─ _should_continue   ← WorkingMemory.from_dict(state["working_memory"])
    └──→ has_sufficient_coverage() (CoveragePolicy)
```


## 关键设计决策

| # | 决策 | 理由 |
|---|------|------|
| 1 | 压缩用 cheap model | 压缩是提取不是推理 |
| 2 | MergedMemory 从 WorkingMemory 生成 | 避免双重计数 |
| 3 | facts 是唯一真相源 | coverage/gaps/risks/conflicts 全部动态派生 |
| 4 | 不修改 checkpoint | 原始数据永远可追溯 |
| 5 | 占位符替换，不删除 | 保持 AI↔ToolMessage 对应关系 |
| 6 | CONFLICT 标记而非静默删除 | 保留不确定信息 |
| 7 | 压缩失败非致命降级 | 不阻断 interview 流程 |
| 8 | SPDV 匹配取代 token Jaccard | subject/predicate/value 精确对比 |
| 9 | FactLedger 保存完整历史 | invalidated/superseded 不丢失 |
| 10 | 严格 ID 验证 | 模型生成的 ID 必须通过 id_mapping |
| 11 | TokenCounter 三分接口 | count_text / count_message / count_messages |
| 12 | 超预算必须抛异常 | 不允许静默返回超预算结果 |
| 13 | 代码生成 source ID (S1, S2) | 模型只看到短 ID，不生成 URL |
| 14 | 领域配置注入 Harness | Harness 不知道 "business_model" |
| 15 | 每轮只摄入最新一轮事实 | WorkingMemory 增量更新，不重新遍历 |
| 16 | updated_at 不用于冲突解决 | 写入时间 ≠ 证据质量 |


## 测试覆盖

```
291 passed in 1.06s

tests/harness/
├── test_memory_models.py              (39 tests)
├── test_memory_core.py                (91 tests)
├── test_compressor.py                 (24 tests)
├── test_memory_regression.py          (113 tests — Round 2 回归)
└── test_interview_memory_integration.py (24 tests — Round 3 集成)
```

实际运行命令及结果：

```text
$ python -m pytest backend/tests/harness/ -q
291 passed in 1.06s
```


## Round 2 修复清单

### 一、Token Counter 接口 ✓
- `TokenCounter` 拆分 `count_text()` / `count_message()` / `count_messages()`
- `HistoryCompactor.should_compact()` 不再抛出 TypeError

### 二、ToolContextPruner 保留逻辑 ✓
- 正确行为：候选 ≤ keep 时 0 清理
- `clear_tool_inputs=False` 不修改 AI tool-call args
- `clear_tool_inputs=True` 正确清理但保留 ID/name

### 三、无 ID 消息摘要幂等性 ✓
- `_stable_message_id()` 不再依赖列表 index
- 使用 role + content + tool_call_id + tool name + occurrence_key

### 四、只摘要旧消息 ✓
- `_split_old_recent()` 分离 old（摘要）和 recent（保留原始）
- 保持完整边界：Q&A 轮次、tool call 并行组

### 五、ContextAssembler 严格预算 ✓
- `assemble()` 返回 `total_tokens <= safe_limit` 或抛 `ContextBudgetExceeded`
- `safe_limit` 公开属性

### 六、CompressedTurn 结构化事实 ✓
- `facts: list[MemoryFact]` 为主要真相源
- 含 SPDV 字段、source_ids、evidence_quality、confidence
- `key_findings` 是派生兼容属性

### 七、删除双重事实统计 ✓
- `MergedMemory` 从 `WorkingMemory` 生成（只读快照）
- facts 是唯一真相源

### 八、CoveragePolicy 策略对象 ✓
- `required_for_full_report` / `required_for_early_stop` 明确两层
- `independent_source_count()` 真正去重
- low-quality facts 不计入 coverage

### 九、FactReconciler SPDV 匹配 ✓
- subject + predicate + period 候选查找
- value 比较判断 ADD/UPDATE/NONE/CONFLICT/INVALIDATE

### 十、FactLedger 保留完整历史 ✓
- `all_facts` 包含 invalidated/superseded
- `active_fact_ids` 当前活跃子集
- UPDATE 保留原 fact_id + revision_history

### 十一、严格模型 ID 验证 ✓
- `raw_id not in id_mapping` → 拒绝操作
- 禁止 fallback 到模型生成的 ID

### 十二、冲突状态动态派生 ✓
- `unresolved_conflicts` 是动态属性（从 `conflicts_with` 生成）
- 冲突解决后自动更新

### 十三、SearchDigest SourceRecord ✓
- `SourceRecord` 保存 source_id/url/title/retrieved_at
- `tokens_after` 统计完整开销

### 十四、序列化补全 ✓
- `to_dict/from_dict` 覆盖所有模型

### 十五、文档与测试声明真实 ✓
- 真实命令 `pytest backend/tests/harness/ -q` 输出 `291 passed`


## Round 3 修复清单

### 一、ContextAssembler 真正接入 Interview Graph ✓
- 所有 LLM 节点（`_generate_question`, `_search_web`, `_generate_answer`, `_write_section`）统一走 `_assemble_llm_messages()`
- 不再直接 `self.llm.invoke([SystemMessage(...)] + state["messages"])`
- ContextBudgetExceeded 捕获 + 降级策略（system prompt only）
- 不静默退回全量历史消息

### 二、真正执行历史压缩 ✓
- 新增 `_compact_history` 节点：在 `update_memory` 后执行
- `should_compact_history()` → `compact_history()` → 更新 `running_summary`
- 只摘要旧消息，recent messages 保留原文
- running_summary 可序列化，相同 checkpoint 重试不重复调用

### 三、WorkingMemory 是唯一事实真相源 ✓
- `_update_memory()` 创建 WorkingMemory，仅摄入最新一轮事实
- 返回 `working_memory` = WorkingMemory.to_dict()
- 返回 `memory_snapshot` = MergedMemory.to_dict()（只读快照）
- `_should_continue()` 读取 WorkingMemory.has_sufficient_coverage()
- coverage、gaps、risks、conflicts 全部从 WorkingMemory.facts 动态派生
- `merge_compressed()` 改为兼容层，内部通过 WorkingMemory.ingest_compressed_turn()

### 四、打通真实来源注册表 ✓
- 搜索阶段生成稳定 source ID（S1, S2, …）
- SourceRecord 保存 source_id → URL/标题/检索时间
- compressor prompt 只显示代码生成的 source ID
- `_parse_compressed_turn()` 校验每个 source ID 在 registry 中
- 不在 registry 中的 source ID 删除并记录 validation warning
- URL 在 source_ids 中被拒绝
- source_registry 跨轮次累积（不覆盖历史来源）

### 五、领域配置从 Harness 移出 ✓
- 新增 `MemoryDomainConfig`：categories, category_descriptions, coverage_policy, predicate_aliases, risk_categories
- `domains/due_diligence/memory_config.py`：DUE_DILIGENCE_MEMORY_CONFIG
- Harness 不知道 "business_model" 等具体类别
- `IncrementalCompressor` 接收 `domain_config` 动态构造压缩 prompt
- `FactReconciler` 接收 `domain_config` 获取 predicate aliases

### 六、FactReconciler 候选匹配修复 ✓
- 候选匹配要求 subject **AND** predicate 相同（不再仅 subject+period）
- 新增 predicate alias 机制（可配置，Domain 注入）
- `_periods_are_distinct()` 识别不同年份 → ADD（不冲突）
- `_can_resolve()` 移除 `updated_at` 判断
- `_resolve_spdv()` 返回 ADD 时正确创建新事实

### 七、Period 处理修复 ✓
- 不同 period 默认 ADD（两个时间点事实）
- 只有明确修正语义才 UPDATE/INVALIDATE
- `_periods_are_distinct()` 正则匹配 20xx 年份

### 八、冲突判定修复 ✓
- 不再使用 `updated_at` 判断新事实更可靠
- 冲突解决仅依据：evidence_quality, source authority, explicit correction
- 同等质量不同来源 → CONFLICT
- 只有 evidence_quality 明显更高 + 来源更权威 + 明确修正 → INVALIDATE

### 九、UPDATE 语义保持 ✓
- 保留原 fact_id
- revision_history 记录 previous_text/value/unit/period/evidence_quality/source_ids
- supersedes 保持为空
- 不混用 supersedes

### 十、ADD 行为修复 ✓
- ADD 永远由代码生成新 UUID
- 模型返回的 ID 不用于 ADD
- UPDATE/INVALIDATE/CONFLICT 目标不存在 → 拒绝

### 十一、消息 ID 持久化 ✓
- `_generate_question` 和 `_generate_answer` 为返回消息分配 UUID
- `model_copy(update={"id": str(uuid.uuid4())})` 保证持久化
- `_ensure_message_ids()` 工具方法

### 十二、max_summary_tokens 真正执行 ✓
- `_generate_summary()` 后检查 token count
- 超限 → 重新压缩 → 截断
- 同步和异步路径行为一致
- `_truncate_to_token_boundary()` 在自然边界截断

### 十三、SearchDigest token 预算真正执行 ✓
- 按优先级逐项添加：query → source IDs → snippets → claims
- 每次添加前验证 projected tokens ≤ max_tokens
- `tokens_after` = 实际序列化 payload token count
- 保证 `tokens_after <= max_tokens`

### 十四、InterviewState 完善 ✓
- 新增：`memory_snapshot`, `running_summary`, `search_digest`, `source_registry`
- `working_memory` 不被 list reducer 追加
- `source_registry` 跨轮次累积

### 十五、真实集成测试 ✓
- `test_interview_memory_integration.py` — 24 tests
- 场景覆盖：ContextAssembler 调用、重复事实、WorkingMemory 持久化、来源闭环、period/predicate 区分、冲突检测、token 预算、消息 ID、checkpoint 不修改、提前停止条件

### 十六、弱断言修复 ✓
- `test_keep_recent_tool_results` 的 `pass` 替换为真实断言
- 不同 period 测试断言两个事实均保留
- 测试验证状态和语义，不仅验证"不报错"

### 十七、README 与真实测试一致 ✓
- 真实命令输出 `291 passed in 1.06s`


## 已知限制

| 限制 | 影响 | 缓解 |
|------|------|------|
| SPDV 字段需模型或代码填充 | 无 SPDV 时回落 text overlap 匹配 | 压缩 prompt 已指示模型输出 SPDV |
| ToolContextPruner 依赖 `langchain_core.messages.ToolMessage` | 与 langchain 耦合 | 可抽象为 Protocol |
| HistoryCompactor 需调用方管理 cursor | 需在 state 中新增字段 | InterviewState 已含 `running_summary` 字段 |
| 中文关键词匹配基于白名单 | 部分中文事实可能分类不准确 | `other` fallback 兜底 |
| 消息 ID 持久化依赖 `model_copy` | LangChain 不可变对象需复制 | 已通过 `model_copy(update={"id": ...})` 处理 |
| 压缩 prompt 中的 source_registry_block 受 prompt 长度限制 | 超多来源时可能截断 | 限制最多 2000 搜索结果进行压缩 |
