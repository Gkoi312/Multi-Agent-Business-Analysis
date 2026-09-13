"""
Search tool abstract interface and unified document model.

Every search backend (Tavily, Brave, Serper, …) implements the ``SearchTool``
protocol so the harness can swap backends without touching domain code.

The ``SearchDocument`` dataclass is the **single internal data type** for the
entire search pipeline.  Adapters produce it, stages transform it, and the
domain layer consumes it.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Unified search document — the ONE type in the pipeline
# ---------------------------------------------------------------------------

@dataclass
class SearchDocument:
    """Unified document model for search results flowing through the pipeline.

    Design rules (enforced by convention — no runtime validation):
    1. ``raw_content`` is **never** modified by any pipeline stage.
    2. ``clean_content`` is populated by ``CleanTextStage``.
    3. ``agent_content`` is populated by LLM-based stages (future).
    4. ``scores`` holds per-dimension 0-1 scores (relevance, quality, freshness).
    5. ``warnings`` accumulates non-fatal issues found during processing.
    6. ``dropped_reason``, when set, signals the doc should be excluded downstream.

    This is a plain dataclass — no Pydantic dependency.  Pipeline stages
    receive and return ``list[SearchDocument]`` exclusively.
    """

    # ---- identity ----
    url: str = ""
    canonical_url: str = ""  # set by CanonicalizeURLStage (tracking params + fragment removed)

    # ---- content (three tiers) ----
    title: str = ""
    raw_content: str = ""  # as returned by the search provider — NEVER mutate
    clean_content: str = ""  # HTML stripped, whitespace collapsed, noise removed
    agent_content: str = ""  # LLM-summarised / extracted content (future stages)

    # ---- temporal ----
    published_date: str = ""  # ISO-8601 if known

    # ---- provenance ----
    source_type: str = "web"  # "web" | "news" | "company" | "academic"
    provider: str = ""  # "tavily", "bocha", "github", …
    provider_score: float | None = None  # original relevance score from the provider

    # ---- metadata (extensible) ----
    metadata: dict[str, Any] = field(default_factory=dict)
    """Arbitrary key-value metadata (language, content-type, word-count, …)."""

    # ---- structured facts ----
    structured: dict[str, Any] = field(default_factory=dict)
    """Extracted numbers, dates, entities, evidence sentences, sentiment, …."""

    # ---- scores (0.0 – 1.0) ----
    scores: dict[str, float] = field(default_factory=dict)
    """Per-dimension scores: relevance, quality, freshness, …."""

    # ---- lifecycle ----
    warnings: list[str] = field(default_factory=list)
    """Non-fatal issues accumulated during processing."""

    dropped_reason: str = ""
    """When non-empty, this document should be excluded from final output."""

    # ---- raw provider payload (for debugging) ----
    raw: dict[str, Any] = field(default_factory=dict, repr=False)
    """Original provider response, kept for traceability."""


# ---------------------------------------------------------------------------
# Legacy search result — kept for backward compatibility
# ---------------------------------------------------------------------------

@dataclass
class SearchResult:
    """Normalised search result, vendor-agnostic.

    .. deprecated::
        Use ``SearchDocument`` for all new code.  This class is kept for
        consumers that haven't migrated yet.  Convert via
        ``SearchDocument(url=r.url, title=r.title, raw_content=r.content, …)``.
    """

    url: str
    title: str
    content: str = ""  # snippet or full text
    published_date: str = ""  # ISO-8601 if known
    source_type: str = "web"  # "web" | "news" | "company" | "academic"
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_search_document(self) -> SearchDocument:
        """Convert to the unified ``SearchDocument`` model."""
        return SearchDocument(
            url=self.url,
            canonical_url=self.url,
            title=self.title,
            raw_content=self.content,
            source_type=self.source_type,
            published_date=self.published_date,
            raw=self.raw,
        )


# ---------------------------------------------------------------------------
# Search query
# ---------------------------------------------------------------------------

@dataclass
class SearchQuery:
    """Input to a search backend.

    The ``source_type`` field controls which search adapter is used:

    .. list-table::
       :header-rows: 1

       * - source_type
         - Adapter
         - Use case
       * - ``"web"`` (default)
         - Serper / Tavily / Bocha
         - General web search
       * - ``"news"``
         - Serper (news endpoint)
         - Recent news articles
       * - ``"company"``
         - Serper (site-targeted)
         - Company homepage, Crunchbase, LinkedIn
       * - ``"annual"``
         - SEC EDGAR / CNINFO
         - Annual reports (10-K, 20-F, 年报)
       * - ``"quarterly"``
         - SEC EDGAR
         - Quarterly reports (10-Q)
       * - ``"current"``
         - SEC EDGAR / CNINFO
         - Material events (8-K, 临时公告)
       * - ``"ipo"``
         - SEC EDGAR / CNINFO
         - IPO prospectus (S-1, 招股书)
       * - ``"sec"``
         - SEC EDGAR
         - Any SEC filing (US-listed companies)
       * - ``"cninfo"`` / ``"announcement"``
         - CNINFO
         - Chinese listed company disclosures (A股)

    ``site_hints`` narrows the search to specific domains (e.g. ``["36kr.com"]``
    for Chinese tech news) or stock codes for CNINFO.
    """

    query: str
    source_type: str = "web"
    site_hints: list[str] = field(default_factory=list)
    freshness_hint: str = "balanced"  # "recent" | "balanced" | "any"
    max_results: int = 10
    reasoning: str = ""  # why the LLM chose this source_type + query


# ---------------------------------------------------------------------------
# Search tool protocol
# ---------------------------------------------------------------------------

class SearchTool(ABC):
    """Protocol for a search backend."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def search(self, query: SearchQuery, **kwargs) -> list[SearchDocument]:
        """Execute a search and return unified ``SearchDocument`` results."""
        ...

    def health_check(self) -> bool:
        """Quick connectivity probe. Override for real checks."""
        return True
