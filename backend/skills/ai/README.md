# AI skill pack

This folder defines the AI vertical skill pack assembled by the main graph and interview subgraph at runtime.

## Files

- `skill_pack.yaml`: configuration loaded at runtime.

## Fields

- `role_skills`: analyst role cards.
- `research_skills` + `mappings`: planner assignments.
- `source_policies`: read by the interview subgraph for search routing.
- `domain_memory`: read-only snippets injected into Q&A and writing.

## Change policy

- Keep `id` values stable over time.
- Prefer changing `source_policies` and `question_templates` when tuning behavior.
- The user-facing flow only needs a company type to load this pack; avoid exposing internal selection knobs.
