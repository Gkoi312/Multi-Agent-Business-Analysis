# Due Diligence — report assembly prompts
from jinja2 import Environment, BaseLoader

jinja_env = Environment(loader=BaseLoader())

REPORT_WRITER_INSTRUCTIONS = jinja_env.from_string("""
[Role]In the main graph, **merge** parallel analyst memos into **one** decision-ready **main body**.

[Brief]
{% if research_query %}
{{ research_query }}
{% else %}
[No brief—produce a generic target-company due diligence summary.]
{% endif %}

[Input]Multiple memo chapters below (possibly overlapping evidence). You must **merge, dedupe, and resolve conflicts** into **one** coherent narrative—do **not** keep a separate "Company Overview" per analyst.

[Output]Main body only (introduction and reader-level conclusion are generated elsewhere; **do not** output ## Introduction, ## Conclusion, or a second "Conclusion" after "## Final Recommendations").

[Language]You MUST write the entire report in **Simplified Chinese (简体中文)**. All headings, body text, risk labels, and source descriptions must be in Chinese.

[Fixed outline]These level-2 headings (##) must appear **only** in this order, using the Chinese titles below:
1. ## 公司概览
2. ## 业务拆解
3. ## 规模与增长
4. ## 风险评估
5. ## 最终建议
6. ## 信息来源

[Sub-heading format — IMPORTANT]
- Use ONLY `###` (level-3 heading) for sub-sections within each chapter.
- **NEVER** use `**text**` or `**text：**` as a substitute for a heading. Every sub-topic must have its own `###` line.
- Example: write `### 核心产品与竞争力` NOT `**核心产品与竞争力：**`
- Bold (`**...**`) may only be used for inline emphasis on individual words/phrases inside a paragraph—never for a whole line that acts as a section title.

[Relationship to memos]
- Fold each chapter's "Key Findings" into the matching sections; consolidate risks and recommendations under "## 风险评估" and "## 最终建议".
- "## 信息来源" must be the **last** level-2 heading; nothing may follow it.

[Global citation numbering (required)]
- Per-analyst [1][2]… are **section-local**; after merge you **must** renumber to a single global [1]…[n]: every [n] in the body must match the nth entry in "## 信息来源".
- If the same URL or source appears in multiple memos, merge to **one** list entry and use the **same [n]** everywhere.
- List "## 信息来源" as [1], [2], … in order of **first appearance** in the main body (IEEE-style); body [n] must match row n.

[Sources list completeness — mandatory]
- Scan sections **## 公司概览** through **## 最终建议** only; find the **largest** citation number **N** that appears as **[N]** in that range.
- Under **## 信息来源**, you MUST output **exactly N** entries: one line (or one short paragraph) per number, each starting with **[1]**, **[2]**, … **[N]** on its own line—**no gaps**, no skipping, no collapsing several body citations into a single list row.
- Self-check before finishing: the largest [n] used in the body must equal the count of numbered rows under ## 信息来源. If the body cites [1] through [5], Sources must show five separate lines starting with [1] … [5], not a single [1] line that omits [2]–[5].

[Writing rules]
- Concise, decision-oriented; do not name analysts.
- Do not invent facts not supported by the memos.
- In "## 风险评估", each risk must include **风险等级：高 / 中 / 低**.
- "## 最终建议" should be actionable plus 2–3 follow-up questions—not a long "overall conclusion" (that belongs in the separate ## Conclusion step).
- Do not add a preamble before the first section.
- Do NOT use `---` or `***` as section separators anywhere in the output.
""")

INTRO_CONCLUSION_INSTRUCTIONS = jinja_env.from_string("""
[Role]You run in parallel with the main-body writer. Input is the **same** concatenation of analyst memo chapters as `write_report`, **not** the merged main body.

[Brief]
{% if research_query %}
{{ research_query }}
{% else %}
[Generic company due diligence task]
{% endif %}

[Input]Parallel memo chapters below—do not draw conclusions unsupported by them.

[Language]You MUST write in **Simplified Chinese (简体中文)**.

[Task]Write either an **introduction** or a **conclusion** (one of the two), about 100–200 words.

[Markdown]
- Introduction: first `# 报告标题` (one line), then `## 引言`.
- Conclusion: only `## 总结` (no extra `#` document title).

[Forbidden] Do not include "## 信息来源" or a full reference list (the consolidated table lives in the main body under "## 信息来源").

[Citations] Do **not** use [n] footnotes in intro/conclusion—summarize evidence in words to avoid clashing with global numbering after merge.

[Vs. Final Recommendations]"## 最终建议" is section 5 of the main body (execution and follow-ups). This `## 总结` is a short reader-facing wrap-up—do not paste section 5 verbatim.

Memo chapters for reference:
{% if formatted_str_sections %}
{{ formatted_str_sections }}
{% else %}
[No sections provided—summarize the overall theme instead.]
{% endif %}
""")
