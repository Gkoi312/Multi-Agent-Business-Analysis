# AI Due Diligence Skill Pack

Analyst role definitions for AI technology company due diligence.

## Files

| File | Role |
|------|------|
| `market-analyst.md` | Product, customers, business model, competitive landscape |
| `tech-analyst.md` | Model capability, data moat, infrastructure, team depth |
| `risk-analyst.md` | Regulation, security, IP, geopolitical exposure |
| `domain-memory.md` | Shared domain knowledge (framework, risk rubric, red flags) |

## Format

Each analyst file is a Markdown document with YAML frontmatter:

```markdown
---
id: ai-market
name: AI Market & Business Analyst
description: ...
source_policy:
  site_hints: [crunchbase.com, ...]
  freshness_hint: balanced
tools: [serper, sec_edgar]
questions:
  - Key question 1?
  - Key question 2?
---
(System prompt / role description in Markdown)
```

## Change policy

- Keep `id` values stable — they're used across the graph to bind analysts to skills
- Tune behavior by editing `questions` and `source_policy.site_hints`
- The Markdown body is the analyst's system prompt — edit it to change analyst behavior
