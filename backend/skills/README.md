# Skills (skill packs)

This directory holds executable skill packs **by vertical**.

## Layout

- One subdirectory per vertical, e.g. `ai/`, `manufacturing/`.
- Each subdirectory must contain exactly `skill_pack.yaml` (fixed filename; JSON is not supported).

## Recommended shape

- `role_skills`: analyst-facing cards for the main graph.
- `research_skills`: planner-facing question/research skills.
- `source_policies`: search routing policies for the interview subgraph.
- `mappings`: `role_skill_id -> research_skill_id` links.
- `domain_memory`: optional read-only domain snippets (fallback memory is used if absent).

Identity is **only** the subdirectory name (e.g. `skills/ai/` → `industry_pack` = `ai`). Do **not** put `company_type` or `industry_pack` at the YAML root (load will fail).

## Maintenance

- Keep `id` fields stable.
- When adding skills, update `mappings` so the planner stays aligned.
- Maintain YAML only; do not add parallel skill formats.
