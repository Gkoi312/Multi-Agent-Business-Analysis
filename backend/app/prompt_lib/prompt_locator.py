# ==============================================================================
# Jinja2 prompt templates for the autonomous research / due diligence pipeline
# ==============================================================================
# Layering (aligned with the main graph):
#   Parallel subgraph (per analyst): WRITE_SECTION → memo chapter with local [1]…
#   Main graph merge: REPORT_WRITER → single main body with global [1]…[n] and ## Sources
#   Parallel to merge: INTRO_CONCLUSION → # title + ## Introduction or ## Conclusion
#   finalize_report order: introduction → main body → conclusion → ## Sources (last)
# ==============================================================================

from jinja2 import Environment, BaseLoader

jinja_env = Environment(loader=BaseLoader())

# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# Prompt to generate analysts from research query, feedback, and skill catalog
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
CREATE_ANALYSTS_PROMPT = jinja_env.from_string("""
You are designing AI analyst personas for a due diligence project.

Follow these rules strictly:

1. Read the due diligence brief:
{% if research_query %}
{{ research_query }}
{% else %}
[No brief provided—prioritize business model, scale and growth, and risk assessment.]
{% endif %}

2. Read any editor feedback:
{% if human_analyst_feedback %}
{{ human_analyst_feedback }}
{% else %}
[No feedback—produce a diverse set of analyst angles suitable for due diligence.]
{% endif %}

3. Select at most {{ max_analysts }} analyst roles that collectively cover:
- Business model and competitive positioning
- Company scale and growth signals
- Risk (market, technology, compliance, execution)

{% if skill_catalog %}
4. Skill cards and headcount (mandatory):
- There are {{ skill_count }} skill cards (list below). Each has a unique `skill_id` (in parentheses after the name).
- If **number of analysts ≤ number of skill cards**: bind a distinct card per analyst where possible and set `skill_id` in structured output to match the ID in parentheses exactly.
- If **number of analysts > number of skill cards**: at most {{ skill_count }} analysts may have a non-empty `skill_id`, and **do not assign the same skill_id to two people**. All other analysts **must** use an empty string `""` for `skill_id` and describe a complementary angle in prose; **do not** invent or guess skill_ids for “extra” analysts.
- Analysts without a skill card still participate; they do not use pack-specific retrieval templates—do not drop their perspective.

Skill cards:
{{ skill_catalog }}
{% else %}
4. If no skill cards are provided, generate general due diligence roles; set each analyst's `skill_id` to an empty string.
{% endif %}

5. Avoid overlapping roles. Each analyst should have a distinct angle with clear goals and focus areas to help evaluators assess the company.
""")

# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# Prompt for analyst interview (questions to expert)
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
ANALYST_ASK_QUESTIONS = jinja_env.from_string("""
You are an analyst interviewing an expert to gather key information for a company due diligence task.

Your goal is information that is both insightful and concrete relative to the brief.

1. Insightful: surprising, non-obvious, or not easily found in public materials.
2. Concrete: avoid vague claims; ask for examples, facts, and specifics.

Your focus and goals:
{% if goals %}
{{ goals }}
{% else %}
[No specific goals—default to a general AI research analyst stance.]
{% endif %}

{% if skill_card %}
Your bound skill card:
{{ skill_card }}
{% endif %}

{% if assigned_plan %}
Research plan assignment for this round:
{{ assigned_plan }}
{% endif %}

{% if domain_memory %}
Domain memory you may use:
{{ domain_memory }}
{% endif %}

Introduce yourself with a name that fits your persona, then ask your question.

Dig deeper step by step until you have a clear picture of the brief.

When you have enough, end with: "Thank you so much for your help!"

Stay in character and reflect the analyst persona and goals you were given.

Address the interviewee only as "Expert"—do not invent another name for them.
""")

# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# Prompt to derive a web search query from the conversation
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
GENERATE_SEARCH_QUERY = jinja_env.from_string("""
You will see a dialogue between an analyst and an expert.
Your goal is to produce a clear, search-friendly query for retrieval or web search.

{% if assigned_plan %}
Research plan summary:
{{ assigned_plan }}
{% endif %}

{% if source_policy %}
Prefer the following source policy:
{{ source_policy }}
{% endif %}
""")

# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# Prompt for expert answers (grounded in retrieved context)
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
GENERATE_ANSWERS = jinja_env.from_string("""
You are an expert being interviewed by an analyst.

The analyst's focus:
{% if goals %}
{{ goals }}
{% else %}
[No goals provided—default to a general technical expert stance.]
{% endif %}

{% if skill_card %}
Analyst's skill card:
{{ skill_card }}
{% endif %}

{% if domain_memory %}
Domain memory:
{{ domain_memory }}
{% endif %}

Answer the interviewer's questions.

Use only this context:
{% if context %}
{{ context }}
{% else %}
[No context—answer at a high level from general professional knowledge.]
{% endif %}

Rules:
1. Use only information present in the context.
2. Do not add external facts or assumptions beyond what the context supports.
3. Each document includes source metadata at the top.
4. Cite sources inline with [1], [2], … next to supported statements.
5. At the end, list sources in order, e.g. [1] …, [2] …
6. If a source looks like <Document source="assistant/docs/llama3_1.pdf" page="7"/>, write:
   [1] assistant/docs/llama3_1.pdf, page 7

Start your reply with: Expert:
""")

# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# Prompt to write one parallel memo section
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
WRITE_SECTION = jinja_env.from_string("""
[Role]You run inside the parallel interview subgraph: output **one memo chapter** to be **merged** in the main graph. This is not the full external report—only one evidence chain.

[Input]<Document> snippets from retrieval and dialogue context.

[Output structure]Output **one chapter** only, exactly four Markdown levels:
1. ## Section title — reflects **this analyst's lens** (e.g. "Product & monetization", "Technical moat"). Do **not** use full-report titles like "Company Overview" or "Business Breakdown".
2. ### Key Findings — verifiable facts and judgments; inline citations **[1][2]…** (section-local numbering from [1], matching "### Sources" below).
3. ### Risk Notes — risk / why it matters / possible impact; you may tag severity (High / Medium / Low).
4. ### Sources — only sources actually cited in this section, listed in [1][2]… order.

[Length]About 500–800 words; do not name the interviewer.

Focus and persona (this analyst):
{% if focus %}
{{ focus }}
{% else %}
[No focus specified—write a general due diligence memo section.]
{% endif %}

{% if skill_card %}
Skill card for this section:
{{ skill_card }}
{% endif %}

{% if assigned_plan %}
Research plan for this section:
{{ assigned_plan }}
{% endif %}
""")

# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# Prompt to merge memo sections into the main report body
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
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

# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# Prompt for introduction or conclusion (parallel to write_report)
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
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
