"""
Serper Search adapter — Google Search via serper.dev.

Serper is a fast, affordable Google Search API designed for AI agents.
It returns clean structured results with titles, links, and snippets.

Free tier: 2,500 queries/month. Register at https://serper.dev.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

from harness.tools.search.base import SearchDocument, SearchQuery, SearchTool

# Serper endpoint mapping
_ENDPOINTS: dict[str, str] = {
    "web": "https://google.serper.dev/search",
    "news": "https://google.serper.dev/news",
}


class SerperAdapter(SearchTool):
    """Search adapter for the Serper.dev Google Search API.

    Maps ``SearchQuery`` fields:
    - max_results → num
    - site_hints → appended to query as ``site:domain`` clauses
    - source_type="news" → news endpoint, otherwise web endpoint
    - freshness_hint → tbs parameter ("recent" → "qdr:w")
    """

    name = "serper"

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or os.getenv("SERPER_API_KEY", "")

    # ------------------------------------------------------------------
    def search(self, query: SearchQuery, **kwargs) -> list[SearchDocument]:
        if not self._api_key:
            return []

        # Resolve endpoint
        source = query.source_type or "web"
        endpoint = _ENDPOINTS.get(source, _ENDPOINTS["web"])

        # Build query string — append site: hints
        q = query.query
        if query.site_hints:
            site_clauses = " OR ".join(f"site:{h}" for h in query.site_hints[:5])
            q = f"{q} ({site_clauses})"

        body: dict[str, Any] = {
            "q": q,
            "num": min(query.max_results, 10),
        }

        # Freshness
        freshness_map = {
            "recent": "qdr:w",
            "balanced": None,
            "any": None,
        }
        tbs = freshness_map.get(query.freshness_hint)
        if tbs:
            body["tbs"] = tbs

        req = urllib.request.Request(
            endpoint,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-API-KEY": self._api_key,
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return []

        # Serper returns "organic" for web, "news" for news
        items: list[dict] = []
        if isinstance(data, dict):
            items = data.get("organic", []) or data.get("news", []) or []

        results: list[SearchDocument] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            content = str(
                item.get("snippet", "")
                or item.get("description", "")
                or ""
            )
            results.append(
                SearchDocument(
                    url=str(item.get("link", "") or ""),
                    canonical_url=str(item.get("link", "") or ""),
                    title=str(item.get("title", "") or ""),
                    raw_content=content,
                    published_date=str(item.get("date", "") or ""),
                    source_type=query.source_type,
                    provider=self.name,
                    raw=item,
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
