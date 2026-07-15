"""
Evaluation Scorer — base classes and registry.

Follows existing project patterns: @dataclass + to_dict/from_dict,
module-level singleton SCORER_REGISTRY (same pattern as _tracers/_ledgers).

Status values:
- ``"pass"`` — all hard thresholds met
- ``"partial"`` — some thresholds met, but not all
- ``"fail"`` — hard thresholds breached
- ``"skipped"`` — case not evaluable (e.g. insufficient labels)

``eligible`` controls whether this result enters aggregate statistics
(pass rate, mean).  Skipped results are excluded.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal


# Valid status values
StatusLiteral = Literal["pass", "partial", "fail", "skipped"]


@dataclass
class ScoreResult:
    """Single evaluation dimension result.

    All scorers return this; EvalRunner aggregates across dimensions.
    """

    dimension: str
    value: float                           # raw score (0 ~ max_value)
    max_value: float
    normalized: float                      # value / max_value, 0~1
    status: str                            # "pass" | "partial" | "fail" | "skipped"
    layer: str = ""                        # "component" | "integration" | "end_to_end"
    details: str = ""
    issues: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    eligible: bool = True                  # False when status=="skipped" → excluded from aggregates

    def __post_init__(self) -> None:
        """Ensure eligible aligns with skipped status."""
        if self.status == "skipped":
            self.eligible = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "value": self.value,
            "max_value": self.max_value,
            "normalized": self.normalized,
            "status": self.status,
            "layer": self.layer,
            "details": self.details,
            "issues": self.issues,
            "evidence": self.evidence,
            "eligible": self.eligible,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ScoreResult":
        return cls(
            dimension=str(d.get("dimension", "")),
            value=float(d.get("value", 0)),
            max_value=float(d.get("max_value", 1)),
            normalized=float(d.get("normalized", 0)),
            status=str(d.get("status", "fail")),
            layer=str(d.get("layer", "")),
            details=str(d.get("details", "")),
            issues=list(d.get("issues", []) or []),
            evidence=dict(d.get("evidence", {}) or {}),
            eligible=bool(d.get("eligible", True)),
        )

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    @classmethod
    def skipped(
        cls,
        dimension: str,
        reason: str = "Not evaluable",
        layer: str = "",
        evidence: dict[str, Any] | None = None,
    ) -> "ScoreResult":
        """Factory for a skipped (not evaluable) result."""
        return cls(
            dimension=dimension,
            layer=layer,
            value=0,
            max_value=0,
            normalized=0,
            status="skipped",
            eligible=False,
            details=reason,
            evidence=evidence or {},
        )


class Scorer(ABC):
    """Base class for all evaluation scorers.

    Each scorer handles ONE evaluation dimension (e.g. compression_fidelity).
    """

    @property
    @abstractmethod
    def dimension(self) -> str:
        """Unique dimension identifier, e.g. 'compression_fidelity'."""
        ...

    @property
    @abstractmethod
    def layer(self) -> str:
        """Which evaluation layer: 'component' | 'integration' | 'end_to_end'."""
        ...

    @abstractmethod
    def score(self, **kwargs: Any) -> ScoreResult:
        """Score one evaluation result. Each scorer defines its own kwargs.

        Returns:
            ScoreResult with normalized score, status, and evidence.
        """
        ...


# ---------------------------------------------------------------------------
# Module-level singleton registry (same pattern as _tracers / _ledgers)
# ---------------------------------------------------------------------------

SCORER_REGISTRY: dict[str, Scorer] = {}


def register_scorer(scorer: Scorer) -> None:
    """Register a scorer in the global registry (no-op if already registered)."""
    SCORER_REGISTRY[scorer.dimension] = scorer


def get_scorer(dimension: str) -> Scorer | None:
    """Look up a scorer by dimension name."""
    return SCORER_REGISTRY.get(dimension)


def list_scorers(layer: str | None = None) -> list[str]:
    """List registered scorer dimensions, optionally filtered by layer."""
    if layer:
        return sorted(
            k for k, s in SCORER_REGISTRY.items() if s.layer == layer
        )
    return sorted(SCORER_REGISTRY.keys())
