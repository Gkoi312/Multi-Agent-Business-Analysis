"""
Tavily Search adapter — uses official ``tavily-python`` SDK.
"""
from __future__ import annotations

import os
from typing import Any

from harness.tools.search.base import SearchDocument, SearchQuery, SearchTool

_FRESHNESS_TO_TIME_RANGE: dict[str, str] = {
    "recent": "week",
    "balanced": "month",
}


def _safe_float(value: object) -> float | None:
    """Convert *value* to float, returning None if not possible."""
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


class TavilyAdapter(SearchTool):
    """Search adapter for the Tavily API using the official ``tavily-python`` SDK.

    Maps ``SearchQuery`` fields:
    - max_results → max_results
    - site_hints → include_domains
    - source_type="news" → topic="news", else topic="general"
    - freshness_hint="recent" → time_range="week", "balanced" → "month", "any" → omitted
    """

    name = "tavily"

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or os.getenv("TAVILY_API_KEY", "")
        self._client: Any = None

    @property
    def _tavily(self):
        """Lazy-init the TavilyClient from the official SDK."""
        if self._client is None:
            if not self._api_key:
                raise ValueError(
                    "TAVILY_API_KEY is missing. Set it as an environment variable "
                    "or pass api_key= to TavilyAdapter()."
                )
            from tavily import TavilyClient

            self._client = TavilyClient(api_key=self._api_key)
        return self._client

    # ------------------------------------------------------------------
    def search(self, query: SearchQuery, **kwargs) -> list[SearchDocument]:
        """Execute a search via the Tavily API and return SearchDocument results."""
        # Build API arguments — only pass non-None, non-empty values
        api_kwargs: dict[str, Any] = {
            "query": query.query,
            "max_results": query.max_results,
        }

        if query.site_hints:
            api_kwargs["include_domains"] = list(query.site_hints)[:5]

        if query.source_type == "news":
            api_kwargs["topic"] = "news"
        else:
            api_kwargs["topic"] = "general"

        time_range = _FRESHNESS_TO_TIME_RANGE.get(query.freshness_hint)
        if time_range:
            api_kwargs["time_range"] = time_range

        # Caller overrides
        api_kwargs.update(kwargs)

        # Remove None/empty values that the API would reject
        api_kwargs = {k: v for k, v in api_kwargs.items() if v is not None and v != ""}

        # Compute effective max_results AFTER kwargs merge — use same value
        # for API request and return slicing
        effective_max_results = max(1, int(api_kwargs.get("max_results", query.max_results)))

        try:
            response: dict[str, Any] = self._tavily.search(**api_kwargs)
        except Exception as e:
            raise RuntimeError(
                f"Tavily API search failed for query={query.query!r}: {e}"
            ) from e

        if not isinstance(response, dict):
            raise RuntimeError(
                f"Tavily API returned unexpected type {type(response).__name__} "
                f"for query={query.query!r}"
            )

        raw_results = response.get("results")
        if raw_results is None or not isinstance(raw_results, list):
            raise RuntimeError(
                f"Tavily API response missing valid 'results' field "
                f"for query={query.query!r}: got {type(raw_results).__name__}"
            )

        results: list[SearchDocument] = []
        for doc in raw_results:
            if not isinstance(doc, dict):
                continue
            try:
                results.append(
                    SearchDocument(
                        url=str(doc.get("url", "") or ""),
                        canonical_url=str(doc.get("url", "") or ""),
                        title=str(doc.get("title", "") or ""),
                        raw_content=str(doc.get("content", "") or ""),
                        published_date=str(doc.get("published_date", "") or ""),
                        source_type=query.source_type,
                        provider=self.name,
                        provider_score=_safe_float(doc.get("score")),
                        raw=doc,
                    )
                )
            except Exception:
                # Single malformed result shouldn't crash the whole search
                continue

        return results[:effective_max_results]

    def health_check(self) -> bool:
        return bool(self._api_key)
