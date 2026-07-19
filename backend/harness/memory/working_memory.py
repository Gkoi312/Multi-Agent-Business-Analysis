"""
Working Memory — structured cognitive state for the agent during research.

Unlike conversation messages (which are sequential and verbose), WorkingMemory
is a structured, compact representation of what the agent has learned so far.
It tracks facts by category using ``MemoryFact`` objects with proper lifecycle,
knowledge gaps, risk signals, and progress.

Facts are the SINGLE source of truth. Coverage, gaps, risks, conflicts, and
source counts are ALL dynamically derived from active MemoryFact objects.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from harness.memory.fact_reconciler import FactReconciler
from harness.memory.policies import VALID_PRIMARY_CATEGORIES
from harness.models.memory import (
    CompressedTurn,
    CoveragePolicy,
    FactLedger,
    MemoryFact,
    MemoryOperation,
    MergedMemory,
    _normalize_fact_text,
)

# ===========================================================================
# WorkingMemory
# ===========================================================================


@dataclass
class WorkingMemory:
    """Agent's structured working memory during a research session.

    Updated after each interview turn by ingesting ``CompressedTurn`` objects
    or raw ``MemoryFact`` entries.  The formatted output is injected into
    system prompts so the LLM knows the current research state without
    replaying the full conversation history.

    Parameters
    ----------
    facts : list[MemoryFact]
        ALL facts (active + historical). Active subset is filtered at query time.
    fact_sources : dict[str, list[str]]
        Mapping from fact_id → source IDs for traceability.
    knowledge_gaps : list[str]
        Categories that still need more data (DYNAMICALLY derived).
    risk_flags : list[str]
        Aggregated risk signals across all turns (DYNAMICALLY derived).
    turns_completed : int
        How many interview turns have been processed.
    coverage_policy : CoveragePolicy
        Single strategy object for all coverage decisions.
    """

    # ALL facts — active + historical (inactive facts preserved)
    facts: list[MemoryFact] = field(default_factory=list)
    fact_sources: dict[str, list[str]] = field(default_factory=dict)
    turns_completed: int = 0

    # Coverage policy — single source of truth
    coverage_policy: CoveragePolicy = field(default_factory=CoveragePolicy)

    # Domain config — injected by domain layer for predicate aliases, categories
    domain_config: Any = field(default=None, repr=False, compare=False)

    # -- reconciler --
    _reconciler: Any = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._reconciler is None:
            # Prefer domain_config when available for predicate aliases + categories
            if self.domain_config is not None:
                self._reconciler = FactReconciler(domain_config=self.domain_config)
            else:
                self._reconciler = FactReconciler(category_whitelist=VALID_PRIMARY_CATEGORIES)

    # ------------------------------------------------------------------
    # Dynamic derived properties (NO manual maintenance)
    # ------------------------------------------------------------------

    @property
    def active_facts(self) -> list[MemoryFact]:
        """Active facts — dynamically filtered."""
        return [f for f in self.facts if f.is_active]

    @property
    def unresolved_conflicts(self) -> list[str]:
        """DYNAMICALLY derived from active facts with conflicts_with."""
        conflict_ids: set[str] = set()
        for f in self.active_facts:
            if f.conflicts_with:
                conflict_ids.add(f.fact_id)
                conflict_ids.update(f.conflicts_with)
        return sorted(conflict_ids)

    @property
    def knowledge_gaps(self) -> list[str]:
        """DYNAMICALLY derived from active fact counts vs policy thresholds."""
        counts = self._count_active_by_category()
        return [
            cat for cat, threshold in self.coverage_policy.required_for_full_report.items()
            if counts.get(cat, 0) < threshold
        ]

    @property
    def risk_flags(self) -> list[str]:
        """DYNAMICALLY derived from active facts with risk keywords."""
        risk_keywords = [
            "risk", "threat", "regulation", "fine", "penalty", "lawsuit",
            "breach", "vulnerab",
            "风险", "威胁", "监管", "合规", "罚款", "处罚",
            "诉讼", "起诉", "违规", "违反", "制裁", "漏洞",
        ]
        flags = []
        for f in self.active_facts:
            lowered = f.text.lower()
            if any(kw in lowered for kw in risk_keywords):
                flags.append(f.text[:200])
        return flags

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_fact(
        self,
        text: str,
        category: str = "other",
        source_ids: list[str] | None = None,
        evidence_quality: str = "medium",
        turn_id: str | int | None = None,
        subject: str = "",
        predicate: str = "",
        value: str | float | None = None,
        unit: str | None = None,
        period: str | None = None,
    ) -> MemoryFact:
        """Add a single fact with lifecycle reconciliation.

        Returns the resulting ``MemoryFact`` (may be a new or updated fact).
        """
        category = self._validate_category(category)
        source_ids = source_ids or []

        new_fact = MemoryFact(
            text=text,
            normalized_text=_normalize_fact_text(text),
            primary_category=category,
            source_ids=list(source_ids),
            evidence_quality=evidence_quality,
            turn_id=turn_id,
            status="active",
            subject=subject,
            predicate=predicate,
            value=value,
            unit=unit,
            period=period,
        )

        # Reconcile — returns FactLedger preserving all history
        ledger: FactLedger = self._reconciler.reconcile([new_fact], self.facts)
        self.facts = ledger.all_facts

        op = ledger.operations[-1] if ledger.operations else None
        resulting_id = self._resulting_fact_id(op, new_fact.fact_id)

        # Update source tracking
        if resulting_id and source_ids:
            existing_sources = self.fact_sources.get(resulting_id, [])
            for sid in source_ids:
                if sid not in existing_sources:
                    existing_sources.append(sid)
            self.fact_sources[resulting_id] = existing_sources

        # Return the actual resulting fact by ID — no more guessing by text
        for f in self.facts:
            if f.fact_id == resulting_id:
                return f
        return new_fact

    def ingest_compressed_turn(self, turn: CompressedTurn) -> None:
        """Ingest all facts from one compressed turn.

        Each fact (from ``turn.facts``, not ``turn.key_findings``) is
        reconciled against existing facts.  This is the ONLY ingestion path.
        """
        if turn.facts:
            # Primary path: structured facts
            for fact in turn.facts:
                fact.turn_id = self.turns_completed
                fact.primary_category = self._validate_category(fact.primary_category)
                fact.normalized_text = fact.normalized_text or _normalize_fact_text(fact.text)

            ledger: FactLedger = self._reconciler.reconcile(turn.facts, self.facts)
            self.facts = ledger.all_facts

            # Source tracking — each fact maps 1:1 to the operation reconcile()
            # logged for it, in the same order, so zip() pairs them correctly.
            for fact, op in zip(turn.facts, ledger.operations):
                if not fact.source_ids:
                    continue
                resulting_id = self._resulting_fact_id(op, fact.fact_id)
                existing = self.fact_sources.get(resulting_id, [])
                for sid in fact.source_ids:
                    if sid not in existing:
                        existing.append(sid)
                self.fact_sources[resulting_id] = existing

        elif turn.key_findings:
            # Backward compat: no structured facts, create from key_findings
            for finding_text in turn.key_findings:
                cat = MergedMemory._classify_fact(finding_text)
                self.add_fact(
                    text=finding_text,
                    category=cat,
                    source_ids=list(turn.sources_cited) if turn.sources_cited else [],
                    evidence_quality=turn.evidence_quality,
                    turn_id=self.turns_completed,
                )

        self.turns_completed += 1

    def to_merged_memory(self) -> MergedMemory:
        """Generate a read-only MergedMemory snapshot from current state."""
        return MergedMemory.from_working_memory(self, self.coverage_policy)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def has_sufficient_coverage(self) -> bool:
        """Return True when coverage is sufficient to stop interviewing.

        ALL checks against coverage_policy:
        - Unique active fact counts per category >= required_for_early_stop
        - Independent source count >= min_independent_sources
        - Facts below minimum_evidence_quality are NOT counted
        """
        policy = self.coverage_policy

        # Quality-filtered counts
        quality_order = {"high": 3, "medium": 2, "low": 1}
        min_q = quality_order.get(policy.minimum_evidence_quality, 2)

        counts: dict[str, int] = {}
        for f in self.active_facts:
            q = quality_order.get(f.evidence_quality, 1)
            if q < min_q:
                continue
            cat = f.primary_category
            counts[cat] = counts.get(cat, 0) + 1

        for cat, threshold in policy.required_for_early_stop.items():
            if counts.get(cat, 0) < threshold:
                return False

        # Independent sources — true dedup across all active fact source_ids
        if policy.min_independent_sources > 0:
            if self.independent_source_count() < policy.min_independent_sources:
                return False

        return True

    def active_fact_count(self) -> int:
        """Number of unique active facts."""
        return len(self.active_facts)

    def independent_source_count(self) -> int:
        """Number of truly distinct source IDs across all active facts."""
        sources: set[str] = set()
        for f in self.active_facts:
            for sid in f.source_ids:
                if sid:
                    sources.add(sid)
        # Also check fact_sources dict
        for sids in self.fact_sources.values():
            for sid in sids:
                if sid:
                    sources.add(sid)
        return len(sources)

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    def format(self) -> str:
        """Render a compact, LLM-readable progress summary.

        ALL data derived dynamically from active facts.
        """
        counts = self._count_active_by_category()
        total = self.active_fact_count()

        lines = [
            f"Research progress: {total} unique facts collected in "
            f"{self.turns_completed} turn(s) from {self.independent_source_count()} sources.",
        ]
        for cat in sorted(counts.keys()):
            if counts[cat] > 0:
                lines.append(f"\n{cat} ({counts[cat]} facts):")
                cat_facts = [f for f in self.active_facts if f.primary_category == cat]
                for fact in cat_facts[-3:]:
                    lines.append(f"  - {fact.text[:150]}")

        gaps = self.knowledge_gaps
        if gaps:
            lines.append(f"\nKnowledge gaps remain: {', '.join(gaps)}")
        conflicts = self.unresolved_conflicts
        if conflicts:
            lines.append(f"\nUnresolved conflicts: {len(conflicts)}")
        flags = self.risk_flags
        if flags:
            lines.append(f"\nRisk signals ({len(flags)}):")
            for r in flags[-3:]:
                lines.append(f"  - {r[:150]}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        result = {
            "facts": [f.to_dict() for f in self.facts],
            "fact_sources": {k: list(v) for k, v in self.fact_sources.items()},
            "turns_completed": self.turns_completed,
            "coverage_policy": self.coverage_policy.to_dict(),
        }
        if self.domain_config is not None:
            try:
                result["domain_config"] = self.domain_config.to_dict()
            except Exception:
                pass
        return result

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "WorkingMemory":
        from harness.memory.policies import MemoryDomainConfig
        facts_raw = d.get("facts") or []
        facts = [MemoryFact.from_dict(f) if isinstance(f, dict) else f for f in facts_raw]
        policy_dict = d.get("coverage_policy") or {}
        dc_dict = d.get("domain_config")
        domain_config = MemoryDomainConfig.from_dict(dc_dict) if dc_dict else None
        return cls(
            facts=facts,
            fact_sources={k: list(v) for k, v in (d.get("fact_sources") or {}).items()},
            turns_completed=int(d.get("turns_completed", 0)),
            coverage_policy=CoveragePolicy.from_dict(policy_dict) if policy_dict else CoveragePolicy(),
            domain_config=domain_config,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _validate_category(self, category: str) -> str:
        if category in VALID_PRIMARY_CATEGORIES:
            return category
        return "other"

    @staticmethod
    def _resulting_fact_id(op: dict[str, Any] | None, fallback_fact_id: str) -> str:
        """Resolve which fact_id an incoming fact actually ended up at.

        An operation's own "fact_id" is the resulting fact_id for ADD/
        UPDATE/CONFLICT, but NOT for NONE (info merged into "matched_id")
        or INVALIDATE (superseded by "replacement_id") — using "fact_id"
        blindly for those two would key off an ID that was never actually
        stored in self.facts.
        """
        if op is None:
            return fallback_fact_id
        if op.get("operation") == MemoryOperation.NONE.value:
            return op.get("matched_id", fallback_fact_id)
        if op.get("operation") == MemoryOperation.INVALIDATE.value:
            return op.get("replacement_id", fallback_fact_id)
        return op.get("fact_id", fallback_fact_id)

    def _count_active_by_category(self) -> dict[str, int]:
        """Count unique active facts per primary category.

        Quality-filtered by coverage_policy.minimum_evidence_quality.
        """
        quality_order = {"high": 3, "medium": 2, "low": 1}
        min_q = quality_order.get(self.coverage_policy.minimum_evidence_quality, 2)

        counts: dict[str, int] = {}
        for fact in self.active_facts:
            q = quality_order.get(fact.evidence_quality, 1)
            if q < min_q:
                continue
            cat = fact.primary_category
            counts[cat] = counts.get(cat, 0) + 1
        return counts
