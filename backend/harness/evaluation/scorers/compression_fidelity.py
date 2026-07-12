"""
Compression Fidelity Scorer — evaluates whether compressed turns preserve key facts.

Uses LLM-Judge for primary scoring, falls back to heuristic (token Jaccard)
when no LLM is available or when LLM output fails validation.
Compares ``CompressedTurn`` output against labeled ground-truth fixtures.

Key fixes:
- LLM judge counts are **validated** before use; invalid output triggers retry
  then falls back to heuristic
- Numbers extracted from ALL text fields (numbers_mentioned, facts.text,
  key_findings, question_intent) using finditer for multi-number sentences
- Currency mismatch ($85B vs EUR 85B) is a hard rejection
- No-label fixtures use explicit flags (expected_no_results, etc.)
- Hard thresholds with explicit status_reason in evidence
"""

from __future__ import annotations

import logging
import re
from typing import Any

from harness.evaluation.scorer import ScoreResult, Scorer
from harness.models.memory import _normalize_fact_text

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Number normalizer — unified extraction and canonicalisation
# ---------------------------------------------------------------------------

_SCALE_MAP: dict[str, float] = {
    "thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000,
    "trillion": 1_000_000_000_000, "k": 1_000, "m": 1_000_000,
    "mn": 1_000_000, "b": 1_000_000_000, "bn": 1_000_000_000,
    "t": 1_000_000_000_000, "tn": 1_000_000_000_000,
}

_CURRENCY_MAP: dict[str, str] = {
    "$": "USD", "usd": "USD", "us$": "USD",
    "€": "EUR", "eur": "EUR", "£": "GBP", "gbp": "GBP",
    "¥": "JPY", "jpy": "JPY", "rmb": "CNY", "cny": "CNY", "￥": "CNY",
}

_CURRENCY_WORDS = {"usd", "eur", "gbp", "jpy", "cny", "rmb", "dollar", "dollars",
                   "euro", "euros", "pound", "pounds", "yen", "yuan", "renminbi"}

_CURRENCY_WORD_MAP: dict[str, str] = {
    "dollar": "USD", "dollars": "USD", "euro": "EUR", "euros": "EUR",
    "pound": "GBP", "pounds": "GBP", "yen": "JPY",
    "yuan": "CNY", "renminbi": "CNY", "rmb": "CNY",
}

# Number tokeniser using finditer for multi-number extraction from sentences.
# Groups: (currency_prefix, integer, decimal, scale_word, percent_sign, unit_word)
_NUM_FINDITER_PAT = re.compile(
    r"""
    (?:[$€£¥￥]|USD|EUR|GBP|JPY|CNY|RMB|usd|eur|gbp|jpy|cny|rmb)\s*
    \d{1,3}(?:,\d{3})*(?:\.\d+)?\s*(?:thousand|million|billion|trillion|[kmbt]|mn|bn|tn)?
    |
    \d{1,3}(?:,\d{3})*(?:\.\d+)?\s*
    (?:thousand|million|billion|trillion|[kmbt]|mn|bn|tn)?
    \s*(?:%|USD|EUR|GBP|JPY|CNY|RMB|usd|eur|gbp|jpy|cny|rmb|dollars?|euros?|pounds?|yen|yuan|renminbi)?
    """,
    re.IGNORECASE | re.VERBOSE,
)

_QUARTER_PAT = re.compile(
    r"(?:Q|q)(?P<q>[1-4])\s*(?P<y1>20\d{2})|(?P<y2>20\d{2})\s*(?:Q|q)(?P<q2>[1-4])"
)

_YEAR_PAT = re.compile(r"\b(?P<year>20\d{2})\b")

_NumberKind = str


def _resolve_currency(raw_stripped: str, currency_pre: str, unit_str: str) -> str:
    """Determine ISO currency code from prefix and unit word."""
    unit = (currency_pre or unit_str).lower()
    if unit in _CURRENCY_MAP:
        return _CURRENCY_MAP[unit]
    if unit in _CURRENCY_WORDS:
        return _CURRENCY_WORD_MAP.get(unit, "")
    raw_lower = raw_stripped.lower()
    for cw in _CURRENCY_WORDS:
        if cw in raw_lower:
            return _CURRENCY_WORD_MAP.get(cw, "")
    return ""


