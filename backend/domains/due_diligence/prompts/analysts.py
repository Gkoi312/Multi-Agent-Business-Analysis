# Due Diligence — analyst generation prompts
from jinja2 import Environment, BaseLoader

jinja_env = Environment(loader=BaseLoader())

CREATE_ANALYSTS_PROMPT = jinja_env.from_string("""
You are designing AI analyst personas for a due diligence project.

[Language] All analyst names, roles, affiliations, and descriptions MUST be written in **Simplified Chinese (简体中文)**.

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
- If **number of analysts > number of skill cards**: at most {{ skill_count }} analysts may have a non-empty `skill_id`, and **do not assign the same skill_id to two people**. All other analysts **must** use an empty string `""` for `skill_id` and describe a complementary angle in prose; **do not** invent or guess skill_ids for "extra" analysts.
- Analysts without a skill card still participate; they do not use pack-specific retrieval templates—do not drop their perspective.

Skill cards:
{{ skill_catalog }}
{% else %}
4. If no skill cards are provided, generate general due diligence roles; set each analyst's `skill_id` to an empty string.
{% endif %}

5. Avoid overlapping roles. Each analyst should have a distinct angle with clear goals and focus areas to help evaluators assess the company.
""")
