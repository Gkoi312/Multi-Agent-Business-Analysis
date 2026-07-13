"""
Source Traceability Scorer — pure regex checker for [Sn] citations in reports.

Key fixes:
- Orphan detection: checks citation_id actually in registry keys
- Body bare URLs: ANY body bare URL → fail (not partial)
- Malformed citation detection: [Sx], [SA], [S], [S-1], [S1x], [s1], missing bracket
- No registry with citations → fail (can't cross-validate)
- Hard thresholds with explicit status_reason
- Multi-digit citation fix: [S12], [S123] are valid, not malformed
  Uses two-phase approach (extract candidates then fullmatch validate) instead of
  the buggy negative-lookahead pattern which backtracks on multi-digit citations.
- Malformed dedup: same malformed citation reported only once
"""

from __future__ import annotations

import re
from typing import Any

from harness.evaluation.scorer import ScoreResult, Scorer

# Valid citations: [S1], [S12], [S123]
_CITATION_RE = re.compile(r"\[S(\d+)\]")

# Candidate extraction: find ALL [S...] tokens (valid + malformed)
# Matches [S followed by non-whitespace, non-bracket chars up to ] or end-boundary
_CITATION_CANDIDATE_RE = re.compile(
    r"\[S[^\s\[\]]*(?:\]|(?=\s|$|[.,;:!?。！？]))",
    re.IGNORECASE,
)

# Full-match validator for legitimate citations
_VALID_CITATION_FULL_RE = re.compile(r"^\[S\d+\]$")

# Malformed citation patterns (specific known-bad shapes)
# NOTE: removed the buggy r"\[S\d+(?!\])" — it backtracks and matches [S1 inside [S12].
# Unclosed-bracket detection is now handled by the two-phase candidate approach.
_MALFORMED_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("[Sx]", re.compile(r"\[S[xX]\]"), "non-numeric suffix 'x'"),
    ("[SA]", re.compile(r"\[S[A-Za-z](?:\d+)?\]"), "letter where digit expected"),
    ("[S]", re.compile(r"\[S\]"), "missing citation number"),
    ("[S-1]", re.compile(r"\[S-\d+\]"), "negative citation number"),
    ("[S1x]", re.compile(r"\[S\d+[A-Za-z]\]"), "trailing letter in citation"),
    ("[s1]", re.compile(r"\[s\d+\]"), "lowercase 's' in citation"),
]

_BARE_URL_RE = re.compile(r"https?://[^\s\)\]一-鿿]*")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。！？\n])\s*")