def _normalize_number(raw: str) -> dict[str, Any] | None:
    """Parse a raw number string into a canonical normalised form.

    Returns dict with: raw, normalized_value, unit, kind
    """
    raw_stripped = raw.strip()

    # --- Quarter ---
    qm = _QUARTER_PAT.search(raw_stripped)
    if qm:
        q = int(qm.group("q") or qm.group("q2"))
        y = int(qm.group("y1") or qm.group("y2"))
        return {"raw": raw_stripped, "normalized_value": float(y * 10 + q),
                "unit": "", "kind": "quarter"}

    # --- Standalone year ---
    ym = _YEAR_PAT.fullmatch(raw_stripped)
    if ym:
        return {"raw": raw_stripped, "normalized_value": float(ym.group("year")),
                "unit": "", "kind": "year"}

    # --- Generic number pattern (single match on cleaned candidate) ---
    # Remove commas first for cleaner parsing
    cleaned = raw_stripped.replace(",", "")
    # Build a simpler regex for the cleaned string
    m = re.search(
        r"""(?:(?P<currency_pre>[$€£¥￥]|USD|EUR|GBP|JPY|CNY|RMB)\s*)?
            (?P<int_part>\d+)(?:\.(?P<dec_part>\d+))?
            \s*(?P<scale>thousand|million|billion|trillion|[kmbt]|mn|bn|tn)?
            \s*(?P<pct>%)?
            \s*(?P<unit>USD|EUR|GBP|JPY|CNY|RMB|dollars?|euros?|pounds?|yen|yuan|renminbi)?""",
        cleaned, re.IGNORECASE | re.VERBOSE,
    )
    if m is None:
        return None

    int_str = m.group("int_part") or "0"
    dec_str = m.group("dec_part")
    scale_str = (m.group("scale") or "").lower()
    is_pct = m.group("pct") is not None
    currency_pre = (m.group("currency_pre") or "").lower()
    unit_str = (m.group("unit") or "").lower()

    try:
        int_val = float(int_str)
    except ValueError:
        return None
    if dec_str:
        int_val += float("0." + dec_str)

    scale_mult = _SCALE_MAP.get(scale_str, 1.0)
    base_value = int_val * scale_mult

    if is_pct:
        return {"raw": raw_stripped, "normalized_value": base_value / 100.0,
                "unit": "%", "kind": "percentage"}

    currency_code = _resolve_currency(raw_stripped, currency_pre, unit_str)
    if currency_code:
        return {"raw": raw_stripped, "normalized_value": base_value,
                "unit": currency_code, "kind": "currency"}

    return {"raw": raw_stripped, "normalized_value": base_value,
            "unit": unit_str, "kind": "count" if base_value == int(base_value) else "plain_number"}


def _extract_all_numbers_from_text(text: str) -> list[dict[str, Any]]:
    """Extract ALL numbers from a single text using finditer.

    Handles multi-number sentences like:
      "Revenue was $85B in Q3 2025 and margin was 17%"
    extracting $85B, Q3 2025, and 17% separately.

    Overlap prevention: once a span is matched, it's excluded.
    Quarter matches consume the year portion too.
    """
    results: list[dict[str, Any]] = []
    seen_spans: set[tuple[int, int]] = set()

    # 1. Find quarters first (they consume 2 tokens: Q3 + 2025)
    for qm in _QUARTER_PAT.finditer(text):
        span = (qm.start(), qm.end())
        if span not in seen_spans:
            seen_spans.add(span)
            norm = _normalize_number(qm.group(0))
            if norm:
                results.append(norm)

    # 2. Find years (standalone, not inside a quarter match)
    for ym in _YEAR_PAT.finditer(text):
        span = (ym.start(), ym.end())
        if span not in seen_spans:
            seen_spans.add(span)
            norm = _normalize_number(ym.group(0))
            if norm:
                results.append(norm)

    # 3. Find number expressions
    for nm in _NUM_FINDITER_PAT.finditer(text):
        span = (nm.start(), nm.end())
        if span not in seen_spans:
            seen_spans.add(span)
            norm = _normalize_number(nm.group(0))
            if norm:
                results.append(norm)

    return results


