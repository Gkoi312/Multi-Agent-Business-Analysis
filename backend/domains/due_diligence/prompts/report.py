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

[Fixed outline]These level-2 headings (##) must appear **only** in this order:
1. ## Company Overview
2. ## Business Breakdown
3. ## Scale & Growth
4. ## Risk Assessment
5. ## Final Recommendations
6. ## Sources

[Relationship to memos]
- Fold each chapter's "Key Findings" into the matching sections; consolidate risks and recommendations under "Risk Assessment" and "Final Recommendations".
- "## Sources" must be the **last** level-2 heading; nothing may follow it.

[Global citation numbering (required)]
- Per-analyst [1][2]… are **section-local**; after merge you **must** renumber to a single global [1]…[n]: every [n] in the body must match the nth entry in "## Sources".
- If the same URL or source appears in multiple memos, merge to **one** list entry and use the **same [n]** everywhere.
- List "## Sources" as [1], [2], … in order of **first appearance** in the main body (IEEE-style); body [n] must match row n.

[Sources list completeness — mandatory]
- Scan sections **## Company Overview** through **## Final Recommendations** only; find the **largest** citation number **N** that appears as **[N]** in that range.
- Under **## Sources**, you MUST output **exactly N** entries: one line (or one short paragraph) per number, each starting with **[1]**, **[2]**, … **[N]** on its own line—**no gaps**, no skipping, no collapsing several body citations into a single list row.
- Self-check before finishing: the largest [n] used in the body must equal the count of numbered rows under ## Sources. If the body cites [1] through [5], Sources must show five separate lines starting with [1] … [5], not a single [1] line that omits [2]–[5].

[Writing rules]
- Concise, decision-oriented; do not name analysts.
- Do not invent facts not supported by the memos.
- In "Risk Assessment", each risk must include **Risk level: High / Medium / Low**.
- "Final Recommendations" should be actionable plus 2–3 follow-up questions—not a long "overall conclusion" (that belongs in the separate ## Conclusion step).
- Do not add a preamble before the first section.
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

[Task]Write either an **introduction** or a **conclusion** (one of the two), about 100–200 words.

[Markdown]
- Introduction: first `# Report title` (one line), then `## Introduction`.
- Conclusion: only `## Conclusion` (no extra `#` document title).

[Forbidden] Do not include "## Sources" or a full reference list (the consolidated table lives in the main body under "## Sources").

[Citations] Do **not** use [n] footnotes in intro/conclusion—summarize evidence in words to avoid clashing with global numbering after merge.

[Vs. Final Recommendations]"Final Recommendations" is section 5 of the main body (execution and follow-ups). This `## Conclusion` is a short reader-facing wrap-up—do not paste section 5 verbatim.

Memo chapters for reference:
{% if formatted_str_sections %}
{{ formatted_str_sections }}
{% else %}
[No sections provided—summarize the overall theme instead.]
{% endif %}
""")
