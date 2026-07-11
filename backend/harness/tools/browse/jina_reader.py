"""
Web page fetch adapters — URL → cleaned text, bridged into the search pipeline.

Two backends:

- **DirectReader** (default): fetches HTML directly via urllib, extracts text
  with html.parser (stdlib).  Zero external dependencies, works everywhere,
  handles Chinese encodings (GBK/GB2312).
- **JinaReader**: uses r.jina.ai for AI-cleaned markdown.  Free, no key needed,
  but may not be reachable from all networks.

Both return ``BrowseResult`` which bridges to ``SearchDocument`` via
``to_search_document()`` so the cleaning pipeline processes browse results
identically to search results.

Usage::

    reader = DirectReader()
    result = reader.fetch("https://example.com/article")
    doc = result.to_search_document(source_type="web")  # → SearchDocument
"""
from __future__ import annotations

import html as html_mod
import re
import time
import urllib.request
import urllib.error
import urllib.parse
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

from harness.tools.search.base import SearchDocument


# ---------------------------------------------------------------------------
# Unified result type
# ---------------------------------------------------------------------------

@dataclass
class BrowseResult:
    url: str
    title: str = ""
    markdown: str = ""          # cleaned text content (no HTML tags)
    raw_html: str = ""          # original HTML, kept for debugging
    status_code: int = 0
    error: str = ""

    def to_search_document(
        self, source_type: str = "web", provider: str = "direct"
    ) -> SearchDocument:
        """Bridge to the search cleaning pipeline.

        The cleaned text becomes ``raw_content`` so downstream stages
        (dedup, quality, structure, format) can process it identically
        to search results.  Unlike search snippets, full-page content
        can be very long — ``OutputGuardStage`` handles truncation.
        """
        title = self.title
        if not title:
            title = self.url

        return SearchDocument(
            url=self.url,
            canonical_url=self.url,
            title=title,
            raw_content=self.markdown,
            source_type=source_type,
            provider=provider,
            raw={"status_code": self.status_code, "error": self.error},
        )


# ---------------------------------------------------------------------------
# HTML text extractor (stdlib, no BeautifulSoup dependency)
# ---------------------------------------------------------------------------

class _TextExtractor(HTMLParser):
    """Pull visible text and <title> out of HTML.

    Skips <script>, <style>, <head>, <nav>, <footer> content.
    Collapses whitespace.
    """

    def __init__(self):
        super().__init__()
        self._skip_tags: set[str] = {"script", "style", "head", "nav", "footer", "noscript"}
        self._skip_depth = 0
        self._in_title = False
        self.title: str = ""
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        tag_lower = tag.lower()
        if tag_lower in self._skip_tags:
            self._skip_depth += 1
        elif tag_lower == "title":
            self._in_title = True

    def handle_endtag(self, tag: str):
        tag_lower = tag.lower()
        if tag_lower in self._skip_tags and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag_lower == "title":
            self._in_title = False

    def handle_data(self, data: str):
        if self._in_title:
            self.title += data
        elif self._skip_depth == 0:
            stripped = data.strip()
            if stripped:
                self.text_parts.append(stripped)


_HEADER_RE = re.compile(r"<head[^>]*>.*?</head>", re.DOTALL | re.IGNORECASE)


def _extract_text_from_html(raw_html: str) -> tuple[str, str]:
    """Return (title, visible_text) from raw HTML.

    Pre-extracts <title> via regex as a fallback, then runs the HTMLParser.
    """
    # Fast title extraction from head block
    title = ""
    head_match = _HEADER_RE.search(raw_html)
    if head_match:
        head_block = head_match.group(0)
        title_match = re.search(r"<title[^>]*>(.*?)</title>", head_block, re.DOTALL | re.IGNORECASE)
        if title_match:
            title = html_mod.unescape(title_match.group(1).strip())

    # Parse body text
    extractor = _TextExtractor()
    try:
        extractor.feed(raw_html)
    except Exception:
        pass  # best-effort

    # Prefer parser title, fall back to regex
    if extractor.title and extractor.title.strip():
        title = extractor.title.strip()

    text = "\n\n".join(extractor.text_parts)
    return title, text


# ---------------------------------------------------------------------------
# Encoding helpers for Chinese sites
# ---------------------------------------------------------------------------

# Common Chinese encodings to try when charset isn't declared
_FALLBACK_ENCODINGS = ["utf-8", "gbk", "gb2312", "gb18030", "big5"]

# Pattern to find <meta charset="..."> or <meta ... charset=...> in HTML
_META_CHARSET_RE = re.compile(
    r"""<meta[^>]+charset\s*=\s*["']?\s*([^"'\s>;]+)""",
    re.IGNORECASE,
)


def _guess_charset_from_body(data: bytes) -> str | None:
    """Peek at the first 2048 bytes of HTML for a <meta charset> declaration."""
    peek = data[:2048]
    # Try common encodings to decode the peek
    for enc in _FALLBACK_ENCODINGS:
        try:
            text = peek.decode(enc, errors="strict")
            match = _META_CHARSET_RE.search(text)
            if match:
                return match.group(1).strip().lower()
        except (UnicodeDecodeError, LookupError):
            continue
    return None


