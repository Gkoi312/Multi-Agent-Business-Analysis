"""
GitHub Repository Search adapter.

Searches public repositories by topic/org/keyword and returns structured
metrics (stars, forks, last push date, language, description, topics).

Free tier: 60 req/hr unauthenticated, 5000 req/hr with a personal access token.
Register at https://github.com/settings/tokens (no special scopes needed).
"""
from __future__ import annotations

import json
import os
import urllib.request
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

from harness.tools.search.base import SearchDocument, SearchQuery, SearchTool


@dataclass
class RepoMetrics:
    """Structured GitHub repository metrics."""

    stars: int = 0
    forks: int = 0
    open_issues: int = 0
    language: str = ""
    license: str = ""
    topics: list[str] = field(default_factory=list)
    last_pushed: str = ""  # ISO date
    created_at: str = ""
    archived: bool = False


class GitHubReposAdapter(SearchTool):
    """Search public GitHub repositories.

    Converts GitHub's REST API response into ``SearchDocument`` items with
    a ``repo_metrics`` field in metadata for downstream analysis.
    """

    name = "github"
    endpoint = "https://api.github.com/search/repositories"

    def __init__(self, api_token: str | None = None):
        self._token = api_token or os.getenv("GITHUB_API_TOKEN", "") or None

    # ------------------------------------------------------------------
    def search(self, query: SearchQuery, **kwargs) -> list[SearchDocument]:
        q_parts = [query.query]
        # Narrow by language if hints suggest it
        lang_hints = {
            "python", "javascript", "typescript", "go", "rust",
            "java", "c", "cpp", "c++", "ruby", "swift", "kotlin",
        }
        for hint in query.site_hints or []:
            h = hint.lower()
            if h in lang_hints:
                q_parts.append(f"language:{h}")
            else:
                q_parts.append(h)

        params: dict[str, Any] = {
            "q": " ".join(q_parts),
            "sort": "stars",
            "order": "desc",
            "per_page": min(query.max_results, 10),
        }
        url = f"{self.endpoint}?{urllib.parse.urlencode(params)}"

        req = urllib.request.Request(url, method="GET")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("User-Agent", "AgentHarness/0.1")
        if self._token:
            req.add_header("Authorization", f"Bearer {self._token}")

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read().decode("utf-8")
                data = json.loads(body)
        except Exception:
            return []

        results: list[SearchDocument] = []
        items = data.get("items", []) if isinstance(data, dict) else []
        for repo in items:
            if not isinstance(repo, dict):
                continue
            full_name = repo.get("full_name", "")
            desc = repo.get("description", "") or ""
            topics = repo.get("topics", []) or []
            metrics = RepoMetrics(
                stars=repo.get("stargazers_count", 0),
                forks=repo.get("forks_count", 0),
                open_issues=repo.get("open_issues_count", 0),
                language=repo.get("language", "") or "",
                license=(repo.get("license") or {}).get("spdx_id", "") if repo.get("license") else "",
                topics=[str(t) for t in topics],
                last_pushed=repo.get("pushed_at", "") or "",
                created_at=repo.get("created_at", "") or "",
                archived=bool(repo.get("archived", False)),
            )
            results.append(
                SearchDocument(
                    url=repo.get("html_url", ""),
                    canonical_url=repo.get("html_url", ""),
                    title=full_name,
                    raw_content=(
                        f"Repository: {full_name}\n"
                        f"Description: {desc}\n"
                        f"Stars: {metrics.stars} | Forks: {metrics.forks} | "
                        f"Open Issues: {metrics.open_issues}\n"
                        f"Language: {metrics.language} | License: {metrics.license}\n"
                        f"Topics: {', '.join(topics)}\n"
                        f"Last pushed: {metrics.last_pushed}\n"
                        f"Archived: {metrics.archived}"
                    ),
                    source_type=query.source_type,
                    provider=self.name,
                    metadata={"repo_metrics": metrics.__dict__},
                    raw={**repo, "repo_metrics": metrics.__dict__},
                )
            )
        return results

    def health_check(self) -> bool:
        try:
            req = urllib.request.Request(
                "https://api.github.com/search/repositories?q=test&per_page=1",
                method="GET",
            )
            req.add_header("Accept", "application/vnd.github+json")
            req.add_header("User-Agent", "AgentHarness/0.1")
            if self._token:
                req.add_header("Authorization", f"Bearer {self._token}")
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status == 200
        except Exception:
            return False
