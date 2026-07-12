"""
Eval Runner — runs evaluation cases with optional repeats.

Orchestrates Scorers against Fixtures and collects EvalRunResult lists.

Key fixes vs original:
- ``EvalRunResult.passed`` now requires all *eligible* scores to pass;
  partial is not pass; all-skipped → passed=False with not_evaluated flag
- Added ``base_case_id`` for repeat-grouping (strips _rN suffix)
- Added ``eligible_scores`` and ``not_evaluated`` properties
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from harness.evaluation.scorer import ScoreResult, SCORER_REGISTRY

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


class EvalRunner:
    """Runs evaluation cases with registered scorers.

    Usage::

        runner = EvalRunner()
        runner.register(CompressionFidelityScorer())
        results = runner.run_cases([
            {"case_id": "comp_001", "fixture_path": "compression/case_001_tesla_q3.json",
             "scorer_dimensions": ["compression_fidelity"], "kwargs": {...}},
        ])
    """

    def __init__(self):
        pass

    def run_single(
        self,
        case_id: str,
        scorer_dimensions: list[str],
        kwargs_by_dimension: dict[str, dict[str, Any]] | None = None,
    ) -> EvalRunResult:
        """Run a single case against the named scorer dimensions.

        Args:
            case_id: Human-readable case identifier.
            scorer_dimensions: List of scorer dimension names to run.
            kwargs_by_dimension: Optional per-dimension kwargs overrides.
        """
        import uuid

        run_id = str(uuid.uuid4())[:8]
        kwargs_by_dim = kwargs_by_dimension or {}
        scores: list[ScoreResult] = []
        error: str | None = None

        started = time.perf_counter()

        for dim in scorer_dimensions:
            scorer = SCORER_REGISTRY.get(dim)
            if scorer is None:
                scores.append(ScoreResult(
                    dimension=dim, layer="unknown",
                    value=0, max_value=2, normalized=0,
                    status="fail",
                    details=f"Scorer '{dim}' not registered",
                    issues=[f"Unknown scorer: {dim}"],
                ))
                continue

            try:
                kw = kwargs_by_dim.get(dim, {})
                result = scorer.score(**kw)
                scores.append(result)
            except Exception as exc:
                scores.append(ScoreResult(
                    dimension=dim, layer=scorer.layer,
                    value=0, max_value=2, normalized=0,
                    status="fail",
                    details=f"Scorer '{dim}' raised: {exc}",
                    issues=[str(exc)],
                ))
                error = str(exc)

        duration_ms = int((time.perf_counter() - started) * 1000)

        return EvalRunResult(
            run_id=run_id,
            case_id=case_id,
            scores=scores,
            duration_ms=duration_ms,
            error=error,
        )

    def run_batch(
        self,
        cases: list[dict[str, Any]],
        repeats: int = 1,
    ) -> list[EvalRunResult]:
        """Run multiple cases, optionally with repeats.

        Args:
            cases: List of case dicts with keys:
                - case_id (str)
                - scorer_dimensions (list[str])
                - kwargs_by_dimension (dict, optional)
            repeats: Number of times to repeat each case.

        Returns:
            Flat list of EvalRunResult (N cases × M repeats).
        """
        results: list[EvalRunResult] = []
        for case in cases:
            base_cid = str(case["case_id"])
            for r in range(repeats):
                repeat_cid = f"{base_cid}_r{r + 1}" if repeats > 1 else base_cid
                run = self.run_single(
                    case_id=repeat_cid,
                    scorer_dimensions=case["scorer_dimensions"],
                    kwargs_by_dimension=case.get("kwargs_by_dimension"),
                )
                results.append(run)
        return results
