"""
Search result cleaning pipeline stages.

Each stage is a ``ProcessingStage`` that receives a list of result dicts and
returns a (usually shorter / cleaner) list of dicts.

The six standard stages
========================
1. **DeduplicateStage** — drop near-duplicates (exact URL + Jaccard on title)
2. **CleanTextStage** — strip HTML, collapse whitespace, drop noise
3. **RelevanceFilterStage** — keyword-gate + optional cheap-LLM binary filter
4. **QualityFilterStage** — anti-spam: fact density, SEO-filler detection, domain blocklist
5. **StructureFactsStage** — extract numbers / dates / sentiment from content
6. **FormatDocumentStage** — format as ``<Document>`` XML for the LLM
"""
from __future__ import annotations

import html
import re

from harness.tools.pipeline import ProcessingStage, ToolContext

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _jaccard(a: str, b: str) -> float:
    """Jaccard similarity on word-level tokens (fast, no deps)."""
    sa, sb = set(a.lower().split()), set(b.lower().split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


_TAG_RE = re.compile(r"<[^>]*>")


def _strip_html(text: str) -> str:
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return text


# ---------------------------------------------------------------------------
# Stage 1 — Deduplication
# ---------------------------------------------------------------------------

class DeduplicateStage(ProcessingStage):
    """Remove duplicate results (exact URL match + near-duplicate title)."""

    name = "dedup"

    def __init__(self, title_similarity_threshold: float = 0.85):
        self.threshold = title_similarity_threshold

    def __call__(self, data: list[dict], ctx: ToolContext) -> list[dict]:
        seen_urls: set[str] = set()
        unique: list[dict] = []
        for doc in data:
            url = str(doc.get("url", "") or "").strip()
            title = str(doc.get("title", "") or "").strip()

            # 1) exact URL dedup
            if url and url in seen_urls:
                continue

            # 2) near-duplicate title check
            is_dup = False
            for u in unique:
                utitle = str(u.get("title", "") or "")
                if _jaccard(title, utitle) > self.threshold:
                    # Keep the longer content
                    if len(doc.get("content", "") or "") > len(u.get("content", "") or ""):
                        unique.remove(u)
                        seen_urls.discard(str(u.get("url", "") or ""))
                    else:
                        is_dup = True
                    break
            if is_dup:
                continue

            seen_urls.add(url)
            unique.append(doc)
        return unique


# ---------------------------------------------------------------------------
# Stage 2 — Text cleanup
# ---------------------------------------------------------------------------

class CleanTextStage(ProcessingStage):
    """Strip HTML tags, collapse whitespace, filter out ultra-short content."""

    name = "clean_text"

    def __init__(self, min_content_length: int = 100):
        self.min_length = min_content_length

    def __call__(self, data: list[dict], ctx: ToolContext) -> list[dict]:
        cleaned: list[dict] = []
        for doc in data:
            content = str(doc.get("content", "") or doc.get("snippet", "") or "")
            content = _strip_html(content)
            # Collapse whitespace
            content = re.sub(r"\s+", " ", content).strip()

            if len(content) < self.min_length:
                continue  # too short → noise

            title = str(doc.get("title", "") or "")
            title = _strip_html(title).strip()

            doc["content"] = content
            doc["title"] = title
            cleaned.append(doc)
        return cleaned


# ---------------------------------------------------------------------------
# Stage 3 — Relevance filter
# ---------------------------------------------------------------------------

class RelevanceFilterStage(ProcessingStage):
    """Two-pass relevance gate on the target entity.

    1. **Keyword pass** — keep docs that mention the target entity or have
       sufficient keyword density (fast, no LLM cost).
    2. **Cheap-LLM pass** (optional) — if *ctx.cheap_llm* is set and results
       are still numerous, run a binary relevant / not-relevant classification.
    """

    name = "relevance"

    def __init__(self, keyword_density_min: float = 0.01, llm_filter_threshold: int = 10):
        self.density_min = keyword_density_min
        self.llm_threshold = llm_filter_threshold

    def __call__(self, data: list[dict], ctx: ToolContext) -> list[dict]:
        target = ctx.target_entity.lower().strip()
        if not target:
            return data  # nothing to filter against

        # ---- keyword pass ----
        keyword_pass: list[dict] = []
        for doc in data:
            text = (str(doc.get("title", "") or "") + " " + str(doc.get("content", "") or "")).lower()
            if target in text:
                keyword_pass.append(doc)
                continue
            if self._keyword_density(text, target) >= self.density_min:
                keyword_pass.append(doc)
                continue
            # dropped
        # ---- LLM pass (if enabled and needed) ----
        if ctx.cheap_llm and len(keyword_pass) > self.llm_threshold:
            keyword_pass = self._llm_filter(keyword_pass, target, ctx)
        return keyword_pass

    # ------------------------------------------------------------------
    def _keyword_density(self, text: str, target: str, window: int = 50) -> float:
        """Fraction of *window*-word spans that contain *target*."""
        words = text.split()
        if len(words) < window:
            return 1.0 if target in text else 0.0
        hits = sum(
            1
            for i in range(len(words) - window + 1)
            if target in " ".join(words[i : i + window])
        )
        return hits / (len(words) - window + 1)

    def _llm_filter(self, docs: list[dict], target: str, ctx: ToolContext) -> list[dict]:
        """Use a cheap LLM to binary-classify each doc as relevant/not."""
        kept: list[dict] = []
        for doc in docs:
            prompt = (
                f"Target entity: {target}\n"
                f"Title: {doc.get('title', '')}\n"
                f"Content: {(doc.get('content', '') or '')[:500]}\n\n"
                f"Does this document contain substantive information about {target}? "
                f"Answer YES or NO only."
            )
            try:
                resp = ctx.cheap_llm.invoke(prompt)
                if hasattr(resp, "content"):
                    answer = resp.content.strip().upper()
                else:
                    answer = str(resp).strip().upper()
                if answer.startswith("YES"):
                    kept.append(doc)
            except Exception:
                kept.append(doc)  # on error, keep (fail-open for safety)
        return kept


# ---------------------------------------------------------------------------
# Stage 4 — Quality filter (anti-spam)
# ---------------------------------------------------------------------------

# Domains that consistently produce low-value / SEO-spam content
_SPAM_DOMAIN_PATTERNS: list[str] = [
    # Add domains as you encounter them, e.g.:
    # "example-spam-site.com",
]

# Phrases that strongly indicate auto-generated / content-farm text
_SEO_FILLER_PHRASES: list[str] = [
    "in recent years", "in the field of", "it is worth noting that",
    "has been widely", "more and more", "with the development of",
    "近年来", "随着……的发展", "值得注意", "越来越多", "众所周知",
    "小编", "点击查看", "详情请见", "阅读全文",
]


class QualityFilterStage(ProcessingStage):
    """Score content quality and drop obvious spam / content-farm pages.

    Uses three cheap signals (zero LLM cost):
    1. **Source credibility** — known spam domains are dropped outright.
    2. **Fact density** — pages without numbers or dates are likely SEO fluff.
    3. **SEO-filler ratio** — generic auto-generated phrases raise a red flag.
    """

    name = "quality"

    def __init__(
        self,
        min_fact_count: int = 1,
        max_filler_ratio: float = 0.05,
    ):
        self.min_fact_count = min_fact_count
        self.max_filler_ratio = max_filler_ratio

    def __call__(self, data: list[dict], ctx: ToolContext) -> list[dict]:
        kept: list[dict] = []
        for doc in data:
            url = str(doc.get("url", "") or "").lower()
            content = str(doc.get("content", "") or "")

            # 1) Domain blocklist
            if self._is_spam_domain(url):
                continue

            # 2) Fact density — count numbers + dates
            numbers = _NUMBER_RE.findall(content)
            dates = _DATE_RE.findall(content)
            fact_count = len(numbers) + len(dates)
            if fact_count < self.min_fact_count:
                continue  # no concrete facts → likely fluff

            # 3) SEO-filler ratio
            filler_count = sum(
                1 for phrase in _SEO_FILLER_PHRASES if phrase.lower() in content.lower()
            )
            word_count = len(content.split())
            if word_count > 20 and (filler_count / word_count) > self.max_filler_ratio:
                continue  # too much filler → likely auto-generated

            kept.append(doc)
        return kept

    @staticmethod
    def _is_spam_domain(url: str) -> bool:
        for pattern in _SPAM_DOMAIN_PATTERNS:
            if pattern in url:
                return True
        return False


# ---------------------------------------------------------------------------
# Stage 5 — Structure facts
# ---------------------------------------------------------------------------

_NUMBER_RE = re.compile(
    r"\$?\b\d{1,3}(?:,\d{3})*(?:\.\d+)?\s?(?:million|billion|trillion|%|percent|k|M|B|T)?\b",
    re.IGNORECASE,
)
_DATE_RE = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}\s(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s\d{4}|(?:20\d{2}|Q[1-4]\s?20\d{2}))\b",
    re.IGNORECASE,
)


