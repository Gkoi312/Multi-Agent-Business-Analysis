"""
Eval Runner — the result type for one evaluation run.

``EvalRunResult`` collects a case's scores across scorer dimensions and
derives pass/fail, aggregate score, and repeat-grouping (``base_case_id``,
strips a trailing ``_rN`` suffix) for the reliability analysis in
``harness.evaluation.reliability``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from harness.evaluation.scorer import ScoreResult

# Regex to strip _r1, _r2, _run1 suffixes
_REPEAT_SUFFIX_RE = re.compile(r"_(?:r|run)\d+$", re.IGNORECASE)


@dataclass
class EvalRunResult:
    """Result of one evaluation run."""
    run_id: str
    case_id: str
    scores: list[ScoreResult] = field(default_factory=list)
    duration_ms: int = 0
    error: str | None = None

    # Optional: the base case ID without repeat suffix
    base_case_id: str | None = None

    def __post_init__(self) -> None:
        if self.base_case_id is None:
            self.base_case_id = _REPEAT_SUFFIX_RE.sub("", self.case_id)

    @property
    def eligible_scores(self) -> list[ScoreResult]:
        """Scores that count toward aggregates (excludes skipped)."""
        return [s for s in self.scores if s.eligible]

    @property
    def skipped_scores(self) -> list[ScoreResult]:
        """Scores that are skipped (not evaluable)."""
        return [s for s in self.scores if not s.eligible]

    @property
    def not_evaluated(self) -> bool:
        """True when all scores are skipped — nothing was actually evaluated."""
        return len(self.scores) > 0 and len(self.eligible_scores) == 0

    @property
    def passed(self) -> bool:
        """All *eligible* scores must have status 'pass'.

        - Partial is NOT pass.
        - If all scores are skipped (not_evaluated), returns False.
        - If no scores at all, returns False.
        """
        eligible = self.eligible_scores
        if not eligible:
            # All skipped or no scores → not evaluable
            return False
        return all(s.status == "pass" for s in eligible)

    @property
    def aggregate_score(self) -> float:
        """Average normalized score across all *eligible* dimensions."""
        eligible = self.eligible_scores
        if not eligible:
            return 0.0
        return round(sum(s.normalized for s in eligible) / len(eligible), 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "case_id": self.case_id,
            "base_case_id": self.base_case_id,
            "scores": [s.to_dict() for s in self.scores],
            "duration_ms": self.duration_ms,
            "error": self.error,
            "passed": self.passed,
            "aggregate_score": self.aggregate_score,
            "eligible_score_count": len(self.eligible_scores),
            "skipped_score_count": len(self.skipped_scores),
            "not_evaluated": self.not_evaluated,
        }


