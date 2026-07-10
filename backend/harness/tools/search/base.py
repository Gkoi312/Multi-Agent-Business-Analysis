"""
Search tool abstract interface.

Every search backend (Tavily, Brave, Serper, …) implements this protocol
so the harness can swap backends without touching domain code.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SearchResult:
    """Normalised search result, vendor-agnostic."""

    url: str
    title: str
    content: str = ""  # snippet or full text
    published_date: str = ""  # ISO-8601 if known
    source_type: str = "web"  # "web" | "news" | "company" | "academic"
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class SearchQuery:
    """Input to a search backend."""

    query: str
    source_type: str = "web"  # "web" | "news" | "company"
    site_hints: list[str] = field(default_factory=list)  # e.g. ["ft.com", "wsj.com"]
    freshness_hint: str = "balanced"  # "recent" | "balanced" | "any"
    max_results: int = 10

    def to_params(self, backend: str) -> dict[str, Any]:
        """Convert to backend-specific kwargs dict.

        Subclasses / adapters can override for finer control.
        """
        return {
            "query": self.query,
            "max_results": self.max_results,
            "source_type": self.source_type,
            "site_hints": self.site_hints,
            "freshness_hint": self.freshness_hint,
        }


class SearchTool(ABC):
    """Protocol for a search backend."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def search(self, query: SearchQuery, **kwargs) -> list[SearchResult]:
        """Execute a search and return normalised results."""
        ...

    def health_check(self) -> bool:
        """Quick connectivity probe. Override for real checks."""
        return True