def _extract_numbers_from_texts(texts: list[str]) -> list[dict[str, Any]]:
    """Extract and normalise all numbers from a list of text fragments."""
    seen_raw: set[str] = set()
    numbers: list[dict[str, Any]] = []
    for text in texts:
        for norm in _extract_all_numbers_from_text(text):
            key = norm["raw"]
            if key not in seen_raw:
                seen_raw.add(key)
                numbers.append(norm)
    return numbers


def _numbers_match(
    labeled: dict[str, Any],
    extracted: dict[str, Any],
    tolerance: float = 0.01,
) -> tuple[bool, str]:
    """Check whether two normalised numbers match.

    Currency mismatch ($85B vs EUR 85B) → hard rejection.
    """
    lk = labeled["kind"]
    ek = extracted["kind"]
    lv = labeled["normalized_value"]
    ev = extracted["normalized_value"]

    # Kind check
    if lk == ek:
        pass
    elif {lk, ek} == {"percentage", "plain_number"}:
        pass
    elif {lk, ek} == {"count", "plain_number"}:
        pass
    else:
        return False, f"Kind mismatch: {lk} vs {ek}"

    # Value check
    rel_tol = tolerance
    if lk in ("year", "quarter"):
        rel_tol = 1e-9

    if lv == 0 and ev == 0:
        values_match = True
    elif lv == 0 or ev == 0:
        values_match = False
    else:
        rel_diff = abs(lv - ev) / max(abs(lv), abs(ev))
        values_match = rel_diff <= rel_tol

    if not values_match:
        return False, f"Value mismatch: {lv} vs {ev}"

    # Unit check — CURRENCY MISMATCH IS HARD FAIL
    lu = (labeled.get("unit") or "").upper()
    eu = (extracted.get("unit") or "").upper()

    if lk == "currency" and ek == "currency":
        if lu and eu and lu != eu:
            return False, f"Currency unit mismatch: {lu} vs {eu}"

    if lu and eu and lu != eu:
        if lk in ("currency",) or ek in ("currency",):
            return False, f"Currency unit mismatch: {lu} vs {eu}"
        # Non-currency unit conflict: flag but don't block
        return True, f"Unit conflict: {lu} vs {eu}"

    return True, ""


# ---------------------------------------------------------------------------
# Default hard thresholds
# ---------------------------------------------------------------------------

DEFAULT_COMPRESSION_THRESHOLDS: dict[str, float] = {
    "fact_retention_min": 0.90,
    "hallucination_rate_max": 0.05,
    "number_retention_min": 0.90,
    "numeric_hallucination_rate_max": 0.05,
}

# ---------------------------------------------------------------------------
# Scorer
# ---------------------------------------------------------------------------


