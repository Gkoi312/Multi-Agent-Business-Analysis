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

# Characters that break XML:  &  <  >  "  '
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

# High confidence: independently sufficient to drop a document
_HIGH_CONFIDENCE_INJECTION: list[re.Pattern] = [
    re.compile(r"ignore\s+(all\s+)?(previous|above)\s+(instructions?|prompts?)", re.IGNORECASE),
    re.compile(r"system\s*(prompt|message)\s*(is|was|:)", re.IGNORECASE),
    re.compile(r"you\s+are\s+(now|no\s+longer)\s+(an?\s+)?(AI|assistant|language\s+model)", re.IGNORECASE),
    re.compile(r"<\|im_start\|>|<\|im_end\|>", re.IGNORECASE),
    re.compile(r"\[system\]|\[/system\]|\[assistant\]|\[/assistant\]", re.IGNORECASE),
    re.compile(r"<\|.*?\|>", re.IGNORECASE),
    re.compile(r"DAN\s+mode|developer\s+mode|jailbreak", re.IGNORECASE),
]

# Low confidence: only warn, never drop alone
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

# Domain patterns that consistently produce low-value / SEO-spam content
_SPAM_DOMAIN_PATTERNS: list[str] = []


def _xml_escape(text: str) -> str:
    """Escape special XML characters in *text*.

    Only called by FormatDocumentStage — never by OutputGuardStage.
    This ensures exactly one escaping pass per pipeline run.
    """
    return text.translate(_XML_ESCAPE_TABLE)


# ----- HTML tag stripper (HTMLParser-based, not regex) -----------------------


