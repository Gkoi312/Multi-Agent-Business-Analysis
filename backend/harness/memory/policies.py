"""
Memory & Context policies — unified configuration for thresholds, token budgets,
and coverage targets across all memory components.

This is the single source of truth for coverage thresholds, token partitions,
and pruning configuration.  All other modules reference these defaults.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ===========================================================================
# Default coverage thresholds (domain-agnostic)
# ===========================================================================

DEFAULT_COVERAGE_THRESHOLDS: dict[str, int] = {
    "business_model": 3,
    "growth": 3,
    "risk": 3,
    "competition": 2,
    "financials": 2,
}

# Lenient thresholds for early-stop decisions (has_sufficient_coverage)
DEFAULT_SUFFICIENT_THRESHOLDS: dict[str, int] = {
    "business_model": 2,
    "growth": 2,
    "risk": 2,
    "competition": 1,
    "financials": 1,
}

# All valid primary categories
VALID_PRIMARY_CATEGORIES: frozenset[str] = frozenset({
    "business_model",
    "growth",
    "risk",
    "competition",
    "financials",
    "other",
})


# ===========================================================================
# Token partition configuration (for ContextAssembler)
# ===========================================================================


@dataclass
class TokenBudget:
    """Token allocation budget for context assembly.

    Parameters
    ----------
    system_prompt : int
        Max tokens for the system prompt.
    research_summary : int
        Max tokens for the research summary / compressed turns.
    working_memory : int
        Max tokens for working memory content.
    recent_messages : int
        Min tokens to reserve for recent raw messages.
    execution_summary : int
        Max tokens for execution/history summary.
    min_current_turn : int
        Minimum tokens that MUST be reserved for the current user input.
        The assembler will never delete the current user message.
    """

    system_prompt: int = 2000
    research_summary: int = 1500
    working_memory: int = 800
    # A single generated answer can reach LLM_MAX_OUTPUT_TOKENS (4096 by
    # default, see llm_loader.py); 2000 couldn't even hold one full turn.
    # 12000 comfortably covers ~2 worst-case turns and is still a small
    # fraction of the real safe_limit for a 128k-context model (~88k).
    recent_messages: int = 12000
    execution_summary: int = 500
    min_current_turn: int = 200


# ===========================================================================
# History compaction policy
# ===========================================================================


@dataclass
class CompactionPolicy:
    """When and how to trigger history compaction.

    Parameters
    ----------
    trigger_tokens : int
        Token count at which compaction is triggered.
    keep_recent_tokens : int
        How many tokens of recent messages to preserve raw.
    max_summary_tokens : int
        Maximum tokens for the generated summary.
    min_turns_before_compact : int
        Don't compact before at least this many turns.
    """

    # This domain's whole interview is only max_num_turns=3 rounds; worst case
    # (every answer maxed at LLM_MAX_OUTPUT_TOKENS=4096) is ~13,200 tokens for
    # the entire raw history. trigger_tokens is set comfortably above that so
    # compaction — a lossy, extra-LLM-call operation meant for much longer
    # conversations — doesn't fire partway through a short interview that
    # would fit in the real context window anyway.
    trigger_tokens: int = 20000
    # Matches TokenBudget.recent_messages: large enough to hold ~2 worst-case
    # turns raw (2000 couldn't even hold one 4096-token answer).
    keep_recent_tokens: int = 12000
    max_summary_tokens: int = 500
    min_turns_before_compact: int = 2


# ===========================================================================
# MemoryDomainConfig — domain-injected configuration
# ===========================================================================


@dataclass
class MemoryDomainConfig:
    """Domain-specific memory configuration injected by the domain layer.

    Harness knows nothing about "business_model" or "growth" —
    it only receives categories, descriptions, aliases, and policy.
    """

    categories: tuple[str, ...] = ("business_model", "growth", "risk", "competition", "financials")
    category_descriptions: dict[str, str] = field(default_factory=dict)
    coverage_policy: Any = None  # CoveragePolicy instance (avoid circular import)
    predicate_aliases: dict[str, set[str]] = field(default_factory=dict)
    fallback_category: str = "other"

    @property
    def all_categories(self) -> frozenset[str]:
        return frozenset(list(self.categories) + [self.fallback_category])

    def resolve_predicate(self, predicate: str) -> str:
        """Return canonical predicate name if alias matches, else original."""
        pred_lower = predicate.lower().strip()
        for canonical, aliases in self.predicate_aliases.items():
            if pred_lower in aliases:
                return canonical
        return predicate

    def are_predicates_equivalent(self, a: str, b: str) -> bool:
        """Check whether two predicates refer to the same concept."""
        ca = self.resolve_predicate(a).lower().strip()
        cb = self.resolve_predicate(b).lower().strip()
        return ca == cb

    def to_dict(self) -> dict[str, Any]:
        return {
            "categories": list(self.categories),
            "category_descriptions": dict(self.category_descriptions),
            "coverage_policy": self.coverage_policy.to_dict() if self.coverage_policy else None,
            "predicate_aliases": {k: sorted(v) for k, v in self.predicate_aliases.items()},
            "fallback_category": self.fallback_category,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MemoryDomainConfig":
        from harness.models.memory import CoveragePolicy as CP
        policy_dict = d.get("coverage_policy")
        return cls(
            categories=tuple(str(c) for c in (d.get("categories") or ())),
            category_descriptions={str(k): str(v) for k, v in (d.get("category_descriptions") or {}).items()},
            coverage_policy=CP.from_dict(policy_dict) if policy_dict else None,
            predicate_aliases={str(k): set(str(x) for x in v) for k, v in (d.get("predicate_aliases") or {}).items()},
            fallback_category=str(d.get("fallback_category", "other") or "other"),
        )


# ===========================================================================
# ContextWindowManager defaults
# ===========================================================================


@dataclass
class ContextWindowConfig:
    """Config for ContextWindowManager.

    Parameters
    ----------
    max_tokens : int
        Total context window size. If None, looked up by model_name.
    reserved_tokens : int
        Tokens reserved for model output.
    safe_ratio : float
        Fraction of window at which compression is triggered.
    token_flush_size : int
        Number of tokens to flush at a time during FIFO eviction.
    """

    max_tokens: int | None = None
    reserved_tokens: int = 2000
    safe_ratio: float = 0.7
    token_flush_size: int = 2000
