"""
Bocha Search adapter — Chinese-language search API.

Bocha (bochaai.com) is a domestic search API designed for AI applications.
It provides better Chinese content coverage than Tavily.

Free tier: 100 queries/day. Register at https://open.bochaai.com.
"""
from __future__ import annotations

import json
import os
import urllib.request
import urllib.parse
from typing import Any

from harness.tools.search.base import SearchQuery, SearchResult, SearchTool

# Bocha freshness values
_FRESHNESS_MAP = {
    "recent": "oneWeek",
    "balanced": "noLimit",
    "any": "noLimit",
}


class BochaAdapter(SearchTool):
    """Search adapter for the Bocha AI Search API (web-search endpoint)."""

    name = "bocha"
    endpoint = "https://api.bochaai.com/v1/web-search"

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or os.getenv("BOCHA_API_KEY", "")

    # ------------------------------------------------------------------
    def search(self, query: SearchQuery, **kwargs) -> list[SearchResult]:
        if not self._api_key:
            return []

        freshness = _FRESHNESS_MAP.get(query.freshness_hint, "noLimit")
        params: dict[str, Any] = {
            "query": query.query,
            "count": min(query.max_results, 10),
            "freshness": freshness,
            "summary": True,  # return longer text snippets
        }

        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps(params).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read().decode("utf-8")
                data = json.loads(body)
        except Exception:
            return []

        # Response: { "data": { "webPages": { "value": [...] } } }
        pages = []
        if isinstance(data, dict):
            inner = data.get("data", {})
            if isinstance(inner, dict):
                wp = inner.get("webPages", {})
                if isinstance(wp, dict):
                    pages = wp.get("value", []) or []

        results: list[SearchResult] = []
        for page in pages:
            if not isinstance(page, dict):
                continue
            content = str(
                page.get("summary", "")
                or page.get("snippet", "")
                or page.get("description", "")
                or ""
            )
            results.append(
                SearchResult(
                    url=str(page.get("url", "") or ""),
                    title=str(page.get("name", "") or page.get("title", "") or ""),
                    content=content,
                    published_date=str(page.get("dateLastCrawled", "") or ""),
                    source_type=query.source_type,
                    raw=page,
                )
            )
        return results[: query.max_results]

    def health_check(self) -> bool:
        if not self._api_key:
            return False
        try:
            self.search(SearchQuery(query="test", max_results=1))
            return True
        except Exception:
            return False
