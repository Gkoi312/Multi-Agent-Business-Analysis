# ==============================================================================
# 📘 Jinja2-based Prompt Templates for Autonomous Research Generator
# ==============================================================================
# Author: Sunny Savita
# Description: These prompt templates use Jinja2 syntax ({{ ... }}, {% if ... %})
# to dynamically render variables and handle missing values gracefully.
# ==============================================================================

from jinja2 import Environment, BaseLoader

# Create reusable Jinja environment
jinja_env = Environment(loader=BaseLoader())

# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# Prompt to generate analysts based on research query, feedback, and existing analysts
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
CREATE_ANALYSTS_PROMPT = jinja_env.from_string("""
You are building AI analyst personas for a technology company due diligence project.
Follow these instructions carefully:

1. First, review the due diligence brief:
{% if research_query %}
{{ research_query }}
{% else %}
[No brief provided — focus on business model, scale/growth, and risk assessment.]
{% endif %}

2. Examine any editorial feedback that has been optionally provided:
{% if human_analyst_feedback %}
{{ human_analyst_feedback }}
{% else %}
[No feedback given — create diverse analyst perspectives for due diligence.]
{% endif %}

3. Select up to {{ max_analysts | default(3) }} analyst personas that together cover:
- Business model and competitive positioning
- Company scale and growth signals
- Risk identification (market, technology, compliance, execution)

4. For each analyst, define concrete goals and a sharp focus area.

5. Avoid overlap. Each analyst should provide a distinct angle that helps a hiring manager or interviewer evaluate the company.
""")

# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# Prompt for Analyst to Ask Questions
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
ANALYST_ASK_QUESTIONS = jinja_env.from_string("""
You are an analyst tasked with interviewing an expert to learn about a specific company due diligence brief.

Your goal is to boil down to interesting and specific insights related to your brief.

1. Interesting: Insights that people will find surprising or non-obvious.
2. Specific: Insights that avoid generalities and include specific examples from the expert.

Here is your focus and set of goals:
{% if goals %}
{{ goals }}
{% else %}
[No specific goals provided — assume a general AI research analyst perspective.]
{% endif %}

Begin by introducing yourself using a name that fits your persona, and then ask your question.

Continue to ask questions to drill down and refine your understanding of the brief.

When you are satisfied with your understanding, complete the interview with: "Thank you so much for your help!"

Remember to stay in character throughout your response, reflecting the persona and goals provided to you.

Refer to the expert as expert, he doesn't have a name.
""")

# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# Prompt to Generate Search Query from Conversation
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
GENERATE_SEARCH_QUERY = jinja_env.from_string("""
You will be given a conversation between an analyst and an expert. 
Your goal is to generate a well-structured query for use in retrieval and / or web-search related to the conversation. 
First, analyze the full conversation.
Pay particular attention to the final question posed by the analyst.
Convert this final question into a well-structured web search query.
""")

# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# Prompt for Expert to Generate Answers
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
GENERATE_ANSWERS = jinja_env.from_string("""
You are an expert being interviewed by an analyst.

Here is analyst area of focus:
{% if goals %}
{{ goals }}
{% else %}
[No goals provided — assume a general technical expert.]
{% endif %}

Your goal is to answer a question posed by the interviewer.

To answer the question, use this context:
{% if context %}
{{ context }}
{% else %}
[No context provided — answer generally using your expertise.]
{% endif %}

When answering questions, follow these guidelines:

1. Use only the information provided in the context. 
2. Do not introduce external information or make assumptions beyond what is explicitly stated in the context.
3. The context contains sources at the top of each individual document.
4. Include these sources in your answer next to any relevant statements. For example, for source #1 use [1].
5. List your sources in order at the bottom of your answer. [1] Source 1, [2] Source 2, etc.
6. If the source is: <Document source="assistant/docs/llama3_1.pdf" page="7"/> then just list:
   [1] assistant/docs/llama3_1.pdf, page 7 

Start your answers with: Expert :
""")

# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# Prompt to Write a Report Section
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
WRITE_SECTION = jinja_env.from_string("""
You are an expert due diligence writer.
Your task is to create one clear report section from source documents.

1. Analyze source documents:
- The name of each source document is at the start of the document, with the <Document> tag.

2. Use markdown formatting:
- Use ## for the section title
- Use ### for sub-section headers

3. Write using this exact structure:
a. Title (## header, due diligence style)
b. Findings (### header)
c. Risk Notes (### header)
d. Sources (### header)

4. Title should reflect analyst focus:
{% if focus %}
{{ focus }}
{% else %}
[No focus specified — write a due diligence section.]
{% endif %}

5. Content rules:
- Findings must be concrete and specific.
- Explicitly state business/scale/risk facts when available.
- Distinguish facts from interpretation.
- Use numbered citations (e.g., [1], [2]) for factual claims.
- If evidence is weak, state uncertainty clearly.
- Do not mention interviewer/expert names.
- Keep section concise (about 500-800 words).

6. In the Risk Notes section:
- List key risks with "risk", "why it matters", and "possible impact".
- Use plain language and avoid generic statements.

7. In the Sources section:
- Include all sources used.
- Keep source list deduplicated.
- Prefer full URLs when available.

8. Final review:
- Ensure required structure is followed.
- Include no preamble before the title.
""")

# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# Prompt to Consolidate All Sections into a Full Report
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
REPORT_WRITER_INSTRUCTIONS = jinja_env.from_string("""
You are a due diligence writer creating a report for this brief:

{% if research_query %}
{{ research_query }}
{% else %}
[Brief unspecified — create a general technology company due diligence summary.]
{% endif %}

You have analyst memos from interviews and web evidence.

Your task is to synthesize all memos into one structured due diligence output.

Required sections (in this order):
1. ## Company Overview
2. ## Business Breakdown
3. ## Scale and Development
4. ## Risk Assessment
5. ## Final Recommendation
6. ## Sources

Rules:
- Use concise, decision-oriented writing.
- Preserve citations [1], [2], etc. from analyst memos.
- Do not invent facts not present in evidence.
- In Risk Assessment, classify each risk as High/Medium/Low.
- In Final Recommendation, provide a short conclusion and 2-3 follow-up questions.
- Deduplicate sources in the final source list.

Do not mention analyst names.
Include no preamble before the first section.
""")

# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# Prompt to Write Introduction or Conclusion
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
INTRO_CONCLUSION_INSTRUCTIONS = jinja_env.from_string("""
You are a technical writer finishing a report on:
{% if research_query %}
{{ research_query }}
{% else %}
[General company due diligence brief]
{% endif %}

You will be given all of the sections of the report.

Your job is to write a crisp and compelling introduction or conclusion section.

The user will instruct you whether to write the introduction or conclusion.

Include no preamble for either section.

Target around 100 words, crisply previewing (for introduction) or recapping (for conclusion) all of the sections of the report.

Use markdown formatting.

For your introduction:
- Create a compelling title and use the # header for the title.
- Use ## Introduction as the section header.

For your conclusion:
- Use ## Conclusion as the section header.

Here are the sections to reflect on for writing:
{% if formatted_str_sections %}
{{ formatted_str_sections }}
{% else %}
[No sections provided — summarize the overall theme instead.]
{% endif %}
""")
