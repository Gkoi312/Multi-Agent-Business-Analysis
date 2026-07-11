"""
Skill Registry — loads analyst skill definitions from Markdown files.

Skills are pure Markdown documents.  No YAML frontmatter is required.
One industry pack = one subdirectory under ``backend/skills/`` containing
``*.md`` files.

Format::

    # AI Market & Business Analyst          ← first H1 = skill name

    You are an AI market analyst...         ← body = system prompt for the LLM
    ## Focus
    ...
    ## Key Questions
    ...
    ## Search Guidance
    ...

- **id** = filename stem (e.g. ``market-analyst``)
- **name** = first ``# Heading`` in the file
- **body** = entire file content (passed directly to the LLM)
- ``domain-memory.md`` = shared domain knowledge (``## Heading`` blocks → entries)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def _extract_name(body: str) -> str:
    """Return the first H1 heading text, or empty string."""
    for line in body.split("\n"):
        stripped = line.strip()
        if stripped.startswith("# ") and len(stripped) > 2:
            return stripped[2:].strip()
    return ""


class SkillRegistry:
    """Loads industry skill packs from ``backend/skills/<id>/*.md``."""

    def __init__(self, base_dir: Path | None = None):
        if base_dir is None:
            base_dir = Path(__file__).resolve().parents[2] / "skills"
        self.base_dir = base_dir

    # ------------------------------------------------------------------
    def load_skill_pack(self, industry_pack: str) -> dict[str, Any]:
        """Load all skills and domain memory from an industry pack.

        Returns a dict with keys:
        - ``role_skills``: list of ``{id, name, body}`` dicts
        - ``domain_memory``: list of ``{title, content}`` dicts
        """
        safe_type = (industry_pack or "").strip().lower()
        if not safe_type:
            return {"role_skills": [], "domain_memory": []}

        pack_dir = self.base_dir / safe_type
        if not pack_dir.is_dir():
            return {"role_skills": [], "domain_memory": []}

        role_skills: list[dict[str, Any]] = []
        domain_memory: list[dict[str, Any]] = []

        for md_path in sorted(pack_dir.glob("*.md")):
            text = md_path.read_text(encoding="utf-8").strip()
            if not text:
                continue

            if md_path.stem in ("domain-memory", "README", "readme"):
                if md_path.stem == "domain-memory":
                    domain_memory = self._parse_domain_memory(text)
                # README is documentation, skip it
                continue
            else:
                role_skills.append({
                    "id": md_path.stem,
                    "name": _extract_name(text),
                    "body": text,
                })

        return {
            "role_skills": role_skills,
            "domain_memory": domain_memory,
        }

    # ------------------------------------------------------------------
    @staticmethod
    def _parse_domain_memory(body: str) -> list[dict[str, Any]]:
        """Parse domain-memory.md body into memory entries.

        Each ``## Heading`` becomes a memory entry with ``title`` and ``content``.
        """
        if not body.strip():
            return []

        entries: list[dict[str, Any]] = []
        current_title = ""
        current_lines: list[str] = []

        for line in body.split("\n"):
            if line.startswith("## "):
                if current_title and current_lines:
                    entries.append({
                        "title": current_title.strip(),
                        "content": "\n".join(current_lines).strip(),
                    })
                current_title = line[3:].strip()
                current_lines = []
            elif line.startswith("# "):
                continue  # skip H1
            else:
                current_lines.append(line)

        if current_title and current_lines:
            entries.append({
                "title": current_title.strip(),
                "content": "\n".join(current_lines).strip(),
            })

        return entries
