"""
Jina Reader — free URL-to-markdown service.  No API key required.

Usage::

    reader = JinaReader()
    markdown = reader.fetch("https://techcrunch.com/article-slug")
"""
import time
import urllib.request
import urllib.error
import urllib.parse
from dataclasses import dataclass


@dataclass
class BrowseResult:
    url: str
    title: str = ""
    markdown: str = ""
    status_code: int = 0
    error: str = ""


class JinaReader:
    """Fetch a web page as clean markdown via Jina Reader (r.jina.ai)."""

    base_url = "https://r.jina.ai"

    def __init__(self, api_key: str | None = None, timeout: int = 15):
        self._api_key = api_key  # optional — free tier works without one
        self._timeout = timeout

    # ------------------------------------------------------------------
    def fetch(self, url: str) -> BrowseResult:
        """Convert *url* to markdown."""
        reader_url = f"{self.base_url}/{url}"
        req = urllib.request.Request(reader_url, method="GET")
        req.add_header("Accept", "text/markdown")
        if self._api_key:
            req.add_header("Authorization", f"Bearer {self._api_key}")

        started = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return BrowseResult(
                    url=url,
                    markdown=body,
                    status_code=resp.status,
                )
        except urllib.error.HTTPError as exc:
            return BrowseResult(
                url=url,
                status_code=exc.code,
                error=f"HTTP {exc.code}: {exc.reason}",
            )
        except Exception as exc:
            return BrowseResult(
                url=url,
                error=f"{type(exc).__name__}: {exc}",
            )
        finally:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            # could log here

    def fetch_batch(self, urls: list[str], max_concurrent: int = 3) -> list[BrowseResult]:
        """Fetch multiple URLs sequentially (concurrency can be added later)."""
        results: list[BrowseResult] = []
        for url in urls:
            results.append(self.fetch(url))
        return results

    def health_check(self) -> bool:
        try:
            r = self.fetch("https://example.com")
            return r.status_code == 200
        except Exception:
            return False
