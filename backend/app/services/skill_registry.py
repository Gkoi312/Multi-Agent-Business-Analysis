from pathlib import Path
from typing import Any
import yaml


class SkillRegistry:
    """Loads industry skill packs from ``backend/skills/<id>/skill_pack.yaml`` only."""

    def __init__(self, base_dir: Path | None = None):
        if base_dir is None:
            base_dir = Path(__file__).resolve().parents[2] / "skills"
        self.base_dir = base_dir

    def list_industry_packs(self) -> list[str]:
        """Subdirectory names under ``skills/`` that contain ``skill_pack.yaml``."""
        if not self.base_dir.exists():
            return []
        out: list[str] = []
        for p in self.base_dir.iterdir():
            if not p.is_dir():
                continue
            if (p / "skill_pack.yaml").exists():
                out.append(p.name.lower())
        return sorted(set(out))

    def load_skill_pack(self, industry_pack: str) -> dict[str, Any]:
        safe_type = (industry_pack or "").strip().lower()
        if not safe_type:
            return {}
        yaml_path = self.base_dir / safe_type / "skill_pack.yaml"
        try:
            if yaml_path.exists():
                loaded = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
                if not isinstance(loaded, dict):
                    return {}
                for forbidden in ("company_type", "industry_pack"):
                    if forbidden in loaded:
                        raise ValueError(
                            f"{yaml_path}: must not set top-level {forbidden!r}; "
                            "pack id is only the parent directory name."
                        )
                return loaded
            return {}
        except ValueError:
            raise
        except Exception:
            return {}