class _TagStripper(HTMLParser):
    """HTMLParser that strips tags while preserving text content.

    Unlike ``<[^>]*>`` regex, this correctly preserves comparison operators
    like ``Revenue < 5 & profit > 2`` — ``<`` followed by a space or digit
    is not a tag start.
    """

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def handle_entityref(self, name: str) -> None:
        # Preserve named entities as-is; html.unescape resolves them later
        self._parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
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
    """
    stripper = _TagStripper()
    try:
        stripper.feed(text)
    except Exception:
        # If HTMLParser chokes on truly malformed input, fall back to regex
        text = re.sub(r"<[A-Za-z][^>]*>", " ", text)
    else:
        text = stripper.get_text()
    text = html_mod.unescape(text)
    return text


# ----- Bigram Jaccard --------------------------------------------------------


def _bigram_jaccard(a: str, b: str) -> float:
    """Bigram Jaccard — works for Chinese (character bigrams) and English (word bigrams).

    For CJK text, bigrams are formed on characters (no whitespace splitting).
    For non-CJK text, bigrams are formed on whitespace-delimited words.
    """
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
    """Compute a lightweight MinHash-ish fingerprint for near-duplicate detection.

    For CJK-heavy text uses character 3-grams; for others uses word 3-grams.
    """
    if not text:
        return ""
    # Check if primarily CJK
    cjk_count = sum(1 for ch in text if '一' <= ch <= '鿿' or '㐀' <= ch <= '䶿')
    if cjk_count > len(text) * 0.3:
        # Character trigrams
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
    cjk = re.findall(r'[一-鿿㐀-䶿]{2,4}', text)
    return cjk


# ===========================================================================
# Stage 1 — Canonicalize URL
# ===========================================================================

class CanonicalizeURLStage(ProcessingStage):
    """Remove tracking query parameters (utm_*, gclid, fbclid, …) and URL fragments.

    Uses ``parse_qsl`` + ``urlencode`` to preserve repeated params,
    empty params, and percent-encoding.  Sets ``doc.canonical_url``.
    Does **not** drop any documents.
    """

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
                # parse_qsl preserves duplicates and encoding (vs parse_qs which loses both)
                qsl = parse_qsl(parsed.query, keep_blank_values=True)
                cleaned_qsl = [
                    (k, v) for k, v in qsl
                    if k.lower() not in self.tracking_params
                ]
                cleaned_query = urlencode(cleaned_qsl, doseq=True, quote_via=quote)
                # Reconstruct without fragment
                doc.canonical_url = urlunparse((
                    parsed.scheme,
                    parsed.netloc.lower(),
                    parsed.path.rstrip("/") or "/",
                    parsed.params,
                    cleaned_query,
                    "",  # drop fragment
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

    **Never** modifies ``raw_content``.
    HTMLParser correctly preserves ``Revenue < 5 & profit > 2``
    (unlike ``<[^>]*>`` regex which eats comparison operators).

    Drops documents whose cleaned content is too short (default 50 chars).
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
    """Drop documents with duplicate ``canonical_url`` — best-wins, not first-wins.

    Strategy for same canonical_url:
    1. Skip documents already marked as dropped.
    2. Among non-dropped duplicates, prefer the one with:
       - longer ``clean_content``
       - higher ``provider_score`` (as tiebreaker)

    Already-dropped docs never claim a canonical_url slot.
    """

    name = "exact_dedup"

    def __call__(
        self, data: list[SearchDocument], ctx: ToolContext
    ) -> list[SearchDocument]:
        # Group by canonical_url, tracking indices
        url_map: dict[str, list[int]] = {}
        for i, doc in enumerate(data):
            if doc.dropped_reason:
                # Dropped docs never participate as keepers and never claim a slot
                continue
            key = doc.canonical_url or doc.url
            if not key:
                continue
            url_map.setdefault(key, []).append(i)

        # For each group with >1 entry, pick the best; drop the rest
        for key, indices in url_map.items():
            if len(indices) <= 1:
                continue
            # Sort by quality: dropped last, then by content length, then by provider_score
            def _sort_key(idx: int) -> tuple[int, int, float]:
                d = data[idx]
                has_dropped = 1 if d.dropped_reason else 0
                content_len = len(d.clean_content or d.raw_content)
                score = d.provider_score or 0.0
                return (-has_dropped, content_len, score)

            indices.sort(key=_sort_key, reverse=True)
            # First is best — keep; rest are duplicates
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
    """Detect near-duplicate documents using content fingerprint + title bigram Jaccard.

    When document *i* is marked as dropped (because *j* has longer content),
    stop comparing *i* with further documents — it's already out.

    Fingerprints use character trigrams for CJK text, word trigrams otherwise.
    """

    name = "near_dedup"

    def __init__(self, title_similarity_threshold: float = 0.80, fp_threshold: float = 0.85):
        self.title_threshold = title_similarity_threshold
        self.fp_threshold = fp_threshold

    def __call__(
        self, data: list[SearchDocument], ctx: ToolContext
    ) -> list[SearchDocument]:
        # Compute fingerprints for all non-dropped docs
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
                    # i is dropped — stop comparing i with further docs
                    break

        return data


# ===========================================================================
# Stage 5 — Relevance score
# ===========================================================================

class RelevanceScoreStage(ProcessingStage):
    """Score each document's relevance to the target entity and focus area.

    Scoring dimensions:
    - **Title match** — target entity hit in title gets independent weight (0.3 bonus).
    - **Content density** — sliding-window keyword density for target_entity.
    - **Focus match** — target_focus keywords (supports Chinese via character extraction).
    - **Provider score** — high provider_score docs qualify for LLM even with zero keyword hits.

    Batch LLM: splits borderline docs into batches of ``llm_batch_size``,
    each batch in one API call.  LLM and keyword scores are **weighted-fused**
    (0.4 × keyword + 0.6 × LLM), not max().
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

        # ---- Keyword-density scoring (title + content separate) ----
        active_docs = [(i, doc) for i, doc in enumerate(data) if not doc.dropped_reason]
        for _, doc in active_docs:
            doc.scores["relevance"] = self._compute_keyword_score(doc, target, focus)

        # ---- Batch LLM pass for borderline docs (including high provider_score) ----
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
        """Score relevance using separate title and content analysis."""
        title_lower = (doc.title or "").lower()
        content_lower = (doc.clean_content or doc.raw_content or "").lower()
        words = content_lower.split()

        title_score = 0.0
        content_score = 0.0
        focus_score = 0.0

        # --- Title: target entity hit gets independent weight ---
        if target:
            if target in title_lower:
                # Count occurrences in title for differentiation
                title_hits = title_lower.count(target)
                title_score = min(1.0, 0.30 + 0.10 * min(title_hits - 1, 3))

            # --- Content: sliding-window density ---
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

        # --- Focus: support Chinese + English keywords ---
        if focus:
            # Extract potential Chinese keywords from focus
            cjk_keywords = _extract_keywords(focus)
            if cjk_keywords:
                focus_hits = sum(1 for kw in cjk_keywords if kw in content_lower)
                focus_score = focus_hits / max(1, len(cjk_keywords)) * 0.5
            else:
                focus_words = focus.split()
                focus_hits = sum(1 for w in focus_words if w in content_lower)
                focus_score = focus_hits / max(1, len(focus_words)) * 0.5

        # Composite
        if target and focus:
            composite = title_score * 0.30 + content_score * 0.40 + focus_score * 0.30
        elif target:
            composite = title_score * 0.35 + content_score * 0.65
        else:
            composite = focus_score

        return round(min(1.0, composite), 4)

    def _eligible_for_llm(self, doc: SearchDocument) -> bool:
        """Determine if a document should be sent to LLM for relevance scoring.

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
        """Score borderline documents using LLM in batches."""
        if not ctx.cheap_llm:
            return

        # Split into batches
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
        items = []
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
                    llm_score = max(0, min(100, raw_score)) / 100.0
                    if 0 <= idx < len(batch):
                        kw_score = batch[idx][1].scores.get("relevance", 0.5)
                        # Weighted fusion: 0.4 × keyword + 0.6 × LLM
                        fused = round(0.4 * kw_score + 0.6 * llm_score, 4)
                        batch[idx][1].scores["relevance"] = fused
        except Exception as e:
            logger.warning("Batch LLM relevance scoring failed: %s", e)


# ===========================================================================
# Stage 6 — Quality score
# ===========================================================================

class QualityScoreStage(ProcessingStage):
    """Score content quality on multiple dimensions — never a single hard gate.

    Dimensions (each 0.0 – 1.0):
    1. **Fact density** — presence of dates, numbers, named entities.
    2. **SEO-filler ratio** — filler phrase **occurrence count** vs total words.
    3. **Domain credibility** — known spam domains get score 0.0.
    4. **Content length** — very short content gets penalised.

    Filler count uses each phrase's total occurrences, not just presence.
    """

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

            # 1) Domain credibility
            dimension_scores["domain"] = 0.0 if self._is_spam_domain(url_lower) else 1.0

            # 2) Fact density
            numbers = len(_NUMBER_RE.findall(content))
            dates = len(_DATE_RE.findall(content))
            entities = len(_ENTITY_RE.findall(content))
            fact_score = min(1.0, numbers * 0.15 + dates * 0.25 + entities * 0.2)
            dimension_scores["fact_density"] = fact_score

            # 3) SEO-filler ratio — count total occurrences, not unique phrases
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

            # 4) Content length
            if word_count < 20:
                length_score = 0.1
            elif word_count < 50:
                length_score = 0.4
            elif word_count < 100:
                length_score = 0.7
            else:
                length_score = 1.0
            dimension_scores["length"] = length_score

            # Composite
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

_NUMBER_RE = re.compile(
    r"\$?\b\d{1,3}(?:,\d{3})*(?:\.\d+)?\s?(?:million|billion|trillion|%|percent|k|M|B|T|万亿|亿|万)?\b",
    re.IGNORECASE,
)
_DATE_RE = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}\s(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s\d{4}|(?:20\d{2}|Q[1-4]\s?20\d{2}))\b",
    re.IGNORECASE,
)
_ENTITY_RE = re.compile(
    r"\b[A-Z一-鿿][A-Za-z一-鿿]+(?:\s[A-Z一-鿿][A-Za-z一-鿿]+){1,3}\b"
)
_SENTENCE_RE = re.compile(r"[^.!?。！？\n]+[.!?。！？]?")


class StructureFactsStage(ProcessingStage):
    """Extract structured metadata from document content.

    Does **not** call an LLM; purely regex + heuristics.
    Populates ``doc.structured`` with:
    - ``numbers`` — financial figures, percentages, counts (top 10)
    - ``dates`` — ISO dates, month-year, quarter references (top 5)
    - ``entities`` — capitalized multi-word noun phrases (top 10)
    - ``sentiment`` — "positive" | "negative" | "neutral"
    - ``char_count`` — character count of clean_content
    - ``evidence`` — up to 3 sentences containing extracted numbers
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

            sentences = _SENTENCE_RE.findall(content)
            evidence: list[str] = []
            if numbers:
                for sent in sentences:
                    if any(n in sent for n in numbers[:5]):
                        evidence.append(sent.strip())
                        if len(evidence) >= 3:
                            break

            doc.structured = {
                "numbers": list(dict.fromkeys(numbers))[:10],
                "dates": list(dict.fromkeys(dates))[:5],
                "entities": list(dict.fromkeys(entities))[:10],
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
# Stage 8 — Output guard (NO XML escaping — that belongs to FormatDocumentStage)
# ===========================================================================

class OutputGuardStage(ProcessingStage):
    """Guard against unsafe content before passing documents to the LLM.

    Performs:
    1. **Prompt injection detection** — high-confidence patterns → drop;
       low-confidence patterns → warning only (never drop alone).
    2. **Character budget** — truncate over-long content with warning.

    Does **NOT** XML-escape — that happens exactly once in FormatDocumentStage.
    This prevents double-escaping bugs like ``AT&amp;amp;T``.
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

            # 1) Prompt injection detection — high confidence
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

            # 2) Prompt injection detection — low confidence (warn only)
            low_hits: list[str] = []
            for pattern in _LOW_CONFIDENCE_INJECTION:
                for m in pattern.finditer(content):
                    low_hits.append(m.group(0)[:80])
                for m in pattern.finditer(title):
                    low_hits.append(f"title:{m.group(0)[:80]}")

            if low_hits:
                doc.warnings.append(f"prompt_injection_low:{','.join(low_hits[:3])}")
                # Never drop based on low-confidence alone

            # 3) Character budget (no XML escaping — use raw lengths)
            if len(title) > self.max_title_chars:
                doc.title = title[:self.max_title_chars - 3] + "..."
                doc.warnings.append("title_truncated")

            if len(content) > self.max_content_chars:
                doc.clean_content = content[:self.max_content_chars - 3] + "..."
                doc.warnings.append("content_truncated")

        return data


# ===========================================================================
# Stage 9 — Format for LLM (all XML escaping happens HERE, exactly once)
# ===========================================================================

class FormatDocumentStage(ProcessingStage):
    """Render each document as an XML ``<Document>`` block for LLM consumption.

    This is the **only** stage that performs XML escaping — all user-supplied
    fields are escaped exactly once.  Warnings are rendered as structured
    ``<Warnings>`` elements, not XML comments (which break on ``--``).
    """

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

            # Scores
            if scores:
                score_parts = []
                for k, v in sorted(scores.items()):
                    score_parts.append(f'{k}="{v:.2f}"')
                parts.append(f"  <Scores {' '.join(score_parts)}/>")

            # Structured facts
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

            # Main content
            parts.append(f"  <Content>{content}</Content>")

            # Warnings as structured elements (not XML comments — avoids -- issues)
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