class CompressionFidelityScorer(Scorer):
    """Score compression quality with validated LLM judge and full-text number extraction."""

    dimension = "compression_fidelity"
    layer = "component"
    _MAX_JUDGE_RETRIES = 1

    def __init__(
        self, llm: Any = None, judge_llm: Any = None,
        thresholds: dict[str, float] | None = None, partial_credit: float = 0.5,
    ):
        self.llm = llm
        self.judge_llm = judge_llm if judge_llm is not None else llm
        self.thresholds = {**DEFAULT_COMPRESSION_THRESHOLDS, **(thresholds or {})}
        self.partial_credit = partial_credit

    @property
    def _using_same_model(self) -> bool:
        return self.llm is not None and self.llm is self.judge_llm

    def score(
        self, compressed_turn: Any = None,
        fixture: dict[str, Any] | None = None, **kwargs: Any,
    ) -> ScoreResult:
        if fixture is None:
            return ScoreResult(dimension=self.dimension, layer=self.layer,
                              value=0, max_value=2, normalized=0, status="fail",
                              details="No fixture provided — cannot score",
                              issues=["Missing fixture"])

        labeled_facts: list[str] = fixture.get("labeled_facts", []) or []
        labeled_numbers: list[dict[str, Any]] = fixture.get("labeled_numbers", []) or []

        # --- No-label fixtures: check for explicit flags ---
        has_explicit_no_result = (
            fixture.get("expected_no_results") is True
            or bool(fixture.get("expected_unanswered"))
            or fixture.get("allowed_fact_count") == 0
        )

        if not labeled_facts and not labeled_numbers:
            if has_explicit_no_result:
                return self._score_no_result_case(compressed_turn, fixture)
            return ScoreResult.skipped(
                dimension=self.dimension, layer=self.layer,
                reason="Fixture has no labeled facts/numbers and no expected_no_results flag",
                evidence={"insufficient_labels": True},
            )

        # Extract facts
        extracted_facts = self._extract_fact_texts(compressed_turn)
        # Extract ALL numbers from all text sources (NOT just numbers_mentioned)
        extracted_all_numbers = self._extract_all_numbers(compressed_turn)

        if self.judge_llm is not None:
            return self._llm_score_with_validation(
                labeled_facts, extracted_facts, labeled_numbers, extracted_all_numbers)
        else:
            return self._heuristic_score(
                labeled_facts, extracted_facts, labeled_numbers, extracted_all_numbers)

    # ------------------------------------------------------------------
    # Unified number extraction from ALL text fields
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_all_numbers(turn: Any) -> list[dict[str, Any]]:
        """Extract numbers from ALL relevant fields using finditer.

        Sources (in order):
        1. numbers_mentioned (explicit field)
        2. facts[].text
        3. key_findings
        4. question_intent

        Returns list of dicts with 'value', 'unit', 'context', and
        '_source_field' tracking which field each number came from.
        """
        all_raw: list[dict[str, Any]] = []

        # 1. numbers_mentioned
        nums = getattr(turn, "numbers_mentioned", None)
        if nums is None and isinstance(turn, dict):
            nums = turn.get("numbers_mentioned") or []
        for n in (nums or []):
            d = dict(n) if isinstance(n, dict) else {"value": str(n)}
            d["_source_field"] = "numbers_mentioned"
            all_raw.append(d)

        # 2. facts[].text
        fact_texts = CompressionFidelityScorer._extract_fact_texts(turn)
        for t in fact_texts:
            norms = _extract_all_numbers_from_text(t)
            for norm in norms:
                # Skip bare single-digit numbers (enumeration artifacts like "fact 1")
                if norm.get("kind") in ("count", "plain_number") and not norm.get("unit"):
                    if norm.get("normalized_value", 0) < 10:
                        continue
                raw_text = norm.get("raw", "")
                unit = norm.get("unit", "")
                all_raw.append({"value": raw_text,
                                "unit": unit,
                                "context": raw_text,
                                "_source_field": "facts.text"})

        # 3. key_findings
        kf = getattr(turn, "key_findings", None)
        if kf is None and isinstance(turn, dict):
            kf = turn.get("key_findings") or []
        for item in (kf or []):
            text = str(item) if not isinstance(item, dict) else item.get("text", str(item))
            norms = _extract_all_numbers_from_text(text)
            for norm in norms:
                raw_text = norm.get("raw", "")
                all_raw.append({"value": raw_text,
                                "unit": norm.get("unit", ""),
                                "context": raw_text,
                                "_source_field": "key_findings"})

        # 4. question_intent
        qi = getattr(turn, "question_intent", None)
        if qi is None and isinstance(turn, dict):
            qi = turn.get("question_intent", "")
        if qi:
            norms = _extract_all_numbers_from_text(str(qi))
            for norm in norms:
                raw_text = norm.get("raw", "")
                all_raw.append({"value": raw_text,
                                "unit": norm.get("unit", ""),
                                "context": raw_text,
                                "_source_field": "question_intent"})

        return all_raw

    # ------------------------------------------------------------------
    # No-result case evaluation
    # ------------------------------------------------------------------

    def _score_no_result_case(self, compressed_turn: Any, fixture: dict[str, Any]) -> ScoreResult:
        facts = self._extract_fact_texts(compressed_turn)
        nums = self._extract_all_numbers(compressed_turn)
        unanswered = self._extract_unanswered(compressed_turn)
        comp_error = self._extract_compression_error(compressed_turn)

        expected_unanswered_raw = fixture.get("expected_unanswered")
        allowed_fact_count = fixture.get("allowed_fact_count")
        expected_error = fixture.get("expected_error")

        issues: list[str] = []
        checks: dict[str, bool] = {}

        # Fact count check (respect allowed_fact_count)
        if allowed_fact_count is not None:
            checks["fact_count_within_limit"] = len(facts) <= int(allowed_fact_count)
            if not checks["fact_count_within_limit"]:
                issues.append(f"Fact count {len(facts)} exceeds allowed {allowed_fact_count}")
        else:
            checks["no_facts_generated"] = len(facts) == 0
            if not checks["no_facts_generated"]:
                issues.append(f"Expected no facts but generated {len(facts)}")

        # Number check
        checks["no_numbers_generated"] = len(nums) == 0
        if not checks["no_numbers_generated"]:
            issues.append(f"Expected no numbers but generated {len(nums)}")

        # Unanswered check (handle bool and list types)
        if expected_unanswered_raw is not None:
            if isinstance(expected_unanswered_raw, bool):
                if expected_unanswered_raw:
                    checks["has_unanswered"] = len(unanswered) > 0
                    if not checks["has_unanswered"]:
                        issues.append("Expected at least one unanswered item but got none")
            elif isinstance(expected_unanswered_raw, list):
                answered = set(str(u).strip().lower() for u in unanswered)
                expected = set(str(e).strip().lower() for e in expected_unanswered_raw)
                checks["unanswered_preserved"] = expected.issubset(answered)
                if not checks["unanswered_preserved"]:
                    issues.append("Expected unanswered items not preserved")

        # Error check (respect expected_error flag)
        if expected_error is True:
            checks["has_error"] = bool(comp_error)
            if not checks["has_error"]:
                issues.append("Expected compression_error but got none")
        elif expected_error is False:
            checks["no_error"] = not bool(comp_error)
            if not checks["no_error"]:
                issues.append(f"Unexpected compression error: {comp_error}")
        else:
            checks["no_error"] = not bool(comp_error)
            if comp_error:
                issues.append(f"Compression error: {comp_error}")

        all_ok = all(checks.values())
        return ScoreResult(
            dimension=self.dimension, layer=self.layer,
            value=2 if all_ok else 0, max_value=2,
            normalized=1.0 if all_ok else 0.0,
            status="pass" if all_ok else "fail",
            details=f"No-result case: {'OK' if all_ok else 'issues found'}",
            issues=issues,
            evidence={"case_type": "expected_no_results", "checks": checks,
                     "generated_fact_count": len(facts),
                     "generated_number_count": len(nums),
                     "unanswered_count": len(unanswered),
                     "compression_error": comp_error},
        )

    # ------------------------------------------------------------------
    # Fact / number / unanswered extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_fact_texts(turn: Any) -> list[str]:
        facts = getattr(turn, "facts", None)
        if facts is None and isinstance(turn, dict):
            facts = turn.get("facts") or []
        texts: list[str] = []
        for f in (facts or []):
            t = f.get("text", "") if isinstance(f, dict) else getattr(f, "text", "")
            if t:
                texts.append(str(t))
        if not texts:
            kf = getattr(turn, "key_findings", None)
            if kf is None and isinstance(turn, dict):
                kf = turn.get("key_findings") or []
            texts = [str(x) for x in (kf or [])]
        return texts

    @staticmethod
    def _extract_unanswered(turn: Any) -> list[str]:
        val = getattr(turn, "unanswered", None)
        if val is None and isinstance(turn, dict):
            val = turn.get("unanswered") or []
        return list(val or [])

    @staticmethod
    def _extract_compression_error(turn: Any) -> str:
        val = getattr(turn, "compression_error", None)
        if val is None and isinstance(turn, dict):
            val = turn.get("compression_error", "")
        return str(val or "")

    # ------------------------------------------------------------------
    # LLM Judge path
    # ------------------------------------------------------------------

    _LLM_FIDELITY_PROMPT = """\
You are an evaluation judge. Compare the EXTRACTED FACTS from a compression system
against LABELED GROUND-TRUTH FACTS.

For each labeled fact, decide if it appears in the extracted facts:
  - "match": the extracted fact conveys the same information
  - "partial": partially covered but details missing
  - "missed": not found in any extracted fact

For each extracted fact, decide if it's a hallucination:
  - "hallucination": not supported by any labeled fact

Return ONLY a JSON object (no markdown, no explanation):
{{
  "matched": <int>,
  "partial": <int>,
  "missed": <int>,
  "hallucinations": <int>
}}

LABELED FACTS ({n_labeled} total):
{labeled_facts}

EXTRACTED FACTS ({n_extracted} total):
{extracted_facts}
"""

    def _llm_score_with_validation(
        self, labeled: list[str], extracted: list[str],
        labeled_nums: list[dict[str, Any]], extracted_all_nums: list[dict[str, Any]],
    ) -> ScoreResult:
        evidence: dict[str, Any] = {
            "judge_method": "llm", "judge_attempts": 0,
            "judge_validation_errors": [], "fallback_used": False,
            "labeled_fact_count": len(labeled), "extracted_fact_count": len(extracted),
        }
        if self._using_same_model:
            evidence["same_model_self_judge"] = True
            logger.warning("Compressor and Judge using same LLM — self-judging bias may inflate scores.")

        data: dict[str, Any] = {}
        for attempt in range(self._MAX_JUDGE_RETRIES + 1):
            evidence["judge_attempts"] = attempt + 1
            try:
                data = self._call_llm_judge(labeled, extracted)
                valid, errors = self._validate_judge_output(data, len(labeled), len(extracted))
                if valid:
                    evidence["judge_validation_errors"] = []
                    break
                evidence["judge_validation_errors"] = errors
                logger.warning(f"LLM judge output invalid (attempt {attempt + 1}): {errors}")
            except Exception as exc:
                evidence["judge_validation_errors"] = [f"Judge call failed: {exc}"]
                if attempt == self._MAX_JUDGE_RETRIES:
                    break

        if evidence["judge_validation_errors"]:
            evidence["fallback_used"] = True
            return self._heuristic_score(labeled, extracted, labeled_nums, extracted_all_nums, evidence)

        matched = int(data.get("matched", 0))
        partial = int(data.get("partial", 0))
        missed = int(data.get("missed", 0))
        hallucinations = int(data.get("hallucinations", 0))

        fact_retention = (matched + self.partial_credit * partial) / len(labeled) if labeled else 1.0
        hallucination_rate = hallucinations / len(extracted) if extracted else 0.0
        num_result = self._match_numbers_full(labeled_nums, extracted_all_nums)

        return self._build_score_result(
            fact_retention, hallucination_rate, matched, partial, missed, hallucinations,
            num_result, evidence, "LLM-Judge (validated)")

    def _call_llm_judge(self, labeled: list[str], extracted: list[str]) -> dict[str, Any]:
        prompt = self._LLM_FIDELITY_PROMPT.format(
            n_labeled=len(labeled), labeled_facts="\n".join(f"- {f}" for f in labeled),
            n_extracted=len(extracted), extracted_facts="\n".join(f"- {f}" for f in extracted),
        )
        from harness.models.memory import _extract_json
        from langchain_core.messages import HumanMessage
        response = self.judge_llm.invoke([HumanMessage(content=prompt)])
        return _extract_json(str(response.content)) or {}

    @staticmethod
    def _validate_judge_output(data: dict[str, Any], labeled_count: int, extracted_count: int) -> tuple[bool, list[str]]:
        errors: list[str] = []
        for key in ("matched", "partial", "missed", "hallucinations"):
            if key not in data:
                errors.append(f"Missing key '{key}' in judge output")
                return False, errors
        try:
            m, p, ms, h = int(data["matched"]), int(data["partial"]), int(data["missed"]), int(data["hallucinations"])
        except (ValueError, TypeError) as e:
            errors.append(f"Non-integer count value: {e}")
            return False, errors
        if m < 0: errors.append(f"matched = {m} < 0")
        if p < 0: errors.append(f"partial = {p} < 0")
        if ms < 0: errors.append(f"missed = {ms} < 0")
        if h < 0: errors.append(f"hallucinations = {h} < 0")
        if m + p + ms != labeled_count:
            errors.append(f"matched({m})+partial({p})+missed({ms})={m+p+ms} ≠ labeled({labeled_count})")
        if h > extracted_count:
            errors.append(f"hallucinations({h}) > extracted({extracted_count})")
        if "fact_retention" in data:
            llm_r = float(data["fact_retention"])
            calc_r = (m + 0.5 * p) / labeled_count if labeled_count else 1.0
            if abs(llm_r - calc_r) > 0.15:
                errors.append(f"LLM fact_retention={llm_r:.3f} vs calculated={calc_r:.3f}")
        if "hallucination_rate" in data:
            llm_h = float(data["hallucination_rate"])
            calc_h = h / extracted_count if extracted_count else 0.0
            if abs(llm_h - calc_h) > 0.15:
                errors.append(f"LLM hallucination_rate={llm_h:.3f} vs calculated={calc_h:.3f}")
        return len(errors) == 0, errors

    # ------------------------------------------------------------------
    # Heuristic fallback
    # ------------------------------------------------------------------

    def _heuristic_score(
        self, labeled: list[str], extracted: list[str],
        labeled_nums: list[dict[str, Any]], extracted_all_nums: list[dict[str, Any]],
        extra_evidence: dict[str, Any] | None = None,
    ) -> ScoreResult:
        evidence: dict[str, Any] = {
            "judge_method": "heuristic", "labeled_fact_count": len(labeled),
            "extracted_fact_count": len(extracted), **(extra_evidence or {}),
        }
        norm_labeled = [_normalize_fact_text(f) for f in labeled]
        norm_extracted = [_normalize_fact_text(f) for f in extracted]

        found = 0
        for nl in norm_labeled:
            tokens_l = set(nl.split())
            for ne in norm_extracted:
                tokens_e = set(ne.split())
                if not tokens_l or not tokens_e: continue
                if len(tokens_l & tokens_e) / len(tokens_l | tokens_e) >= 0.4:
                    found += 1; break
        retention = found / len(labeled) if labeled else 1.0

        halluc_count = 0
        for ne in norm_extracted:
            tokens_e = set(ne.split()); matched = False
            for nl in norm_labeled:
                tokens_l = set(nl.split())
                if not tokens_l or not tokens_e: continue
                if len(tokens_l & tokens_e) / len(tokens_l | tokens_e) >= 0.4:
                    matched = True; break
            if not matched: halluc_count += 1
        hallu_rate = halluc_count / len(extracted) if extracted else 0.0

        num_result = self._match_numbers_full(labeled_nums, extracted_all_nums)
        return self._build_score_result(
            retention, hallu_rate, found, len(labeled) - found - 0, len(labeled) - found,
            halluc_count, num_result, evidence, "Heuristic")

    # ------------------------------------------------------------------
    # Shared score builder
    # ------------------------------------------------------------------

    def _build_score_result(
        self, fact_retention: float, hallucination_rate: float,
        matched: int, partial: int, missed: int, hallucinations: int,
        num_result: dict[str, Any], evidence: dict[str, Any], method_label: str,
    ) -> ScoreResult:
        number_retention = num_result["number_retention"]
        numeric_hallu_rate = num_result["numeric_hallucination_rate"]

        composite = round(fact_retention * 0.40 + (1.0 - hallucination_rate) * 0.25
                         + number_retention * 0.25 + (1.0 - numeric_hallu_rate) * 0.10, 4)

        threshold_checks = {
            "fact_retention": {"value": round(fact_retention, 4),
                "threshold": self.thresholds["fact_retention_min"],
                "passed": fact_retention >= self.thresholds["fact_retention_min"]},
            "hallucination_rate": {"value": round(hallucination_rate, 4),
                "threshold": self.thresholds["hallucination_rate_max"],
                "passed": hallucination_rate <= self.thresholds["hallucination_rate_max"]},
            "number_retention": {"value": round(number_retention, 4),
                "threshold": self.thresholds["number_retention_min"],
                "passed": number_retention >= self.thresholds["number_retention_min"]},
            "numeric_hallucination_rate": {"value": round(numeric_hallu_rate, 4),
                "threshold": self.thresholds["numeric_hallucination_rate_max"],
                "passed": numeric_hallu_rate <= self.thresholds["numeric_hallucination_rate_max"]},
        }

        core_passed = all(threshold_checks[k]["passed"] for k in threshold_checks)
        partial_checks = [threshold_checks["fact_retention"]["value"] >= 0.75,
                         threshold_checks["hallucination_rate"]["value"] <= 0.15,
                         threshold_checks["number_retention"]["value"] >= 0.60]

        if core_passed:
            status, status_reason = "pass", "All core hard thresholds met"
        elif all(partial_checks):
            status, status_reason = "partial", "Partial thresholds met but core thresholds not all satisfied"
        else:
            status, status_reason = "fail", "One or more hard thresholds breached"

        issues: list[str] = []
        for cn, ch in threshold_checks.items():
            if not ch["passed"]:
                direction = "≥" if "retention" in cn else "≤"
                issues.append(f"{cn} = {ch['value']:.2%} (threshold: {direction} {ch['threshold']:.2%})")

        return ScoreResult(
            dimension=self.dimension, layer=self.layer,
            value=round(composite * 2, 1), max_value=2, normalized=composite,
            status=status,
            details=(f"{method_label}: retention={fact_retention:.0%}, hallu={hallucination_rate:.0%}, "
                    f"num_ret={number_retention:.0%}, num_hallu={numeric_hallu_rate:.0%}. "
                    f"matched={matched}, partial={partial}, missed={missed}, hallucinations={hallucinations}. "
                    f"Status: {status} — {status_reason}"),
            issues=issues,
            evidence={**evidence,
                "fact_retention": round(fact_retention, 4),
                "hallucination_rate": round(hallucination_rate, 4),
                "number_retention": round(number_retention, 4),
                "numeric_hallucination_rate": round(numeric_hallu_rate, 4),
                "matched": matched, "partial": partial, "missed": missed,
                "hallucinations": hallucinations,
                "composite_score": composite,
                "thresholds": self.thresholds, "threshold_checks": threshold_checks,
                "status_reason": status_reason,
                **num_result},
        )

    # ------------------------------------------------------------------
    # Full number matching
    # ------------------------------------------------------------------

    @staticmethod
    def _match_numbers_full(
        labeled: list[dict[str, Any]], extracted_all: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not labeled:
            return {"matched_numbers": [], "missed_numbers": [], "extra_numbers": [],
                    "number_retention": 1.0, "numeric_hallucination_rate": 0.0}

        norm_labeled: list[dict[str, Any]] = []
        for ln in labeled:
            parse_str = f"{ln.get('value','')} {ln.get('unit','')} {ln.get('context','')}"
            norm = _normalize_number(parse_str)
            if norm is not None:
                norm["_labeled_idx"] = len(norm_labeled); norm_labeled.append(norm)

        norm_extracted: list[dict[str, Any]] = []
        for en in extracted_all:
            parse_str = f"{en.get('value','')} {en.get('unit','')} {en.get('context','')}"
            norm = _normalize_number(parse_str)
            if norm is not None:
                norm["_extracted_idx"] = len(norm_extracted)
                norm["_source_field"] = en.get("_source_field", "unknown")
                norm_extracted.append(norm)

        matched_labeled: set[int] = set()
        matched_extracted: set[int] = set()
        matched_pairs: list[dict[str, Any]] = []

        # First pass: strict matches (no unit conflict, no currency mismatch)
        for ei, en in enumerate(norm_extracted):
            if ei in matched_extracted: continue
            for li, ln in enumerate(norm_labeled):
                if li in matched_labeled: continue
                is_match, reason = _numbers_match(ln, en)
                if is_match and "mismatch" not in reason.lower() and "conflict" not in reason.lower():
                    matched_labeled.add(li); matched_extracted.add(ei)
                    matched_pairs.append({"labeled": ln, "extracted": en,
                                         "match_quality": "exact" if reason == "" else reason})
                    break

        # Second pass: allow unit-conflict for non-currency
        for ei, en in enumerate(norm_extracted):
            if ei in matched_extracted: continue
            for li, ln in enumerate(norm_labeled):
                if li in matched_labeled: continue
                is_match, reason = _numbers_match(ln, en)
                if is_match and "mismatch" not in reason.lower():
                    matched_labeled.add(li); matched_extracted.add(ei)
                    matched_pairs.append({"labeled": ln, "extracted": en,
                                         "match_quality": "unit_conflict"})
                    break

        missed_count = len(norm_labeled) - len(matched_labeled)
        extra_count = len(norm_extracted) - len(matched_extracted)
        number_retention = len(matched_labeled) / len(norm_labeled) if norm_labeled else 1.0
        numeric_hallu_rate = extra_count / len(norm_extracted) if norm_extracted else 0.0

        return {
            "matched_numbers": matched_pairs,
            "missed_numbers": [norm_labeled[i] for i in range(len(norm_labeled)) if i not in matched_labeled],
            "extra_numbers": [norm_extracted[i] for i in range(len(norm_extracted)) if i not in matched_extracted],
            "number_retention": round(number_retention, 4),
            "numeric_hallucination_rate": round(numeric_hallu_rate, 4),
        }
