"""
Harness memory data models — MemoryFact, CompressedTurn, RunningSummary,
SearchDigest, FactLedger, SourceRecord, CoveragePolicy, and helpers.

These types are domain-agnostic and used by the IncrementalCompressor,
WorkingMemory, and context management infrastructure.
"""
from __future__ import annotations

import json
import re
import uuid
import hashlib
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence

logger = logging.getLogger(__name__)


# ===========================================================================
# Memory Operations
# ===========================================================================


class MemoryOperation(str, Enum):
    """Fact lifecycle operations for the reconciler."""
    ADD = "ADD"
    UPDATE = "UPDATE"
    INVALIDATE = "INVALIDATE"
    NONE = "NONE"
    CONFLICT = "CONFLICT"


# ===========================================================================
# MemoryFact — structured fact with lifecycle
# ===========================================================================


@dataclass
class MemoryFact:
    """A single structured fact with lifecycle tracking.

    Each fact has a stable ``fact_id``, belongs to exactly one
    ``primary_category``, and carries evidence quality, source bindings,
    subject/predicate/value decomposition, and operation history.
    """

    fact_id: str = ""
    text: str = ""
    normalized_text: str = ""
    primary_category: str = "other"
    tags: list[str] = field(default_factory=list)
    source_ids: list[str] = field(default_factory=list)
    evidence_quality: str = "medium"  # high | medium | low
    confidence: float | None = None
    status: str = "active"  # active | invalidated | superseded
    turn_id: str | int | None = None
    supersedes: str | None = None
    conflicts_with: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    # -- SPDV decomposition for precise reconciliation --------------------
    subject: str = ""       # what/who the fact is about
    predicate: str = ""     # the property/attribute
    value: str | float | None = None   # the asserted value
    unit: str | None = None             # unit of measurement
    period: str | None = None           # time period

    # -- Revision history -------------------------------------------------
    revision_history: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.fact_id:
            self.fact_id = str(uuid.uuid4())
        ts = _now_iso()
        if not self.created_at:
            self.created_at = ts
        if not self.updated_at:
            self.updated_at = ts

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    def to_dict(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "text": self.text,
            "normalized_text": self.normalized_text,
            "primary_category": self.primary_category,
            "tags": list(self.tags),
            "source_ids": list(self.source_ids),
            "evidence_quality": self.evidence_quality,
            "confidence": self.confidence,
            "status": self.status,
            "turn_id": self.turn_id,
            "supersedes": self.supersedes,
            "conflicts_with": list(self.conflicts_with),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "subject": self.subject,
            "predicate": self.predicate,
            "value": self.value,
            "unit": self.unit,
            "period": self.period,
            "revision_history": list(self.revision_history),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MemoryFact":
        return cls(
            fact_id=str(d.get("fact_id", "") or ""),
            text=str(d.get("text", "") or ""),
            normalized_text=str(d.get("normalized_text", "") or ""),
            primary_category=str(d.get("primary_category", "other") or "other"),
            tags=[str(t) for t in (d.get("tags") or [])],
            source_ids=[str(s) for s in (d.get("source_ids") or [])],
            evidence_quality=str(d.get("evidence_quality", "medium") or "medium"),
            confidence=float(d["confidence"]) if d.get("confidence") is not None else None,
            status=str(d.get("status", "active") or "active"),
            turn_id=d.get("turn_id"),
            supersedes=str(d.get("supersedes")) if d.get("supersedes") else None,
            conflicts_with=[str(c) for c in (d.get("conflicts_with") or [])],
            created_at=str(d.get("created_at", "") or ""),
            updated_at=str(d.get("updated_at", "") or ""),
            subject=str(d.get("subject", "") or ""),
            predicate=str(d.get("predicate", "") or ""),
            value=d.get("value"),
            unit=str(d.get("unit")) if d.get("unit") else None,
            period=str(d.get("period")) if d.get("period") else None,
            revision_history=[dict(h) for h in (d.get("revision_history") or [])],
        )


# ===========================================================================
# FactLedger — complete fact set with active tracking
# ===========================================================================


@dataclass
class FactLedger:
    """Complete fact set returned by reconciliation.

    ``all_facts`` contains every fact ever seen (including invalidated/
    superseded). ``active_fact_ids`` is the subset currently active.
    ``operations`` records what happened during this reconciliation.
    """

    all_facts: list[MemoryFact] = field(default_factory=list)
    active_fact_ids: set[str] = field(default_factory=set)
    operations: list[dict[str, Any]] = field(default_factory=list)

    @property
    def active_facts(self) -> list[MemoryFact]:
        return [f for f in self.all_facts if f.fact_id in self.active_fact_ids]

    def to_dict(self) -> dict[str, Any]:
        return {
            "all_facts": [f.to_dict() for f in self.all_facts],
            "active_fact_ids": sorted(self.active_fact_ids),
            "operations": list(self.operations),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "FactLedger":
        facts = [MemoryFact.from_dict(f) if isinstance(f, dict) else f
                 for f in (d.get("all_facts") or [])]
        return cls(
            all_facts=facts,
            active_fact_ids=set(d.get("active_fact_ids") or []),
            operations=list(d.get("operations") or []),
        )


# ===========================================================================
# RunningSummary — incremental summary cursor
# ===========================================================================


@dataclass
class RunningSummary:
    """Tracks which messages have been summarized to ensure idempotent,
    incremental summarization across checkpoint retries.

    Attributes
    ----------
    summary : str
        The latest summary text produced by the summarization model.
    summarized_message_ids : set[str]
        All message IDs that have been included in a previous summary.
    last_summarized_message_id : str | None
        The ID of the last message that was summarized.  Used to find
        the resumption point on the next call.
    version : int
        Monotonic counter incremented on each successful summarization.
    """

    summary: str = ""
    summarized_message_ids: set[str] = field(default_factory=set)
    last_summarized_message_id: str | None = None
    version: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "summarized_message_ids": sorted(self.summarized_message_ids),
            "last_summarized_message_id": self.last_summarized_message_id,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RunningSummary":
        return cls(
            summary=str(d.get("summary", "") or ""),
            summarized_message_ids=set(d.get("summarized_message_ids") or []),
            last_summarized_message_id=d.get("last_summarized_message_id"),
            version=int(d.get("version", 0) or 0),
        )


# ===========================================================================
# SourceRecord — registered source metadata
# ===========================================================================


@dataclass
class SourceRecord:
    """Registered source with metadata for URL mapping.

    Models see only source IDs; code maps to URLs at citation time.
    """

    source_id: str = ""
    url: str = ""
    title: str = ""
    retrieved_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "url": self.url,
            "title": self.title,
            "retrieved_at": self.retrieved_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SourceRecord":
        return cls(
            source_id=str(d.get("source_id", "") or ""),
            url=str(d.get("url", "") or ""),
            title=str(d.get("title", "") or ""),
            retrieved_at=str(d.get("retrieved_at", "") or ""),
        )


# ===========================================================================
# SearchDigest — lightweight search result representation
# ===========================================================================


@dataclass
class SearchDigest:
    """Compressed representation of search results for prompt injection.

    The raw ToolResult stays in checkpoint; only this digest goes into
    the assembled LLM context.
    """

    query: str = ""
    source_ids: list[str] = field(default_factory=list)
    evidence_snippets: list[str] = field(default_factory=list)
    extracted_claims: list[str] = field(default_factory=list)
    tokens_before: int = 0
    tokens_after: int = 0
    # Source registry: model sees IDs, code maps to URLs
    source_registry: dict[str, SourceRecord] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "source_ids": list(self.source_ids),
            "evidence_snippets": list(self.evidence_snippets),
            "extracted_claims": list(self.extracted_claims),
            "tokens_before": self.tokens_before,
            "tokens_after": self.tokens_after,
            "source_registry": {k: v.to_dict() for k, v in self.source_registry.items()},
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SearchDigest":
        registry = {}
        for k, v in (d.get("source_registry") or {}).items():
            registry[str(k)] = SourceRecord.from_dict(v) if isinstance(v, dict) else v
        return cls(
            query=str(d.get("query", "") or ""),
            source_ids=[str(s) for s in (d.get("source_ids") or [])],
            evidence_snippets=[str(s) for s in (d.get("evidence_snippets") or [])],
            extracted_claims=[str(c) for c in (d.get("extracted_claims") or [])],
            tokens_before=int(d.get("tokens_before", 0) or 0),
            tokens_after=int(d.get("tokens_after", 0) or 0),
            source_registry=registry,
        )


# ===========================================================================
# CoveragePolicy — explicit strategy object
# ===========================================================================


@dataclass
class CoveragePolicy:
    """Explicit strategy for coverage requirements.

    Two tiers: required_for_full_report (strict) and required_for_early_stop
    (lenient). All components read from the same instance.
    """

    required_for_full_report: dict[str, int] = field(default_factory=lambda: {
        "business_model": 3,
        "growth": 3,
        "risk": 3,
        "competition": 2,
        "financials": 2,
    })
    required_for_early_stop: dict[str, int] = field(default_factory=lambda: {
        "business_model": 2,
        "growth": 2,
        "risk": 2,
        "competition": 1,
        "financials": 1,
    })
    minimum_evidence_quality: str = "medium"
    min_independent_sources: int = 2
    unresolved_questions_block_stop: bool = False
    unresolved_conflicts_block_stop: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "required_for_full_report": dict(self.required_for_full_report),
            "required_for_early_stop": dict(self.required_for_early_stop),
            "minimum_evidence_quality": self.minimum_evidence_quality,
            "min_independent_sources": self.min_independent_sources,
            "unresolved_questions_block_stop": self.unresolved_questions_block_stop,
            "unresolved_conflicts_block_stop": self.unresolved_conflicts_block_stop,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CoveragePolicy":
        return cls(
            required_for_full_report={
                k: int(v) for k, v in (d.get("required_for_full_report") or {}).items()
            },
            required_for_early_stop={
                k: int(v) for k, v in (d.get("required_for_early_stop") or {}).items()
            },
            minimum_evidence_quality=str(d.get("minimum_evidence_quality", "medium") or "medium"),
            min_independent_sources=int(d.get("min_independent_sources", 2) or 2),
            unresolved_questions_block_stop=bool(d.get("unresolved_questions_block_stop", False)),
            unresolved_conflicts_block_stop=bool(d.get("unresolved_conflicts_block_stop", True)),
        )


# ===========================================================================
# CompressedTurn — structured turn compression
# ===========================================================================


@dataclass
class CompressedTurn:
    """Structured summary of one interview/research round.

    Produced by ``IncrementalCompressor.compress_completed_turn()`` and
    accumulated in ``InterviewState.compressed_turns``.

    ``facts`` is the primary truth source — ``key_findings`` is a derived
    compatibility property.  For backward compatibility, constructor accepts
    ``key_findings``, ``evidence_quality``, and ``sources_cited`` as InitVar
    kwargs which are translated to facts.
    """

    question_intent: str = ""
    facts: list[MemoryFact] = field(default_factory=list)
    numbers_mentioned: list[dict[str, Any]] = field(default_factory=list)
    unanswered: list[str] = field(default_factory=list)
    compression_error: str = ""

    def __init__(
        self,
        question_intent: str = "",
        facts: list[MemoryFact] | None = None,
        numbers_mentioned: list[dict[str, Any]] | None = None,
        unanswered: list[str] | None = None,
        compression_error: str = "",
        **kwargs: Any,
    ):
        # Handle backward-compat kwargs
        key_findings = kwargs.pop("key_findings", None)
        evidence_quality = kwargs.pop("evidence_quality", "medium")
        sources_cited = kwargs.pop("sources_cited", None)

        self.question_intent = question_intent

        if facts:
            self.facts = list(facts)
        elif key_findings:
            self.facts = [
                MemoryFact(
                    text=str(t),
                    primary_category="other",
                    evidence_quality=str(evidence_quality),
                    source_ids=list(sources_cited) if sources_cited else [],
                )
                for t in key_findings
            ]
        else:
            self.facts = []

        self.numbers_mentioned = list(numbers_mentioned) if numbers_mentioned else []
        self.unanswered = list(unanswered) if unanswered else []
        self.compression_error = compression_error

    # -- derived compatibility accessors -------------------------------
    @property
    def key_findings(self) -> list[str]:
        """Derived from ``facts`` for backward compatibility."""
        return [f.text for f in self.facts if f.text]

    @key_findings.setter
    def key_findings(self, value: list[str]) -> None:
        """Allow setting from legacy code."""
        if not self.facts:
            self.facts = [
                MemoryFact(text=str(t), primary_category="other", evidence_quality="low")
                for t in value
            ]
        else:
            for t in value:
                self.facts.append(MemoryFact(text=str(t), primary_category="other", evidence_quality="low"))

    @property
    def evidence_quality(self) -> str:
        """Derived: best quality among facts, or 'medium' if empty."""
        if not self.facts:
            return "medium"
        qualities = {"high": 3, "medium": 2, "low": 1}
        best = max(self.facts, key=lambda f: qualities.get(f.evidence_quality, 1))
        return best.evidence_quality

    @property
    def sources_cited(self) -> list[str]:
        """Derived: unique source IDs across all facts."""
        seen: set[str] = set()
        result: list[str] = []
        for f in self.facts:
            for sid in f.source_ids:
                if sid not in seen:
                    seen.add(sid)
                    result.append(sid)
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_intent": self.question_intent,
            "facts": [f.to_dict() for f in self.facts],
            "key_findings": self.key_findings,
            "numbers_mentioned": self.numbers_mentioned,
            "evidence_quality": self.evidence_quality,
            "sources_cited": self.sources_cited,
            "unanswered": self.unanswered,
            "compression_error": self.compression_error,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CompressedTurn":
        facts_raw = d.get("facts") or []
        facts = [MemoryFact.from_dict(f) if isinstance(f, dict) else f for f in facts_raw]

        # Backward compat: if facts is empty but key_findings exists, create facts
        if not facts and d.get("key_findings"):
            facts = [
                MemoryFact(
                    text=str(t),
                    primary_category="other",
                    evidence_quality=str(d.get("evidence_quality", "low") or "low"),
                )
                for t in d["key_findings"]
            ]

        return cls(
            question_intent=str(d.get("question_intent", "") or ""),
            facts=facts,
            numbers_mentioned=[
                dict(n) for n in (d.get("numbers_mentioned") or []) if isinstance(n, dict)
            ],
            unanswered=[
                str(u) for u in (d.get("unanswered") or [])
            ] if isinstance(d.get("unanswered"), list) else (
                [str(d["unanswered"])] if d.get("unanswered") else []
            ),
            compression_error=str(d.get("compression_error", "") or ""),
        )

    def format(self) -> str:
        """One-paragraph human-readable summary for injection into prompts."""
        parts = [f"Intent: {self.question_intent}"]
        if self.facts:
            parts.append("Findings:")
            for f in self.facts:
                parts.append(f"  - {f.text[:200]}")
        if self.numbers_mentioned:
            nums = [
                f"{n.get('value','?')}{n.get('unit','')} ({n.get('context','')})"
                for n in self.numbers_mentioned
            ]
            parts.append("Numbers: " + ", ".join(nums))
        parts.append(f"Evidence quality: {self.evidence_quality}")
        if self.compression_error:
            parts.append(f"Compression error: {self.compression_error}")
        if self.unanswered:
            parts.append(f"Still unanswered: {'; '.join(self.unanswered)}")
        return "\n".join(parts)


# ===========================================================================
# MergedMemory — read-only statistical snapshot
# ===========================================================================


@dataclass
class MergedMemory:
    """Aggregated research state — a read-only statistical snapshot.

    Generated by ``WorkingMemory``, NOT independently maintained.
    All counts derive from active ``MemoryFact`` objects.
    """

    total_facts: int = 0
    coverage: dict[str, int] = field(default_factory=lambda: {
        "business_model": 0,
        "growth": 0,
        "risk": 0,
        "competition": 0,
        "financials": 0,
        "other": 0,
    })
    knowledge_gaps: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    unresolved_questions: list[str] = field(default_factory=list)
    unresolved_conflicts: list[str] = field(default_factory=list)
    used_sources: set[str] = field(default_factory=set)
    facts: list[dict[str, Any]] = field(default_factory=list)
    independent_source_count: int = 0

    # Category keyword detection (code-based, no LLM needed)
    CATEGORY_KEYWORDS: dict[str, list[str]] = field(default_factory=lambda: {
        "business_model": [
            "revenue", "profit", "margin", "pricing", "monetiz", "subscription",
            "saas", "b2b", "b2c",
        ],
        "growth": [
            "growth", "scale", "expand", "market share", "yoy", "cagr",
            "acceleration", "traction",
        ],
        "risk": [
            "risk", "regulation", "compliance", "threat", "lawsuit",
            "sanction", "fine", "penalty",
        ],
        "competition": [
            "compet", "rival", "moat", "market", "differentiat", "position", "leader",
        ],
        "financials": [
            "revenue", "profit", "loss", "margin", "ebitda", "cash flow",
            "balance sheet", "debt", "valuation",
        ],
    })

    @staticmethod
    def from_working_memory(
        wm: Any,  # WorkingMemory
        coverage_policy: CoveragePolicy | None = None,
    ) -> "MergedMemory":
        """Create a read-only snapshot from WorkingMemory.

        ALL counts derive from active MemoryFact objects — no independent
        truth source.
        """
        policy = coverage_policy or CoveragePolicy()

        active_facts = [f for f in wm.facts if f.is_active]
        quality_order = {"high": 3, "medium": 2, "low": 1}
        min_q = quality_order.get(policy.minimum_evidence_quality, 2)

        # Count per category (quality-filtered)
        coverage: dict[str, int] = {}
        for f in active_facts:
            q = quality_order.get(f.evidence_quality, 1)
            if q < min_q:
                continue
            cat = f.primary_category
            coverage[cat] = coverage.get(cat, 0) + 1
        for key in ["business_model", "growth", "risk", "competition", "financials", "other"]:
            coverage.setdefault(key, 0)

        # Independent sources
        all_sources: set[str] = set()
        for f in active_facts:
            for sid in f.source_ids:
                all_sources.add(sid)

        # Knowledge gaps
        gaps = [
            cat for cat, threshold in policy.required_for_full_report.items()
            if coverage.get(cat, 0) < threshold
        ]

        # Risk flags
        risk_keywords = [
            "risk", "threat", "regulation", "fine", "penalty", "lawsuit",
            "breach", "vulnerab",
        ]
        risk_flags = []
        for f in active_facts:
            lowered = f.text.lower()
            if any(kw in lowered for kw in risk_keywords):
                risk_flags.append(f.text[:200])

        # Conflicts — dynamically derived from facts
        conflict_ids: set[str] = set()
        for f in active_facts:
            if f.conflicts_with:
                conflict_ids.add(f.fact_id)
                conflict_ids.update(f.conflicts_with)
        unresolved_conflicts = sorted(conflict_ids)

        # Unresolved questions
        unresolved = list(wm.unresolved_questions) if hasattr(wm, "unresolved_questions") else []

        return MergedMemory(
            total_facts=len(active_facts),
            coverage=coverage,
            knowledge_gaps=gaps,
            risk_flags=risk_flags,
            unresolved_questions=unresolved,
            unresolved_conflicts=unresolved_conflicts,
            used_sources=all_sources,
            facts=[f.to_dict() for f in active_facts],
            independent_source_count=len(all_sources),
        )

    def has_sufficient_coverage(self, policy: CoveragePolicy | None = None) -> bool:
        """Return True when every tracked category has enough facts."""
        p = policy or CoveragePolicy()
        thresholds = p.required_for_early_stop

        for cat, threshold in thresholds.items():
            if self.coverage.get(cat, 0) < threshold:
                return False

        if p.min_independent_sources > 0 and self.independent_source_count < p.min_independent_sources:
            return False

        if p.unresolved_conflicts_block_stop and self.unresolved_conflicts:
            return False

        return True

    @staticmethod
    def _classify_fact(fact: str) -> str:
        """Assign exactly ONE primary category per fact via keyword priority."""
        lowered = fact.lower()
        # Priority: risk > growth > competition > financials > business_model > other
        if any(kw in lowered for kw in [
            "risk", "regulation", "compliance", "threat", "lawsuit",
            "sanction", "fine", "penalty", "breach", "vulnerab", "dispute",
            "litigation", "infringe", "antitrust", "corruption", "bribery",
            "fraud", "misconduct", "recall", "ban", "prohibition",
            "风险", "监管", "合规", "处罚", "制裁", "诉讼", "违规", "漏洞",
            "腐败", "受贿", "欺诈", "召回", "禁令",
        ]):
            return "risk"
        if any(kw in lowered for kw in [
            "growth", "scale", "expand", "yoy", "cagr", "traction",
            "acceleration", "momentum", "ramp", "adoption rate",
            "增长", "增速", "扩大", "扩张", "同比增长", "年复合增长率",
        ]):
            return "growth"
        if any(kw in lowered for kw in [
            "compet", "rival", "moat", "differentiat", "position",
            "leader", "market share", "incumbent", "new entrant",
            "oligopoly", "monopoly", "duopoly",
            "竞争", "对手", "护城河", "差异化", "市场地位", "龙头",
            "市场份额", "垄断", "寡头",
        ]):
            return "competition"
        if any(kw in lowered for kw in [
            "ebitda", "cash flow", "balance sheet", "debt", "valuation",
            "gross margin", "net margin", "operating income", "free cash flow",
            "capex", "working capital", "liquidity", "solvency",
            "现金流", "资产负债", "负债", "估值", "毛利率", "净利率",
            "资本支出", "流动性", "偿债",
        ]):
            return "financials"
        if any(kw in lowered for kw in [
            "revenue", "profit", "margin", "pricing", "monetiz",
            "subscription", "saas", "b2b", "b2c", "business model",
            "go-to-market", "distribution channel", "customer segment",
            "value proposition", "cost structure",
            "收入", "营收", "利润", "定价", "变现", "订阅", "商业模式",
            "销售渠道", "客户群",
        ]):
            return "business_model"
        return "other"

    def format(self) -> str:
        """Format as a short, LLM-readable progress block."""
        lines = [
            f"Research progress: {self.total_facts} facts collected "
            f"across {self.independent_source_count} sources."
        ]
        lines.append("\nCoverage per theme:")
        for cat, count in self.coverage.items():
            marker = " ✓" if count >= 2 else " ⚠"
            lines.append(f"  {cat}: {count}{marker}")
        if self.knowledge_gaps:
            lines.append(f"\nKnowledge gaps remain: {', '.join(self.knowledge_gaps)}")
        if self.unresolved_questions:
            lines.append(f"\nUnresolved questions: {len(self.unresolved_questions)}")
        if self.unresolved_conflicts:
            lines.append(f"\nUnresolved conflicts: {len(self.unresolved_conflicts)}")
        if self.risk_flags:
            lines.append(f"\nRisk signals flagged: {len(self.risk_flags)}")
            for rf in self.risk_flags[-3:]:
                lines.append(f"  - {rf[:120]}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_facts": self.total_facts,
            "coverage": dict(self.coverage),
            "knowledge_gaps": list(self.knowledge_gaps),
            "risk_flags": list(self.risk_flags),
            "unresolved_questions": list(self.unresolved_questions),
            "unresolved_conflicts": list(self.unresolved_conflicts),
            "used_sources": sorted(self.used_sources),
            "facts": list(self.facts),
            "independent_source_count": self.independent_source_count,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MergedMemory":
        coverage = {k: int(v) for k, v in (d.get("coverage") or {}).items()}
        for key in ["business_model", "growth", "risk", "competition", "financials", "other"]:
            coverage.setdefault(key, 0)
        return cls(
            total_facts=int(d.get("total_facts", 0)),
            coverage=coverage,
            knowledge_gaps=[str(g) for g in (d.get("knowledge_gaps") or [])],
            risk_flags=[str(r) for r in (d.get("risk_flags") or [])],
            unresolved_questions=[str(q) for q in (d.get("unresolved_questions") or [])],
            unresolved_conflicts=[str(c) for c in (d.get("unresolved_conflicts") or [])],
            used_sources=set(d.get("used_sources") or []),
            facts=[dict(f) for f in (d.get("facts") or [])],
            independent_source_count=int(d.get("independent_source_count", 0) or 0),
        )


# ===========================================================================
# ContextAssemblyResult
# ===========================================================================


class ContextBudgetExceeded(Exception):
    """Raised when context cannot be shrunk to fit within the budget."""
    def __init__(self, current_tokens: int, safe_limit: int):
        self.current_tokens = current_tokens
        self.safe_limit = safe_limit
        super().__init__(
            f"Context budget exceeded: {current_tokens} tokens > "
            f"{safe_limit} safe limit. Cannot shrink further without "
            f"removing current user input."
        )


@dataclass
class ContextAssemblyResult:
    """Projected context for a single LLM call — never mutates canonical state."""

    system_prompt: str = ""
    execution_summary: str = ""
    research_summary: str = ""
    working_memory: str = ""
    recent_raw_messages: list[Any] = field(default_factory=list)
    current_search_digest: str = ""
    retrieved_long_term_facts: str = ""
    total_tokens: int = 0
    token_breakdown: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "system_prompt": self.system_prompt,
            "execution_summary": self.execution_summary,
            "research_summary": self.research_summary,
            "working_memory": self.working_memory,
            "recent_raw_messages": [
                {"type": getattr(m, "type", "unknown"), "content": str(getattr(m, "content", str(m)))[:500]}
                for m in self.recent_raw_messages[-10:]
            ],
            "current_search_digest": self.current_search_digest,
            "retrieved_long_term_facts": self.retrieved_long_term_facts,
            "total_tokens": self.total_tokens,
            "token_breakdown": dict(self.token_breakdown),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ContextAssemblyResult":
        return cls(
            system_prompt=str(d.get("system_prompt", "") or ""),
            execution_summary=str(d.get("execution_summary", "") or ""),
            research_summary=str(d.get("research_summary", "") or ""),
            working_memory=str(d.get("working_memory", "") or ""),
            current_search_digest=str(d.get("current_search_digest", "") or ""),
            retrieved_long_term_facts=str(d.get("retrieved_long_term_facts", "") or ""),
            total_tokens=int(d.get("total_tokens", 0) or 0),
            token_breakdown={k: int(v) for k, v in (d.get("token_breakdown") or {}).items()},
        )


# ===========================================================================
# TokenCounter — split interface for type-safe counting
# ===========================================================================


class TokenCounter:
    """Type-safe token counting interface.

    Provides three distinct methods so callers never confuse str, Message,
    or list[Message].
    """

    def __init__(self, window_mgr: Any = None):
        """Wrap a ContextWindowManager or use fallback heuristic."""
        self._wm = window_mgr

    def count_text(self, text: str) -> int:
        """Count tokens in a plain string."""
        if not text:
            return 0
        if self._wm is not None:
            return self._wm.estimate_tokens(text)
        return max(1, len(text) // 4)

    def count_message(self, message: Any) -> int:
        """Count tokens in a single message object."""
        if self._wm is not None:
            return self._wm.estimate_messages([message])
        content = str(getattr(message, "content", str(message)))
        tool_calls = getattr(message, "tool_calls", None)
        extra = 0
        if tool_calls:
            for tc in tool_calls:
                if isinstance(tc, dict):
                    extra += self.count_text(str(tc.get("args", "") or ""))
        return max(1, len(content) // 4) + extra + 4  # +4 per-message overhead

    def count_messages(self, messages: Sequence[Any]) -> int:
        """Count tokens in a list of messages."""
        if self._wm is not None:
            return self._wm.estimate_messages(list(messages))
        return sum(self.count_message(m) for m in messages)


# ===========================================================================
# JSON extraction helpers
# ===========================================================================


def _extract_json(text: str) -> dict[str, Any]:
    """Try to parse JSON from an LLM response, handling markdown fences."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    return {}


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ===========================================================================
# Fact normalization helpers
# ===========================================================================


def _normalize_fact_text(text: str) -> str:
    """Normalize a fact string for deduplication comparison."""
    t = text.lower().strip()
    t = re.sub(r"(\d+)%", r"\1 percent", t)
    t = re.sub(r"\s+", " ", t)
    stopwords = {
        "the", "a", "an", "is", "are", "was", "were", "has", "have", "had",
        "will", "would", "shall", "should", "may", "might", "can", "could",
        "this", "that", "these", "those", "it", "its", "they", "them",
        "by", "in", "of", "at", "on", "to", "for", "with", "from",
        "and", "or", "but", "as", "than", "then", "also", "just",
    }
    words = [w for w in re.findall(r"\w+", t) if w not in stopwords]
    return " ".join(words)


def evidence_quality_from_sources(source_ids: Sequence[str]) -> str:
    """Derive evidence_quality mechanically from independent source count.

    Shared by the compressor (fresh facts) and the reconciler (facts whose
    source_ids grow as later rounds corroborate them) so the same fact
    always gets the same quality label for the same source count, whether
    it's evaluated on first extraction or after a cross-round merge.
    """
    distinct_sources = len(set(source_ids))
    if distinct_sources >= 2:
        return "high"
    if distinct_sources == 1:
        return "medium"
    return "low"


def _stable_message_id(msg: Any, occurrence_key: str = "") -> str:
    """Return a stable ID for a message object WITHOUT list-index dependency.

    Uses ``msg.id`` if available, otherwise role + content + tool_call_id +
    tool name + occurrence_key for stability across list mutations.
    """
    msg_id = getattr(msg, "id", None)
    if msg_id:
        return str(msg_id)

    # Build stable identifier from intrinsic properties only
    role = getattr(msg, "type", "unknown")
    content = str(getattr(msg, "content", str(msg)))
    tool_call_id = getattr(msg, "tool_call_id", None)
    tool_name = getattr(msg, "name", None)

    parts = [role, content]
    if tool_call_id:
        parts.append(str(tool_call_id))
    if tool_name:
        parts.append(str(tool_name))
    if occurrence_key:
        parts.append(occurrence_key)

    stable_key = "|".join(parts)
    return hashlib.md5(stable_key.encode()).hexdigest()[:16]
