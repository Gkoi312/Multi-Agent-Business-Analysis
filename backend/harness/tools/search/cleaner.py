"""
Search result cleaning pipeline stages.

Each stage is a ``ProcessingStage`` that receives ``list[SearchDocument]`` and
returns a (usually shorter / cleaner) ``list[SearchDocument]``.

The standard stages
===================
1.  **CanonicalizeURLStage** — remove tracking params + fragment, set canonical_url
2.  **CleanTextStage** — strip HTML via HTMLParser, collapse whitespace → clean_content
3.  **ExactDeduplicateStage** — drop docs sharing the same canonical_url (best-wins)
4.  **NearDuplicateStage** — content-hash + bigram-Jaccard for Chinese titles
5.  **RelevanceScoreStage** — keyword-density pre-filter + batch LLM relevance
6.  **QualityScoreStage** — multi-dimension quality scoring (no hard gate on numbers)
7.  **StructureFactsStage** — extract numbers, dates, entities, evidence sentences → structured
8.  **OutputGuardStage** — prompt-injection detection, token/char budget (no XML escaping!)
9.  **FormatDocumentStage** — XML-escape once, render ``<Document>`` for LLM consumption
"""
from __future__ import annotations

import hashlib
import html as html_mod
import logging
import re
from html.parser import HTMLParser
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

from harness.tools.pipeline import ProcessingStage, ToolContext
from harness.tools.search.base import SearchDocument

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_XML_ESCAPE_TABLE = str.maketrans({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&apos;",
})

_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "gclsrc", "fbclid", "msclkid", "dclid", "twclid",
    "igshid", "mc_cid", "mc_eid", "oly_anon_id", "oly_enc_id",
    "_ga", "_gl", "_hsenc", "_hsmi", "__hsfp", "__hstc", "__hssc",
    "ref", "ref_src", "ref_url",
}

# ----- Prompt injection patterns --------------------------------------------

_HIGH_CONFIDENCE_INJECTION: list[re.Pattern] = [
    re.compile(r"ignore\s+(all\s+)?(previous|above)\s+(instructions?|prompts?)", re.IGNORECASE),
    re.compile(r"system\s*(prompt|message)\s*(is|was|:)", re.IGNORECASE),
    re.compile(r"you\s+are\s+(now|no\s+longer)\s+(an?\s+)?(AI|assistant|language\s+model)", re.IGNORECASE),
    re.compile(r"<\|im_start\|>|<\|im_end\|>", re.IGNORECASE),
    re.compile(r"\[system\]|\[/system\]|\[assistant\]|\[/assistant\]", re.IGNORECASE),
    re.compile(r"<\|.*?\|>", re.IGNORECASE),
    re.compile(r"DAN\s+mode|developer\s+mode|jailbreak", re.IGNORECASE),
]

_LOW_CONFIDENCE_INJECTION: list[re.Pattern] = [
    re.compile(r"pretend|roleplay|act\s+as\s+if", re.IGNORECASE),
]

# ----- SEO filler phrases ----------------------------------------------------

_SEO_FILLER_PHRASES: list[str] = [
    "in recent years", "in the field of", "it is worth noting that",
    "has been widely", "more and more", "with the development of",
    "近年来", "随着……的发展", "值得注意", "越来越多", "众所周知",
    "小编", "点击查看", "详情请见", "阅读全文",
    "免责声明", "广告", "推广",
]

_SPAM_DOMAIN_PATTERNS: list[str] = []


def _xml_escape(text: str) -> str:
    """Escape special XML characters in *text*.

    Only called by FormatDocumentStage — never by OutputGuardStage.
    This ensures exactly one escaping pass per pipeline run.
    """
    return text.translate(_XML_ESCAPE_TABLE)


# ----- HTML tag stripper (HTMLParser-based, not regex) -----------------------

# Tags whose entire inner content (including nested tags) is discarded
_SKIP_TAGS = {"script", "style", "noscript", "template", "svg"}

