"""
Tavily Search adapter — wraps ``TavilySearchResults`` behind ``SearchTool``.
"""
import os
from typing import Any

from harness.tools.search.base import SearchResult, SearchQuery, SearchTool


class TavilyAdapter(SearchTool):
    """Search adapter for the Tavily API."""

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
    def search(self, query: SearchQuery, **kwargs) -> list[SearchResult]:
        raw_results: list[dict] = self._search.invoke(query.query, **kwargs)
        results: list[SearchResult] = []
        for doc in raw_results or []:
            if not isinstance(doc, dict):
                continue
            results.append(
                SearchResult(
                    url=str(doc.get("url", "") or ""),
                    title=str(doc.get("title", "") or ""),
                    content=str(doc.get("content", "") or ""),
                    source_type=query.source_type,
                    raw=doc,
                )
            )
        return results[: query.max_results]

    def health_check(self) -> bool:
        return bool(self._api_key)
