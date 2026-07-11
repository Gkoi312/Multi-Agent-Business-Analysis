# Tools Layer

外部分发搜索、浏览请求，对返回结果做清洗、去重、结构化，最终输出 LLM-ready 的文档块。

## 目录结构

```
tools/
├── registry.py          # ToolRegistry：线程安全的工具注册/发现
├── pipeline.py          # ToolPipeline + ProcessingStage + StageTrace
├── browse/
│   └── jina_reader.py   # Jina Reader（URL → Markdown）
└── search/
    ├── base.py          # SearchDocument / SearchQuery / SearchTool 协议
    ├── tavily.py        # Tavily 适配器
    ├── brave.py         # Brave Search 适配器
    ├── bocha.py         # Bocha（中文搜索）适配器
    ├── github_repos.py  # GitHub 仓库搜索适配器
    └── cleaner.py       # 9 个 ProcessingStage + 预置 pipeline
```

## 核心概念

### SearchDocument — 唯一内部数据类型

整个 pipeline 只认 `SearchDocument`，不再出现 `dict` / `SearchResult` 混用：

```
url          → 原始 URL（provider 返回的）
canonical_url → 去跟踪参数 + 去 fragment 后的规范 URL
title        → 标题
raw_content  → 原始正文 —— 任何 Stage 都不得修改
clean_content → 清洗后的正文（去 HTML、合并空白）
agent_content → LLM 提取/总结的内容（预留）
published_date → ISO 日期
source_type  → "web" | "news" | "company" | "academic"
provider     → "tavily" | "brave" | "bocha" | "github"
provider_score → provider 原始相关性分（可为 None）
metadata     → 自由扩展的键值对（repo_metrics、formatted 等）
structured   → 结构化提取结果（numbers、dates、entities、evidence、sentiment）
scores       → 各维度评分（relevance、quality、freshness …）
warnings     → 处理过程中的非致命告警
dropped_reason → 非空表示应被下游排除；为空表示有效
raw          → provider 原始响应（调试用）
```

### ProcessingStage — 单一职责的变换单元

```python
class ProcessingStage(ABC):
    name: str                        # kebab-case，用于 trace

    def __call__(
        self,
        data: list[SearchDocument],
        ctx: ToolContext,
    ) -> list[SearchDocument]: ...
```

- 输入和输出都是 `list[SearchDocument]`
- **不修改** `raw_content`
- 丢文档通过设置 `dropped_reason` 标记，而不是静默移除
- 异常默认 fail-open（保留文档 + 写 warning）

### ToolPipeline — 编排器

```python
pipeline = ToolPipeline([
    CanonicalizeURLStage(),
    CleanTextStage(),
    ExactDeduplicateStage(),
    NearDuplicateStage(),
    RelevanceScoreStage(),
    QualityScoreStage(),
    StructureFactsStage(),
    OutputGuardStage(),
    FormatDocumentStage(),
])

cleaned, trace = pipeline.run_with_trace(raw_docs, ctx)
# cleaned: list[SearchDocument]
# trace:   list[StageTrace]（每阶段耗时、计数、告警数、丢弃数）
```

## 数据流

```
SearchTool.search()        # adapter 返回 list[SearchDocument]（raw_content 有值）
      │
      ▼
CanonicalizeURLStage       # 去 utm_* / gclid / fbclid / fragment → canonical_url
      │
      ▼
CleanTextStage             # 去 HTML → clean_content；太短的标记 dropped
      │
      ▼
ExactDeduplicateStage      # 相同 canonical_url 只保留第一个
      │
      ▼
NearDuplicateStage         # 内容指纹 + 标题 bigram Jaccard（中文用字符级 bigram）
      │
      ▼
RelevanceScoreStage        # 关键词密度打分 + 批量 LLM 打分（使用 target_entity & target_focus）
      │
      ▼
QualityScoreStage          # 4 维加权评分（domain / fact_density / seo_filler / length）
      │
      ▼
StructureFactsStage        # 提取数字、日期、实体、情感、证据句 → structured
      │
      ▼
OutputGuardStage           # XML 转义、prompt injection 检测、字数预算
      │
      ▼
FormatDocumentStage        # 渲染 <Document> XML → metadata["formatted"]
      │
      ▼
domain 层消费              # doc.metadata["formatted"] / doc.clean_content / doc.structured
```

## 预置 Pipeline

```python
from harness.tools.search.cleaner import SEARCH_PIPELINE_BASIC, SEARCH_PIPELINE_FULL

# 轻量：canonicalize → clean → exact_dedup → format
SEARCH_PIPELINE_BASIC  # 4 stages

# 完整：9 stages（上面列出的全部）
SEARCH_PIPELINE_FULL
```