# Block-level tags that should produce a space or newline boundary
_BLOCK_TAGS = {
    "p", "div", "section", "article", "main", "header", "footer",
    "li", "ul", "ol", "br", "h1", "h2", "h3", "h4", "h5", "h6",
    "tr", "td", "th", "blockquote", "pre", "figure", "figcaption",
}


class _TagStripper(HTMLParser):
    """HTMLParser that strips tags while preserving text content.

    - Tags listed in ``_SKIP_TAGS`` (script, style, noscript, template, svg)
      have their **entire content discarded**, including nested children.
    - Block-level tags insert a space or newline boundary so text from
      adjacent elements does not run together.
    - Comparison operators like ``Revenue < 5 & profit > 2`` are preserved
      because ``<`` followed by space/digit is not a tag start.
    """

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self._parts: list[str] = []
        self._skip_depth: int = 0  # >0 → we are inside a SKIP_TAG (supports nesting)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth > 0:
            return
        if tag.lower() in _BLOCK_TAGS:
            self._parts.append(" " if tag.lower() != "br" else "\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in _SKIP_TAGS:
            if self._skip_depth > 0:
                self._skip_depth -= 1
            return
        if self._skip_depth > 0:
            return
        if tag.lower() in _BLOCK_TAGS:
            self._parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        self._parts.append(data)

    def handle_entityref(self, name: str) -> None:
        if self._skip_depth > 0:
            return
        self._parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self._skip_depth > 0:
            return
        self._parts.append(f"&#{name};")

    def get_text(self) -> str:
        return "".join(self._parts)


def _strip_html(text: str) -> str:
    """Remove HTML tags using HTMLParser and unescape entities.

    Safe for text containing comparison operators::

        >>> _strip_html("Revenue < 5 & profit > 2")
        'Revenue < 5 & profit > 2'
        >>> _strip_html("<p>Hello <em>world</em></p>")
        'Hello world'
        >>> _strip_html("<p>Hello</p><script>alert('x')</script><p>World</p>")
        'Hello World'

    ``script``, ``style``, ``noscript``, ``template``, ``svg`` contents are
    discarded entirely.  Block-level tags insert whitespace boundaries.
    """
    stripper = _TagStripper()
    try:
        stripper.feed(text)
    except Exception:
        text = re.sub(r"<[A-Za-z][^>]*>", " ", text)
    else:
        text = stripper.get_text()
    text = html_mod.unescape(text)
    return text


# ----- Bigram Jaccard --------------------------------------------------------


def _bigram_jaccard(a: str, b: str) -> float:
    """Bigram Jaccard for Chinese (character bigrams) and English (word bigrams)."""
    if not a or not b:
        return 0.0

    def _bigrams(text: str) -> set[str]:
        cjk_count = sum(1 for ch in text if '一' <= ch <= '鿿' or '㐀' <= ch <= '䶿')
        if cjk_count > len(text) * 0.3:
            chars = [ch for ch in text if ch.strip()]
            return {"".join(chars[i:i+2]) for i in range(len(chars) - 1)}
        else:
            words = text.lower().split()
            return {" ".join(words[i:i+2]) for i in range(len(words) - 1)}

    sa = _bigrams(a)
    sb = _bigrams(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


# ----- Content fingerprint ---------------------------------------------------


def _content_fingerprint(text: str, n: int = 5) -> str:
    """Compute a lightweight MinHash-ish fingerprint for near-duplicate detection."""
    if not text:
        return ""
    cjk_count = sum(1 for ch in text if '一' <= ch <= '鿿' or '㐀' <= ch <= '䶿')
    if cjk_count > len(text) * 0.3:
        chars = [ch for ch in text if ch.strip()]
        shingles = ["".join(chars[i:i+3]) for i in range(max(1, len(chars) - 2))]
    else:
        words = text.lower().split()
        shingles = [" ".join(words[i:i+3]) for i in range(max(1, len(words) - 2))]

    if len(shingles) < n:
        return hashlib.sha256(text.encode()).hexdigest()[:16]
    hashes = sorted(hashlib.md5(s.encode()).hexdigest() for s in shingles)
    return "|".join(hashes[:n])


# ----- Chinese keyword extraction --------------------------------------------


def _extract_keywords(text: str) -> list[str]:
    """Extract potential Chinese keywords (2-4 character sequences)."""
    return re.findall(r'[一-鿿㐀-䶿]{2,4}', text)


# ----- Sentence splitter (decimal-aware) -------------------------------------


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences, protecting decimal points.

    Temporarily replaces decimal points (``5.5`` → ``5__DOT__5``) before
    splitting on ``. ! ?`` and Chinese terminators, then restores them.
    This avoids false splits on ``$5.5 billion`` or ``12.8%``.
    """
    # Protect decimal points: digit.digit patterns
    protected = re.sub(r"(\d)\.(\d)", r"\1__DOT__\2", text)
    sentences = re.split(r"(?<=[.!?。！？\n])\s*", protected)
    return [s.replace("__DOT__", ".").strip() for s in sentences if s.strip()]


# ===========================================================================
# Stage 1 — Canonicalize URL
# ===========================================================================

class CanonicalizeURLStage(ProcessingStage):
    """Remove tracking query parameters (utm_*, gclid, fbclid, …) and URL fragments."""

    name = "canonicalize_url"

    def __init__(self, extra_tracking_params: set[str] | None = None):
        self.tracking_params = _TRACKING_PARAMS | (extra_tracking_params or set())

    def __call__(
        self, data: list[SearchDocument], ctx: ToolContext
    ) -> list[SearchDocument]:
        for doc in data:
            if not doc.url or not doc.url.strip():
                doc.canonical_url = doc.url
                continue
            try:
                parsed = urlparse(doc.url)
                qsl = parse_qsl(parsed.query, keep_blank_values=True)
                cleaned_qsl = [
                    (k, v) for k, v in qsl
                    if k.lower() not in self.tracking_params
                ]
                cleaned_query = urlencode(cleaned_qsl, doseq=True, quote_via=quote)
                doc.canonical_url = urlunparse((
                    parsed.scheme,
                    parsed.netloc.lower(),
                    parsed.path.rstrip("/") or "/",
                    parsed.params,
                    cleaned_query,
                    "",
                ))
            except Exception:
                doc.canonical_url = doc.url
                doc.warnings.append("url_parse_failed")
        return data


# ===========================================================================
# Stage 2 — Clean text
# ===========================================================================

class CleanTextStage(ProcessingStage):
    """Strip HTML tags via HTMLParser, collapse whitespace, populate ``clean_content``.

    ``script``, ``style``, ``noscript``, ``template``, ``svg`` contents are discarded.
    Block-level tags insert whitespace boundaries.
    **Never** modifies ``raw_content``.
    """

    name = "clean_text"

    def __init__(self, min_content_length: int = 50):
        self.min_length = min_content_length

    def __call__(
        self, data: list[SearchDocument], ctx: ToolContext
    ) -> list[SearchDocument]:
        result: list[SearchDocument] = []
        for doc in data:
            title = _strip_html(doc.title or "")
            title = re.sub(r"\s+", " ", title).strip()

            content = _strip_html(doc.raw_content or "")
            content = re.sub(r"\s+", " ", content).strip()

            if len(content) < self.min_length:
                doc.dropped_reason = f"content_too_short:{len(content)}"
                doc.warnings.append("content_too_short")
                result.append(doc)
                continue

            doc.title = title
            doc.clean_content = content
            result.append(doc)
        return result


# ===========================================================================
# Stage 3 — Exact URL deduplication
# ===========================================================================

class ExactDeduplicateStage(ProcessingStage):
    """Drop documents with duplicate ``canonical_url`` — best-wins, not first-wins."""

    name = "exact_dedup"

    def __call__(
        self, data: list[SearchDocument], ctx: ToolContext
    ) -> list[SearchDocument]:
        url_map: dict[str, list[int]] = {}
        for i, doc in enumerate(data):
            if doc.dropped_reason:
                continue
            key = doc.canonical_url or doc.url
            if not key:
                continue
            url_map.setdefault(key, []).append(i)

        for key, indices in url_map.items():
            if len(indices) <= 1:
                continue

            def _sort_key(idx: int) -> tuple[int, int, float]:
                d = data[idx]
                has_dropped = 1 if d.dropped_reason else 0
                content_len = len(d.clean_content or d.raw_content)
                score = d.provider_score or 0.0
                return (-has_dropped, content_len, score)

            indices.sort(key=_sort_key, reverse=True)
            for idx in indices[1:]:
                data[idx].dropped_reason = "duplicate_url"
                data[idx].warnings.append(
                    f"duplicate_of:{data[indices[0]].canonical_url or data[indices[0]].url}"
                )

        return data


# ===========================================================================
# Stage 4 — Near-duplicate detection
# ===========================================================================

class NearDuplicateStage(ProcessingStage):
    """Detect near-duplicate documents using content fingerprint + title bigram Jaccard."""

    name = "near_dedup"

    def __init__(self, title_similarity_threshold: float = 0.80, fp_threshold: float = 0.85):
        self.title_threshold = title_similarity_threshold
        self.fp_threshold = fp_threshold

    def __call__(
        self, data: list[SearchDocument], ctx: ToolContext
    ) -> list[SearchDocument]:
        fingerprints: dict[int, str] = {}
        for i, doc in enumerate(data):
            if not doc.dropped_reason:
                text = doc.clean_content or doc.raw_content
                fingerprints[i] = _content_fingerprint(text)

        for i in range(len(data)):
            if data[i].dropped_reason:
                continue
            fp_i = fingerprints.get(i, "")
            for j in range(i + 1, len(data)):
                if data[j].dropped_reason:
                    continue
                fp_j = fingerprints.get(j, "")

                fp_sim = 0.0
                if fp_i and fp_j:
                    set_i = set(fp_i.split("|"))
                    set_j = set(fp_j.split("|"))
                    if set_i and set_j:
                        fp_sim = len(set_i & set_j) / len(set_i | set_j)

                title_sim = _bigram_jaccard(data[i].title, data[j].title)
                is_dup = fp_sim > self.fp_threshold or title_sim > self.title_threshold
                if not is_dup:
                    continue

                len_i = len(data[i].clean_content or data[i].raw_content)
                len_j = len(data[j].clean_content or data[j].raw_content)
                if len_i >= len_j:
                    data[j].dropped_reason = "near_duplicate"
                    data[j].warnings.append(
                        f"near_duplicate_of:{data[i].canonical_url or data[i].url}"
                    )
                else:
                    data[i].dropped_reason = "near_duplicate"
                    data[i].warnings.append(
                        f"near_duplicate_of:{data[j].canonical_url or data[j].url}"
                    )
                    break

        return data


# ===========================================================================
# Stage 5 — Relevance score
# ===========================================================================

class RelevanceScoreStage(ProcessingStage):
    """Score each document's relevance to the target entity and focus area.

    Scoring dimensions (all 0.0–1.0 component scores):
    - **Title match** — 1.0 if target entity appears in title, else 0.0.
    - **Content density** — sliding-window keyword density for target_entity.
    - **Focus match** — target_focus keywords (supports Chinese).

    Composite (target only): ``0.35 × title_score + 0.65 × content_score``.
    Composite (target + focus): ``0.30 × title_score + 0.45 × content_score + 0.25 × focus_score``.

    Title-only hit is guaranteed to score ≥ 0.35, above the default 0.15 threshold.

    Batch LLM: splits borderline docs into batches of ``llm_batch_size``.
    LLM and keyword scores are weighted-fused: ``0.4 × keyword + 0.6 × LLM``.
    """

    name = "relevance"

    def __init__(self, score_threshold: float = 0.15, llm_batch_size: int = 5):
        self.score_threshold = score_threshold
        self.llm_batch_size = llm_batch_size

    def __call__(
        self, data: list[SearchDocument], ctx: ToolContext
    ) -> list[SearchDocument]:
        target = ctx.target_entity.lower().strip() if ctx.target_entity else ""
        focus = ctx.target_focus.lower().strip() if ctx.target_focus else ""

        if not target and not focus:
            for doc in data:
                if not doc.dropped_reason:
                    doc.scores["relevance"] = 0.5
            return data

        # ---- Keyword-density scoring ----
        active_docs = [(i, doc) for i, doc in enumerate(data) if not doc.dropped_reason]
        for _, doc in active_docs:
            doc.scores["relevance"] = self._compute_keyword_score(doc, target, focus)

        # ---- Batch LLM pass ----
        if ctx.cheap_llm:
            borderline = [
                (i, doc) for i, doc in active_docs
                if self._eligible_for_llm(doc)
            ]
            if borderline:
                self._batch_llm_score(borderline, target, focus, ctx)

        # ---- Mark below-threshold docs ----
        for doc in data:
            if doc.dropped_reason:
                continue
            if doc.scores.get("relevance", 0.5) < self.score_threshold:
                doc.dropped_reason = "low_relevance"
                doc.warnings.append(
                    f"relevance_score:{doc.scores['relevance']:.2f}"
                )

        return data

    # ------------------------------------------------------------------
    def _compute_keyword_score(
        self, doc: SearchDocument, target: str, focus: str
    ) -> float:
        """Score relevance using separate title and content components."""
        title_lower = (doc.title or "").lower()
        content_lower = (doc.clean_content or doc.raw_content or "").lower()
        words = content_lower.split()

        title_score = 0.0
        content_score = 0.0
        focus_score = 0.0

        if target:
            # Title: 1.0 if target appears, else 0.0
            title_score = 1.0 if target in title_lower else 0.0

            # Content: sliding-window density
            if len(words) >= 10:
                window_size = min(50, len(words))
                window_hits = sum(
                    1 for i in range(len(words) - window_size + 1)
                    if target in " ".join(words[i:i + window_size])
                )
                density = window_hits / max(1, len(words) - window_size + 1)
                content_score = min(1.0, density * 4.0)
            else:
                content_score = 1.0 if target in content_lower else 0.0

        if focus:
            cjk_keywords = _extract_keywords(focus)
            if cjk_keywords:
                focus_hits = sum(1 for kw in cjk_keywords if kw in content_lower)
                focus_score = focus_hits / max(1, len(cjk_keywords)) * 0.5
            else:
                focus_words = focus.split()
                focus_hits = sum(1 for w in focus_words if w in content_lower)
                focus_score = focus_hits / max(1, len(focus_words)) * 0.5

        # Composite — component scores are independent 0.0–1.0
        if target and focus:
            composite = 0.30 * title_score + 0.45 * content_score + 0.25 * focus_score
        elif target:
            composite = 0.35 * title_score + 0.65 * content_score
        else:
            composite = focus_score

        return round(min(1.0, composite), 4)

    def _eligible_for_llm(self, doc: SearchDocument) -> bool:
        """Documents eligible for LLM re-scoring.

        Eligible: keyword score in borderline range (0.0-0.4), OR
        zero keyword hits but high provider_score (>0.7).
        """
        kw_score = doc.scores.get("relevance", 0.0)
        if 0.0 < kw_score < 0.4:
            return True
        if kw_score == 0.0 and (doc.provider_score or 0.0) > 0.7:
            return True
        return False

    def _batch_llm_score(
        self,
        borderline: list[tuple[int, SearchDocument]],
        target: str,
        focus: str,
        ctx: ToolContext,
    ) -> None:
        """Score borderline documents using LLM in batches of ``llm_batch_size``."""
        if not ctx.cheap_llm:
            return

        for batch_start in range(0, len(borderline), self.llm_batch_size):
            batch = borderline[batch_start:batch_start + self.llm_batch_size]
            self._score_single_batch(batch, target, focus, ctx)

    def _score_single_batch(
        self,
        batch: list[tuple[int, SearchDocument]],
        target: str,
        focus: str,
        ctx: ToolContext,
    ) -> None:
        """Score one batch of documents via a single LLM call."""
        focus_str = f"\nFocus area: {focus}" if focus else ""
        items: list[str] = []
        for idx, (_, doc) in enumerate(batch):
            text = (doc.clean_content or doc.raw_content)[:300]
            items.append(
                f"[{idx}] Title: {doc.title}\n    Content: {text}"
            )

        prompt = (
            f"Target entity: {target}{focus_str}\n\n"
            f"For each numbered document below, rate its relevance (0-100) "
            f"to the target entity and focus area. A document is relevant if it "
            f"contains substantive information about the target, not just a passing mention.\n\n"
            f"Output only one line per document: [index]=score\n"
            f"Score must be an integer between 0 and 100.\n\n"
            + "\n\n".join(items)
        )

        try:
            resp = ctx.cheap_llm.invoke(prompt)
            text = getattr(resp, "content", str(resp))
            for line in text.splitlines():
                m = re.match(r"\[(\d+)\]\s*=\s*(\d+)", line.strip())
                if m:
                    idx = int(m.group(1))
                    raw_score = int(m.group(2))
                    llm_score = max(0.0, min(100.0, float(raw_score))) / 100.0
                    if 0 <= idx < len(batch):
                        kw_score = batch[idx][1].scores.get("relevance", 0.5)
                        fused = round(0.4 * kw_score + 0.6 * llm_score, 4)
                        batch[idx][1].scores["relevance"] = fused
        except Exception as e:
            logger.warning("Batch LLM relevance scoring failed: %s", e)
            # fail-open: keep existing keyword scores for this batch


# ===========================================================================
# Stage 6 — Quality score
# ===========================================================================

class QualityScoreStage(ProcessingStage):
    """Score content quality on multiple dimensions — never a single hard gate."""

    name = "quality"

    def __init__(self, score_threshold: float = 0.20):
        self.score_threshold = score_threshold

    def __call__(
        self, data: list[SearchDocument], ctx: ToolContext
    ) -> list[SearchDocument]:
        for doc in data:
            if doc.dropped_reason:
                continue

            content = doc.clean_content or doc.raw_content
            content_lower = content.lower()
            url_lower = (doc.canonical_url or doc.url).lower()

            dimension_scores: dict[str, float] = {}

            dimension_scores["domain"] = 0.0 if self._is_spam_domain(url_lower) else 1.0

            numbers = len(_NUMBER_RE.findall(content))
            dates = len(_DATE_RE.findall(content))
            entities = len(_ENTITY_RE.findall(content))
            fact_score = min(1.0, numbers * 0.15 + dates * 0.25 + entities * 0.2)
            dimension_scores["fact_density"] = fact_score

            filler_occurrences = sum(
                content_lower.count(phrase.lower()) for phrase in _SEO_FILLER_PHRASES
            )
            word_count = len(content.split())
            if word_count > 30:
                filler_ratio = filler_occurrences / word_count
                filler_score = max(0.0, 1.0 - filler_ratio * 50)
            else:
                filler_score = 0.5
            dimension_scores["seo_filler"] = filler_score

            if word_count < 20:
                length_score = 0.1
            elif word_count < 50:
                length_score = 0.4
            elif word_count < 100:
                length_score = 0.7
            else:
                length_score = 1.0
            dimension_scores["length"] = length_score

            weights = {"domain": 0.30, "fact_density": 0.30, "seo_filler": 0.25, "length": 0.15}
            composite = round(sum(dimension_scores[k] * weights[k] for k in weights), 4)
            doc.scores["quality"] = composite
            doc.metadata["quality_dimensions"] = dimension_scores

            if composite < self.score_threshold:
                doc.dropped_reason = f"low_quality:{composite}"
                doc.warnings.append(
                    f"quality_score:{composite};dims:{dimension_scores}"
                )

        return data

    @staticmethod
    def _is_spam_domain(url: str) -> bool:
        return any(pattern in url for pattern in _SPAM_DOMAIN_PATTERNS)


# ===========================================================================
# Stage 7 — Structure facts
# ===========================================================================

# Improved number regex: supports plain integers, comma-separated, decimals,
# percentages, currency symbols, and Chinese/English units.
_NUMBER_RE = re.compile(
    r"""
    (?<![\w.])
    (?:[$€£¥￥]\s*)?
    (?:
        \d{1,3}(?:,\d{3})+
        |
        \d+(?:\.\d+)?
    )
    \s*
    (?:
        %
        |percent
        |thousand
        |million
        |billion
        |trillion
        |k|m|b|t
        |万亿|亿|万
    )?
    (?![\w-])
    """,
    re.IGNORECASE | re.VERBOSE,
)

_DATE_RE = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}\s(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s\d{4}|(?:20\d{2}|Q[1-4]\s?20\d{2}))\b",
    re.IGNORECASE,
)

_ENTITY_RE = re.compile(
    r"\b[A-Z一-鿿][A-Za-z一-鿿]+(?:\s[A-Z一-鿿][A-Za-z一-鿿]+){1,3}\b"
)


class StructureFactsStage(ProcessingStage):
    """Extract structured metadata from document content.

    Populates ``doc.structured`` with numbers, dates, entities, sentiment,
    char_count, and evidence sentences.
    """

    name = "structure"

    def __call__(
        self, data: list[SearchDocument], ctx: ToolContext
    ) -> list[SearchDocument]:
        for doc in data:
            if doc.dropped_reason:
                continue

            content = doc.clean_content or doc.raw_content

            numbers = _NUMBER_RE.findall(content)
            dates = _DATE_RE.findall(content)
            entities = _ENTITY_RE.findall(content)

            # Use decimal-aware sentence splitter
            sentences = _split_sentences(content)
            evidence: list[str] = []
            seen_evidence: set[str] = set()
            if numbers:
                for sent in sentences:
                    if any(n.strip() in sent for n in numbers[:5]):
                        s = sent.strip()
                        if s and s not in seen_evidence:
                            evidence.append(s)
                            seen_evidence.add(s)
                        if len(evidence) >= 3:
                            break

            # Deduplicate while preserving order for numbers/dates
            doc.structured = {
                "numbers": list(dict.fromkeys(n.strip() for n in numbers))[:10],
                "dates": list(dict.fromkeys(d.strip() for d in dates))[:5],
                "entities": list(dict.fromkeys(e.strip() for e in entities))[:10],
                "sentiment": self._classify_sentiment(content),
                "char_count": len(content),
                "evidence": evidence,
            }

        return data

    @staticmethod
    def _classify_sentiment(text: str) -> str:
        pos = [
            "growth", "profit", "increase", "leader", "innovate",
            "breakthrough", "opportunity", "strong", "advantage",
            "增长", "盈利", "领先", "突破", "机遇", "优势",
        ]
        neg = [
            "risk", "decline", "loss", "threat", "lawsuit", "fine",
            "sanction", "investigation", "weakness", "vulnerable",
            "风险", "下降", "损失", "威胁", "诉讼", "罚款",
        ]
        lo = text.lower()
        p = sum(1 for w in pos if w in lo)
        n = sum(1 for w in neg if w in lo)
        if p > n + 1:
            return "positive"
        if n > p + 1:
            return "negative"
        return "neutral"


# ===========================================================================
# Stage 8 — Output guard
# ===========================================================================

class OutputGuardStage(ProcessingStage):
    """Guard against unsafe content — injection detection + budget truncation.

    Does **NOT** XML-escape — that happens exactly once in FormatDocumentStage.
    """

    name = "output_guard"

    def __init__(
        self,
        max_content_chars: int = 8000,
        max_title_chars: int = 300,
    ):
        self.max_content_chars = max_content_chars
        self.max_title_chars = max_title_chars

    def __call__(
        self, data: list[SearchDocument], ctx: ToolContext
    ) -> list[SearchDocument]:
        for doc in data:
            if doc.dropped_reason:
                continue

            content = doc.clean_content or doc.raw_content
            title = doc.title or ""

            # High-confidence injection → drop
            high_hits: list[str] = []
            for pattern in _HIGH_CONFIDENCE_INJECTION:
                for m in pattern.finditer(content):
                    high_hits.append(m.group(0)[:80])
                for m in pattern.finditer(title):
                    high_hits.append(f"title:{m.group(0)[:80]}")

            if high_hits:
                doc.warnings.append(f"prompt_injection_high:{','.join(high_hits[:3])}")
                doc.dropped_reason = "prompt_injection"
                continue

            # Low-confidence injection → warn only
            low_hits: list[str] = []
            for pattern in _LOW_CONFIDENCE_INJECTION:
                for m in pattern.finditer(content):
                    low_hits.append(m.group(0)[:80])
                for m in pattern.finditer(title):
                    low_hits.append(f"title:{m.group(0)[:80]}")

            if low_hits:
                doc.warnings.append(f"prompt_injection_low:{','.join(low_hits[:3])}")

            # Character budget
            if len(title) > self.max_title_chars:
                doc.title = title[:self.max_title_chars - 3] + "..."
                doc.warnings.append("title_truncated")

            if len(content) > self.max_content_chars:
                doc.clean_content = content[:self.max_content_chars - 3] + "..."
                doc.warnings.append("content_truncated")

        return data


# ===========================================================================
# Stage 9 — Format for LLM
# ===========================================================================

class FormatDocumentStage(ProcessingStage):
    """Render each document as XML.  The **only** stage that XML-escapes."""

    name = "format"

    def __init__(self, include_dropped: bool = False):
        self.include_dropped = include_dropped

    def __call__(
        self, data: list[SearchDocument], ctx: ToolContext
    ) -> list[SearchDocument]:
        for i, doc in enumerate(data):
            if doc.dropped_reason and not self.include_dropped:
                continue

            href = _xml_escape(doc.canonical_url or doc.url or "#")
            title = _xml_escape(doc.title or "Untitled")
            content = _xml_escape(doc.clean_content or doc.raw_content or "")
            structured = doc.structured or {}
            scores = doc.scores or {}

            parts: list[str] = []
            parts.append(
                f'<Document index="{i+1}" href="{href}" title="{title}">'
            )

            if scores:
                score_parts = [
                    f'{k}="{v:.2f}"' for k, v in sorted(scores.items())
                ]
                parts.append(f"  <Scores {' '.join(score_parts)}/>")

            numbers = structured.get("numbers", [])
            if numbers:
                nums = ", ".join(_xml_escape(n) for n in numbers[:10])
                parts.append(f"  <Numbers>{nums}</Numbers>")

            dates = structured.get("dates", [])
            if dates:
                dts = ", ".join(_xml_escape(d) for d in dates[:5])
                parts.append(f"  <Dates>{dts}</Dates>")

            entities = structured.get("entities", [])
            if entities:
                ents = ", ".join(_xml_escape(e) for e in entities[:10])
                parts.append(f"  <Entities>{ents}</Entities>")

            sentiment = structured.get("sentiment", "")
            if sentiment:
                parts.append(f"  <Sentiment>{_xml_escape(sentiment)}</Sentiment>")

            evidence = structured.get("evidence", [])
            if evidence:
                for ev in evidence[:3]:
                    parts.append(f"  <Evidence>{_xml_escape(ev)}</Evidence>")

            parts.append(f"  <Content>{content}</Content>")

            if doc.warnings:
                parts.append("  <Warnings>")
                for w in doc.warnings[:10]:
                    parts.append(f"    <Warning>{_xml_escape(w)}</Warning>")
                parts.append("  </Warnings>")

            parts.append("</Document>")

            doc.metadata["formatted"] = "\n".join(parts)

        return data


# ---------------------------------------------------------------------------
# Pre-built pipeline presets
# ---------------------------------------------------------------------------

SEARCH_PIPELINE_BASIC: list[ProcessingStage] = [
    CanonicalizeURLStage(),
    CleanTextStage(),
    ExactDeduplicateStage(),
    FormatDocumentStage(),
]

SEARCH_PIPELINE_FULL: list[ProcessingStage] = [
    CanonicalizeURLStage(),
    CleanTextStage(),
    ExactDeduplicateStage(),
    NearDuplicateStage(),
    RelevanceScoreStage(),
    QualityScoreStage(),
    StructureFactsStage(),
    OutputGuardStage(),
    FormatDocumentStage(),
]
