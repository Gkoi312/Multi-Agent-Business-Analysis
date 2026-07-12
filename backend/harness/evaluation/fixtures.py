"""
Evaluation Fixture loader.

Loads JSON fixture files for component, integration, and end-to-end eval.
Fixtures are stored under backend/tests/fixtures/ by layer.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _fixture_root() -> Path:
    """Resolve the fixture root relative to this project."""
    # Go up from harness/evaluation/ → harness/ → backend/ → tests/fixtures/
    return Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures"


def load_fixture(relative_path: str) -> dict[str, Any]:
    """Load a single JSON fixture.

    Args:
        relative_path: Path relative to tests/fixtures/, e.g. 'compression/case_001.json'.

    Returns:
        Parsed fixture dict.

    Raises:
        FileNotFoundError: If the fixture file does not exist.
        json.JSONDecodeError: If the file is not valid JSON.
    """
    full_path = _fixture_root() / relative_path
    with open(full_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_fixtures(directory: str) -> list[dict[str, Any]]:
    """Load all JSON fixtures from a subdirectory.

    Args:
        directory: Subdirectory under tests/fixtures/, e.g. 'compression'.

    Returns:
        List of parsed fixture dicts, sorted by filename.
    """
    full_dir = _fixture_root() / directory
    if not full_dir.is_dir():
        return []
    fixtures: list[dict[str, Any]] = []
    for fname in sorted(os.listdir(full_dir)):
        if fname.endswith(".json"):
            with open(full_dir / fname, "r", encoding="utf-8") as f:
                fixtures.append(json.load(f))
    return fixtures


def save_fixture(data: dict[str, Any], relative_path: str) -> None:
    """Save a fixture dict to a JSON file (for authoring new fixtures).

    Args:
        data: The fixture dict to save.
        relative_path: Path relative to tests/fixtures/.
    """
    full_path = _fixture_root() / relative_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    with open(full_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