## 各 Stage 详解

### 1. CanonicalizeURLStage

| 属性 | 值 |
|------|-----|
| name | `canonicalize_url` |
| 修改 | `canonical_url` |
| 丢弃 | 无 |

移除的 tracking 参数：`utm_source`, `utm_medium`, `utm_campaign`, `utm_term`, `utm_content`, `gclid`, `gclsrc`, `fbclid`, `msclkid`, `dclid`, `twclid`, `igshid`, `mc_cid`, `mc_eid`, `_ga`, `_gl`, `_hsenc`, `_hsmi`, `ref`, `ref_src`, `ref_url`

同时移除 URL fragment（`#section`）。

### 2. CleanTextStage

| 属性 | 值 |
|------|-----|
| name | `clean_text` |
| 修改 | `title`, `clean_content` |
| 丢弃 | `clean_content` < `min_content_length`（默认 50 字符） |

- 使用 `html.unescape` + 正则去标签（**不用** BeautifulSoup 解析完整 HTML）
- 合并连续空白符
- **绝不修改** `raw_content`

### 3. ExactDeduplicateStage

| 属性 | 值 |
|------|-----|
| name | `exact_dedup` |
| 修改 | `dropped_reason = "duplicate_url"` |
| 丢弃 | 已存在于 `seen` 集合的 `canonical_url` |

First-wins 策略：第一个出现的保留，后续相同 canonical_url 标记为丢弃。

### 4. NearDuplicateStage

| 属性 | 值 |
|------|-----|
| name | `near_dedup` |
| 修改 | `dropped_reason = "near_duplicate"` |
| 丢弃 | 内容指纹相似 > 85% 或标题 bigram Jaccard > 80% |

两步检测：
1. **内容指纹** — 取 5 个最小 hash 的 word 3-gram，比较 Jaccard
2. **标题 bigram Jaccard** — 中文用字符 bigram，英文用 word bigram

保留 `clean_content` 更长的那篇。

### 5. RelevanceScoreStage

| 属性 | 值 |
|------|-----|
| name | `relevance` |
| 修改 | `scores["relevance"]`、`dropped_reason = "low_relevance"` |
| 丢弃 | 得分 < `score_threshold`（默认 0.15） |

- 使用 `target_entity` 和 `target_focus` 两个维度打分
- 标题命中加权 0.3，正文用滑动窗口密度
- 边界文档（0.0~0.4）批量调用一次 LLM 二次确认（不是每篇一次）
- 没有 target 时全部给 0.5 中性分

### 6. QualityScoreStage

| 属性 | 值 |
|------|-----|
| name | `quality` |
| 修改 | `scores["quality"]`、`dropped_reason = "low_quality"` |
| 丢弃 | 加权得分 < `score_threshold`（默认 0.20） |

四维加权评分（**不采用一项失败立即删除**）：

| 维度 | 权重 | 检测方式 |
|------|------|----------|
| domain | 30% | 已知垃圾域名列表匹配 |
| fact_density | 30% | 数字、日期、命名实体密度 |
| seo_filler | 25% | SEO 填充词占比（中英文） |
| length | 15% | 词数分段评分 |

### 7. StructureFactsStage

| 属性 | 值 |
|------|-----|
| name | `structure` |
| 修改 | `structured` |

提取到 `structured` 的内容：

```python
{
    "numbers":   ["$5 billion", "40%", ...],   # 金额、百分比、计数（去重 top 10）
    "dates":     ["2025-03-15", "Q1 2025", ...], # 日期（去重 top 5）
    "entities":  ["OpenAI", "Sam Altman", ...],   # 大写/中文多词短语（top 10）
    "sentiment": "positive" | "negative" | "neutral",
    "char_count": 1234,
    "evidence":  ["Revenue hit $5 billion in fiscal year 2025.", ...],
                 # 包含提取数字的原始句子（最多 3 句），用于事实追溯
}
```

### 8. OutputGuardStage

| 属性 | 值 |
|------|-----|
| name | `output_guard` |
| 修改 | `clean_content`（转义后）、`title`（转义后）、`dropped_reason` |
| 丢弃 | prompt injection 命中（`injection_action="drop"` 时） |

三步防护：
1. **Prompt Injection 检测** — 8 条正则覆盖 `ignore previous instructions`、`<|im_start|>`、`[system]` 等模式
2. **XML 转义** — `&` `<` `>` `"` `'` → `&amp;` `&lt;` `&gt;` `&quot;` `&apos;`
3. **字符预算** — 超过 `max_content_chars`（默认 8000）/ `max_title_chars`（默认 300）时截断并告警