_REFERENCE_SECTION_HEADERS = re.compile(
    r"^#{1,4}\s*(?:Sources?|References?|参考文献|参考资料|引用来源|来源列表)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

DEFAULT_TRACEABILITY_THRESHOLDS: dict[str, float] = {
    "min_citation_rate": 0.3,
    "max_body_bare_urls": 0,
    "max_orphan_count": 0,
}


class SourceTraceabilityScorer(Scorer):
    dimension = "source_traceability"
    layer = "component"

    def __init__(self, min_citation_rate: float = 0.3,
                 thresholds: dict[str, float] | None = None):
        self.min_citation_rate = min_citation_rate
        self.thresholds = {**DEFAULT_TRACEABILITY_THRESHOLDS, **(thresholds or {})}
        if "min_citation_rate" not in (thresholds or {}):
            self.thresholds["min_citation_rate"] = min_citation_rate

    def score(self, report_text: str = "",
              source_registry: dict[str, Any] | None = None,
              **kwargs: Any) -> ScoreResult:
        body_text, refs_text = self._split_body_and_references(report_text)

        # Citations
        all_citations = _CITATION_RE.findall(report_text)
        body_citations = _CITATION_RE.findall(body_text)

        # --- Malformed citations: two-phase detection ---
        malformed_list: list[dict[str, str]] = []
        seen_malformed: set[tuple[int, int, str]] = set()

        # Phase 1: Extract all [S...] candidates and check with fullmatch
        for m in _CITATION_CANDIDATE_RE.finditer(report_text):
            candidate = m.group(0)
            span = (m.start(), m.end())
            if _VALID_CITATION_FULL_RE.fullmatch(candidate):
                continue  # Valid citation, skip
            # This is malformed — dedup by (start, end, match_text)
            dedup_key = (span[0], span[1], candidate)
            if dedup_key not in seen_malformed:
                seen_malformed.add(dedup_key)
                malformed_list.append({
                    "match": candidate,
                    "type": "unclosed" if not candidate.endswith("]") else "malformed",
                    "description": (
                        "missing closing bracket" if not candidate.endswith("]")
                        else f"malformed citation: {candidate}"
                    ),
                })

        # Phase 2: Check known-bad patterns (may overlap with candidates above,
        # dedup prevents double-counting)
        for label, pattern, description in _MALFORMED_PATTERNS:
            for m in pattern.finditer(report_text):
                dedup_key = (m.start(), m.end(), m.group(0))
                if dedup_key not in seen_malformed:
                    seen_malformed.add(dedup_key)
                    malformed_list.append({
                        "match": m.group(0),
                        "type": label,
                        "description": description,
                    })

        malformed_count = len(malformed_list)

        # Bare URLs
        all_bare_urls = _BARE_URL_RE.findall(report_text)
        body_bare_urls = _BARE_URL_RE.findall(body_text)
        refs_bare_urls = _BARE_URL_RE.findall(refs_text)

        # Sentences
        body_sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(body_text) if s.strip()]
        factual_sentences = [s for s in body_sentences
                            if len(s) >= 10 and not s.startswith("#") and not s.startswith("[")]
        factual_sentence_count = max(len(factual_sentences), 1)
        cited_factual_sentences = [s for s in factual_sentences if _CITATION_RE.search(s)]

        # Orphan detection
        orphan_count = 0
        orphan_ids: list[str] = []
        has_citations = bool(all_citations)

        if has_citations:
            if source_registry:
                normalized_registry_ids: set[str] = set()
                for key in source_registry:
                    key_str = str(key)
                    if key_str.upper().startswith("S") and key_str[1:].isdigit():
                        normalized_registry_ids.add(f"S{key_str[1:]}")
                    elif key_str.isdigit():
                        normalized_registry_ids.add(f"S{key_str}")
                for c in all_citations:
                    citation_id = f"S{c}"
                    if citation_id not in normalized_registry_ids:
                        orphan_count += 1
                        orphan_ids.append(citation_id)
            else:
                # No registry provided but citations exist → can't validate → fail
                pass  # Handled in status determination below

        # Metrics
        unique_sources = len(set(all_citations))
        total_citation_instances = len(all_citations)
        citation_instance_density = total_citation_instances / factual_sentence_count if factual_sentence_count > 0 else 0.0
        cited_sentence_rate = len(cited_factual_sentences) / factual_sentence_count if factual_sentence_count > 0 else 0.0

        # --- Status determination with hard thresholds ---
        if malformed_count > 0:
            status = "fail"
            status_reason = f"Malformed citations detected: {[m['match'] for m in malformed_list[:5]]}"
        elif orphan_count > 0:
            status = "fail"
            status_reason = f"Orphan citations detected: {orphan_ids}"
        elif has_citations and (source_registry is None or len(source_registry) == 0):
            status = "fail"
            status_reason = "Citations present but no source_registry provided — cannot cross-validate"
        elif not has_citations:
            status = "fail"
            status_reason = "No [Sn] citations found in report"
        elif len(body_bare_urls) > 0:
            # Body bare URL with threshold=0 → fail, not partial
            status = "fail"
            status_reason = f"Body bare URLs detected ({len(body_bare_urls)}); max allowed is {self.thresholds['max_body_bare_urls']}"
        elif cited_sentence_rate >= self.thresholds["min_citation_rate"]:
            status = "pass"
            status_reason = "All hard thresholds met"
        elif cited_sentence_rate >= self.thresholds["min_citation_rate"] * 0.5:
            status = "partial"
            status_reason = f"Cited sentence rate ({cited_sentence_rate:.0%}) below target ({self.thresholds['min_citation_rate']:.0%})"
        else:
            status = "fail"
            status_reason = f"Cited sentence rate ({cited_sentence_rate:.0%}) far below target ({self.thresholds['min_citation_rate']:.0%})"

        # Composite (for ranking)
        has_cit = 1.0 if all_citations else 0.0
        density_score = min(citation_instance_density / max(self.min_citation_rate, 0.01), 1.0)
        cited_rate_score = min(cited_sentence_rate / max(self.min_citation_rate, 0.01), 1.0)
        bare_penalty = max(0.0, 1.0 - len(body_bare_urls) * 0.25)
        orphan_penalty = 1.0 if (not all_citations or orphan_count == 0) else max(0.0, 1.0 - orphan_count / max(len(all_citations), 1))
        malformed_penalty = max(0.0, 1.0 - malformed_count * 0.2)

        composite = round(0.20 * has_cit + 0.15 * density_score + 0.15 * cited_rate_score
                         + 0.20 * bare_penalty + 0.15 * orphan_penalty + 0.15 * malformed_penalty, 4)

        issues: list[str] = []
        if not all_citations:
            issues.append("No [Sn] citations found in report")
        if body_bare_urls:
            issues.append(f"{len(body_bare_urls)} bare URL(s) in body: {body_bare_urls[:3]}")
        if orphan_count > 0:
            issues.append(f"{orphan_count} orphan citation(s): {orphan_ids}")
        if malformed_count > 0:
            issues.append(f"{malformed_count} malformed citation(s): {[m['match'] for m in malformed_list[:5]]}")
        if has_citations and (source_registry is None or len(source_registry) == 0):
            issues.append("Citations present but source_registry is empty/None — cannot verify")

        threshold_checks = {
            "has_citations": {"value": bool(all_citations), "threshold": True, "passed": bool(all_citations)},
            "orphan_count": {"value": orphan_count, "threshold": self.thresholds["max_orphan_count"],
                            "passed": orphan_count <= self.thresholds["max_orphan_count"]},
            "body_bare_url_count": {"value": len(body_bare_urls), "threshold": self.thresholds["max_body_bare_urls"],
                                    "passed": len(body_bare_urls) <= self.thresholds["max_body_bare_urls"]},
            "malformed_citation_count": {"value": malformed_count, "threshold": 0,
                                        "passed": malformed_count == 0},
            "cited_sentence_rate": {"value": round(cited_sentence_rate, 4),
                                    "threshold": self.thresholds["min_citation_rate"],
                                    "passed": cited_sentence_rate >= self.thresholds["min_citation_rate"]},
        }

        return ScoreResult(
            dimension=self.dimension, layer=self.layer,
            value=composite * 2, max_value=2, normalized=composite,
            status=status,
            details=(f"Citations: {unique_sources} unique / {total_citation_instances} total. "
                    f"Density: {citation_instance_density:.2f}/sent. "
                    f"Cited rate: {cited_sentence_rate:.0%} ({len(cited_factual_sentences)}/{factual_sentence_count}). "
                    f"Orphans: {orphan_count}. Malformed: {malformed_count}. "
                    f"Body bare URLs: {len(body_bare_urls)}. Refs URLs: {len(refs_bare_urls)}. "
                    f"Status: {status} — {status_reason}"),
            issues=issues,
            evidence={
                "unique_sources_cited": unique_sources,
                "total_citation_instances": total_citation_instances,
                "citation_instance_density": round(citation_instance_density, 4),
                "cited_sentence_rate": round(cited_sentence_rate, 4),
                "cited_factual_sentence_count": len(cited_factual_sentences),
                "factual_sentence_count": factual_sentence_count,
                "total_sentence_count": len(body_sentences),
                "orphan_citations": orphan_count,
                "orphan_ids": orphan_ids,
                "malformed_citation_count": malformed_count,
                "malformed_citations": malformed_list,
                "bare_urls_found": len(all_bare_urls),
                "body_bare_urls": len(body_bare_urls),
                "refs_bare_urls": len(refs_bare_urls),
                "body_bare_url_list": body_bare_urls,
                "composite_score": composite,
                "thresholds": self.thresholds,
                "threshold_checks": threshold_checks,
                "status_reason": status_reason,
            },
        )

    @staticmethod
    def _split_body_and_references(report_text: str) -> tuple[str, str]:
        match = _REFERENCE_SECTION_HEADERS.search(report_text)
        if match:
            return report_text[:match.start()], report_text[match.start():]
        return report_text, ""
