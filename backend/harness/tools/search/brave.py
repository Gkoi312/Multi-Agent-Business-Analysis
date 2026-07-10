"""
Brave Search adapter — wraps ``brave-search`` Python package behind ``SearchTool``.

The Brave Search API supports ``site:`` filtering natively, which lets us
activate the ``site_hints`` in skill_pack.yaml for source-diverse search.

Register at https://brave.com/search/api/ to get an API key.
"""
import os
import json
import urllib.request
import urllib.parse
from typing import Any

from harness.tools.search.base import SearchDocument, SearchQuery, SearchTool


class BraveSearchAdapter(SearchTool):
    """Search adapter for the Brave Search API."""

    name = "brave"
    endpoint = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or os.getenv("BRAVE_SEARCH_API_KEY", "")

    # ------------------------------------------------------------------
    def search(self, query: SearchQuery, **kwargs) -> list[SearchDocument]:
        if not self._api_key:
            return []  # not configured → silently return empty (graceful degradation)

        # Build query string with optional site: filters
        q = query.query
        if query.site_hints:
            site_clause = " OR ".join(
                f"site:{s}" for s in query.site_hints[:3]
            )
            q = f"{q} ({site_clause})"

        params = {
            "q": q,
            "count": min(query.max_results, 20),
        }

        url = f"{self.endpoint}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, method="GET")
        req.add_header("Accept", "application/json")
        req.add_header("Accept-Encoding", "gzip")
        req.add_header("X-Subscription-Token", self._api_key)

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read().decode("utf-8")
                data = json.loads(body)
        except Exception:
            return []

        results: list[SearchDocument] = []
        web_results = data.get("web", {}).get("results", []) if isinstance(data, dict) else []
        for item in web_results:
            if not isinstance(item, dict):
                continue
            results.append(
                SearchDocument(
                    url=str(item.get("url", "") or ""),
                    canonical_url=str(item.get("url", "") or ""),
                    title=str(item.get("title", "") or ""),
                    raw_content=str(item.get("description", "") or ""),
                    published_date=str(item.get("age", "") or ""),
                    source_type=query.source_type,
                    provider=self.name,
                    raw=item,
                )
            )
        return results[: query.max_results]

    def health_check(self) -> bool:
        return bool(self._api_key)