`injection_action` 可选值：
- `"drop"` — 标记 `dropped_reason = "prompt_injection"`（默认）
- `"warn"` — 仅添加 warning，不丢弃

### 9. FormatDocumentStage

| 属性 | 值 |
|------|-----|
| name | `format` |
| 修改 | `metadata["formatted"]` |

输出格式：

```xml
<Document index="1" href="https://..." title="Title">
  <Scores relevance="0.85" quality="0.72"/>
  <Numbers>$5 billion, 40%</Numbers>
  <Dates>2025-03-15</Dates>
  <Entities>OpenAI, Microsoft</Entities>
  <Sentiment>positive</Sentiment>
  <Evidence>Revenue hit $5 billion.</Evidence>
  <Content>文章正文（已 XML 转义）...</Content>
  <!-- warnings: content_truncated; near_duplicate_of:https://... -->
</Document>
```

所有用户数据均被 XML 转义，包括 `href`、`title`、`Content` 和 `Evidence`。

## SearchTool 协议与适配器

所有搜索后端实现同一个协议：

```python
class SearchTool(ABC):
    name: str

    def search(self, query: SearchQuery, **kwargs) -> list[SearchDocument]: ...
    def health_check(self) -> bool: ...
```

| 适配器 | name | 特点 |
|--------|------|------|
| `TavilyAdapter` | `tavily` | 默认；通过 LangChain 封装调用 |
| `BraveSearchAdapter` | `brave` | 原生 `site:` 过滤支持 |
| `BochaAdapter` | `bocha` | 中文内容覆盖更好 |
| `GitHubReposAdapter` | `github` | 仓库搜索，附带 RepoMetrics |

每个适配器的 `SearchDocument` 都填充了 `provider`、`provider_score`（如有）和 `raw`（原始响应）。

## ToolRegistry

```python
from harness.tools.registry import TOOL_REGISTRY

TOOL_REGISTRY.register_search(TavilyAdapter())
TOOL_REGISTRY.register_browse("jina", JinaReader())

tool = TOOL_REGISTRY.get_search("tavily")
tool = TOOL_REGISTRY.get_best_search(["news"])  # 优先匹配 source_type
```

模块级单例 `TOOL_REGISTRY`，domain 层和 pipeline 共用。线程安全（`threading.Lock`）。

## StageTrace

```python
@dataclass
class StageTrace:
    stage: str           # stage.name
    duration_ms: int     # 耗时（毫秒）
    input_count: int     # 进入该 stage 的文档数
    output_count: int    # 离开该 stage 的文档数
    reduction_pct: float # 丢弃比例
    warning_count: int   # 累积告警数
    dropped_count: int   # 标记丢弃的文档数
```

## 添加新 Stage

```python
class MyStage(ProcessingStage):
    name = "my_stage"

    def __call__(
        self, data: list[SearchDocument], ctx: ToolContext
    ) -> list[SearchDocument]:
        for doc in data:
            if doc.dropped_reason:
                continue  # 尊重前序 stage 的丢弃决定
            # ... 处理 doc ...
            if problem:
                doc.dropped_reason = "my_reason"
                doc.warnings.append("my_warning")
        return data
```

## 添加新搜索适配器

```python
class MyAdapter(SearchTool):
    name = "my_search"

    def search(self, query: SearchQuery, **kwargs) -> list[SearchDocument]:
        results = []
        for item in self._call_api(query):
            results.append(SearchDocument(
                url=item["url"],
                title=item["title"],
                raw_content=item["snippet"],
                source_type=query.source_type,
                provider=self.name,
                raw=item,
            ))
        return results[: query.max_results]
```

## 设计约束

1. **raw_content 不可变** — 任何 Stage 不得修改它，方便调试和重处理
2. **唯一内部类型** — pipeline 只认 `SearchDocument`，不兼容 dict
3. **不解析完整 HTML** — 去标签用正则，不用 BeautifulSoup（性能和依赖考虑）
4. **中文 bigram** — 标题去重用字符级 bigram，不依赖空格分词
5. **质量评分不硬删** — 多维度加权，一项差不会直接毙掉
6. **LLM 批量调用** — relevance filter 一次调 LLM 处理多篇边界文档
7. **外部内容不可信** — OutputGuardStage 对 LLM 输入做转义和注入检测
8. **证据可追溯** — StructureFactsStage 保留原始证据句