class StructureFactsStage(ProcessingStage):
    """Extract structured metadata from document content — numbers, dates, sentiment.

    Does **not** call an LLM; purely regex + heuristics.  This is fast and free.
    """

    name = "structure"

    def __call__(self, data: list[dict], ctx: ToolContext) -> list[dict]:
        for doc in data:
            content = str(doc.get("content", "") or "")
            numbers = _NUMBER_RE.findall(content)
            dates = _DATE_RE.findall(content)
            doc["structured"] = {
                "numbers": list(set(numbers))[:10],
                "dates": list(set(dates))[:5],
                "sentiment": self._classify_sentiment(content),
                "char_count": len(content),
            }
        return data

    @staticmethod
    def _classify_sentiment(text: str) -> str:
        pos = ["growth", "profit", "increase", "leader", "innovate",
               "breakthrough", "opportunity", "strong", "advantage"]
        neg = ["risk", "decline", "loss", "threat", "lawsuit", "fine",
               "sanction", "investigation", "weakness", "vulnerable"]
        lo = text.lower()
        p = sum(1 for w in pos if w in lo)
        n = sum(1 for w in neg if w in lo)
        if p > n + 1:
            return "positive"
        if n > p + 1:
            return "negative"
        return "neutral"


# ---------------------------------------------------------------------------
# Stage 5 — Format for LLM
# ---------------------------------------------------------------------------