def _decode_body(data: bytes, content_type: str = "") -> str:
    """Decode response body, trying declared charset, meta charset, then common encodings.

    Uses a "fewest replacement characters" heuristic to pick the best encoding
    when the declared one produces garbled text.
    """
    # 1. Try charset from HTTP Content-Type header
    charset_match = re.search(r"charset\s*=\s*([^\s;]+)", content_type, re.IGNORECASE)
    declared = (charset_match.group(1) or "").lower() if charset_match else ""

    # 2. Try charset from HTML <meta> tag (many Chinese sites declare it here)
    meta_charset = _guess_charset_from_body(data)

    # Build ordered list of encodings to try
    candidates: list[str] = []
    seen: set[str] = set()
    for enc in (declared, meta_charset):
        if enc and enc not in seen:
            candidates.append(enc)
            seen.add(enc)
    for enc in _FALLBACK_ENCODINGS:
        if enc not in seen:
            candidates.append(enc)
            seen.add(enc)

    # Try each encoding with errors='replace', pick the one with fewest
    # replacement characters (U+FFFD).  This handles sites that declare the
    # wrong charset — a common issue with Chinese web pages.
    best_text = ""
    best_replacements = 1_000_000  # lower is better

    for encoding in candidates:
        try:
            text = data.decode(encoding, errors="replace")
            replacement_count = text.count("�")
            if replacement_count < best_replacements:
                best_replacements = replacement_count
                best_text = text
            if replacement_count == 0:
                break  # perfect decode — stop looking
        except LookupError:
            continue

    return best_text if best_text else data.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


class DirectReader:
    """Fetch a web page directly, converting HTML to clean text.

    Zero external dependencies — urllib + html.parser (stdlib).
    Handles Chinese encodings (GBK/GB2312) and paywall/403 failures gracefully.

    Parameters
    ----------
    timeout:
        Request timeout in seconds.
    user_agent:
        Custom User-Agent header.  Uses a modern Chrome UA by default.
    """

    def __init__(self, timeout: int = 15, user_agent: str = ""):
        self._timeout = timeout
        self._user_agent = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        )

    # ------------------------------------------------------------------
    def fetch(self, url: str) -> BrowseResult:
        """Fetch *url* and extract visible text."""
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", self._user_agent)
        req.add_header("Accept", "text/html,application/xhtml+xml,*/*")
        req.add_header("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8")

        started = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read()
                content_type = resp.headers.get("Content-Type", "")
                html_text = _decode_body(raw, content_type)
                title, text = _extract_text_from_html(html_text)

                return BrowseResult(
                    url=url,
                    title=title,
                    markdown=text,
                    raw_html=html_text,
                    status_code=resp.status,
                )
        except urllib.error.HTTPError as exc:
            body = b""
            try:
                body = exc.read()
            except Exception:
                pass
            return BrowseResult(
                url=url,
                status_code=exc.code,
                error=f"HTTP {exc.code}: {exc.reason}",
                raw_html=_decode_body(body) if body else "",
            )
        except Exception as exc:
            return BrowseResult(
                url=url,
                error=f"{type(exc).__name__}: {exc}",
            )
        finally:
            elapsed_ms = int((time.perf_counter() - started) * 1000)

    def fetch_batch(self, urls: list[str]) -> list[BrowseResult]:
        """Fetch multiple URLs sequentially."""
        return [self.fetch(u) for u in urls]

    def health_check(self) -> bool:
        try:
            r = self.fetch("https://httpbin.org/ip")
            return r.status_code == 200 and bool(r.markdown)
        except Exception:
            return False


class JinaReader:
    """Fetch a web page as AI-cleaned markdown via Jina Reader (r.jina.ai).

    Free tier requires no API key.  May not be reachable from all networks.

    Parameters
    ----------
    api_key:
        Optional Jina API key for higher rate limits.
    timeout:
        Request timeout in seconds.
    """

    base_url = "https://r.jina.ai"

    def __init__(self, api_key: str | None = None, timeout: int = 15):
        self._api_key = api_key
        self._timeout = timeout

    # ------------------------------------------------------------------
    def fetch(self, url: str) -> BrowseResult:
        """Convert *url* to markdown via Jina."""
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

    def fetch_batch(self, urls: list[str]) -> list[BrowseResult]:
        """Fetch multiple URLs sequentially."""
        return [self.fetch(u) for u in urls]

    def health_check(self) -> bool:
        try:
            r = self.fetch("https://example.com")
            return r.status_code == 200
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Composite reader — tries Jina first, falls back to direct
# ---------------------------------------------------------------------------

class CompositeReader:
    """Tries Jina first (better quality), falls back to DirectReader."""

    def __init__(self, timeout: int = 15):
        self._jina = JinaReader(timeout=timeout)
        self._direct = DirectReader(timeout=timeout)

    def fetch(self, url: str) -> BrowseResult:
        result = self._jina.fetch(url)
        if result.error:
            result = self._direct.fetch(url)
        return result

    def fetch_batch(self, urls: list[str]) -> list[BrowseResult]:
        return [self.fetch(u) for u in urls]

    def health_check(self) -> bool:
        return self._direct.health_check()
