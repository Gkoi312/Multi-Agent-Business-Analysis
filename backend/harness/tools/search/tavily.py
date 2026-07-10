"""
Tavily Search adapter — wraps ``TavilySearchResults`` behind ``SearchTool``.
"""
import os
from typing import Any

from harness.tools.search.base import SearchDocument, SearchQuery, SearchTool

# Map our freshness hints to Tavily time_range values
_FRESHNESS_TO_TIME_RANGE: dict[str, str] = {
    "recent": "week",
    "balanced": "month",
    "any": "",
}


class TavilyAdapter(SearchTool):
    """Search adapter for the Tavily API.

    Maps ``SearchQuery`` fields to Tavily API parameters:
    - max_results → max_results (passed at API level)
    - site_hints → include_domains
    - source_type → topic ("news" → "news", else "general")
    - freshness_hint → time_range
    """

    name = "tavily"

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or os.getenv("TAVILY_API_KEY", "")
        self._client: Any = None

    @property
    def _search(self):
        """Lazy-import the LangChain Tavily wrapper so importing this module
        does not fail when ``langchain-community`` is not installed."""
        if self._client is None:
            if not self._api_key:
                raise ValueError("TAVILY_API_KEY is missing.")
            from langchain_community.tools.tavily_search import TavilySearchResults

            self._client = TavilySearchResults(tavily_api_key=self._api_key)
        return self._client

    # ------------------------------------------------------------------
    def search(self, query: SearchQuery, **kwargs) -> list[SearchDocument]:
        # Build Tavily-specific API params from SearchQuery
        api_kwargs: dict[str, Any] = {}

        # max_results passed at API level, not post-hoc slicing
        api_kwargs["max_results"] = query.max_results

        # site_hints → include_domains
        if query.site_hints:
            api_kwargs["include_domains"] = list(query.site_hints)[:5]

        # source_type → topic
        if query.source_type == "news":
            api_kwargs["topic"] = "news"
        else:
            api_kwargs["topic"] = "general"

        # freshness_hint → time_range
        time_range = _FRESHNESS_TO_TIME_RANGE.get(query.freshness_hint)
        if time_range:
            api_kwargs["time_range"] = time_range

        # Allow caller overrides via kwargs
        api_kwargs.update(kwargs)

        raw_results: list[dict] = self._search.invoke({"query": query.query, **api_kwargs})
        results: list[SearchDocument] = []
        for doc in raw_results or []:
            if not isinstance(doc, dict):
                continue
            results.append(
                SearchDocument(
                    url=str(doc.get("url", "") or ""),
                    canonical_url=str(doc.get("url", "") or ""),
                    title=str(doc.get("title", "") or ""),
                    raw_content=str(doc.get("content", "") or ""),
                    published_date=str(doc.get("published_date", "") or ""),
                    source_type=query.source_type,
                    provider=self.name,
                    provider_score=float(doc.get("score", 0)) if doc.get("score") else None,
                    raw=doc,
                )
            )
        return results[: query.max_results]

    def health_check(self) -> bool:
        return bool(self._api_key)