class FormatDocumentStage(ProcessingStage):
    """Render each document as an XML ``<Document>`` block for LLM consumption."""

    name = "format"

    def __call__(self, data: list[dict], ctx: ToolContext) -> list[dict]:
        """Add a *formatted* key with the LLM-ready string."""
        for i, doc in enumerate(data):
            href = doc.get("url", "#") or "#"
            title = doc.get("title", "") or "Untitled"
            content = doc.get("content", "") or ""
            structured = doc.get("structured", {})

            parts = [f'<Document index="{i+1}" href="{href}" title="{title}">']
            if structured:
                numbers = structured.get("numbers", [])
                if numbers:
                    parts.append(f"  <Numbers>{', '.join(numbers[:10])}</Numbers>")
                dates = structured.get("dates", [])
                if dates:
                    parts.append(f"  <Dates>{', '.join(dates[:5])}</Dates>")
                sentiment = structured.get("sentiment", "")
                if sentiment:
                    parts.append(f"  <Sentiment>{sentiment}</Sentiment>")
            parts.append(f"  <Content>{content}</Content>")
            parts.append("</Document>")

            doc["formatted"] = "\n".join(parts)
        return data


# ---------------------------------------------------------------------------
# Pre-built pipeline presets
# ---------------------------------------------------------------------------

SEARCH_PIPELINE_BASIC = [DeduplicateStage(), CleanTextStage(), FormatDocumentStage()]

SEARCH_PIPELINE_FULL = [
    DeduplicateStage(),
    CleanTextStage(),
    RelevanceFilterStage(),
    QualityFilterStage(),
    StructureFactsStage(),
    FormatDocumentStage(),
]
