"""
Fact Reconciler — manages the lifecycle of MemoryFact objects.

Uses subject/predicate/value/period decomposition for precise matching.
Replaces token-Jaccard-on-whole-sentence with structured comparison.

Operations:
- ADD: New fact with no subject/predicate match in existing.
- UPDATE: Same subject/predicate/period, improved detail or quality.
- INVALIDATE: Old fact proven wrong/outdated by newer information.
- NONE: Semantic equivalent; no incremental value.
- CONFLICT: Same subject/predicate/period, contradictory value/direction.

Key rules:
- Old values and source IDs are always preserved via FactLedger.
- Period mismatch means different time-point facts (no conflict).
- Predicate mismatch means different facts (ADD, not UPDATE/CONFLICT).
- updated_at is a write timestamp, NOT used for conflict resolution.
"""
from __future__ import annotations

import logging
from typing import Any

from harness.models.memory import (
    MemoryFact,
    MemoryOperation,
    FactLedger,
    _normalize_fact_text,
    _now_iso,
    evidence_quality_from_sources,
)

logger = logging.getLogger(__name__)


def _cjk_aware_tokens(text: str) -> set[str]:
    """Tokenize for Jaccard overlap: characters for CJK-heavy text (which has
    no whitespace between words, so word-splitting collapses a whole sentence
    into a single token), words otherwise.
    """
    cjk_count = sum(1 for ch in text if '一' <= ch <= '鿿' or '㐀' <= ch <= '䶿')
    if cjk_count > len(text) * 0.3:
        return {ch for ch in text if ch.strip()}
    return set(text.split())


# ===========================================================================
# FactReconciler
# ===========================================================================


class FactReconciler:
    """Reconciles incoming facts against existing facts using structured
    subject/predicate/value matching.

    Parameters
    ----------
    category_whitelist : frozenset[str] | None
        Allowed primary categories. Facts outside this set get ``"other"``.
    domain_config : MemoryDomainConfig | None
        Domain-specific config with predicate aliases and categories.
    """

    def __init__(
        self,
        category_whitelist: frozenset[str] | None = None,
        domain_config: Any = None,
    ):
        self.category_whitelist = category_whitelist
        self._domain_config = domain_config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reconcile(
        self,
        incoming: list[MemoryFact],
        existing: list[MemoryFact],
    ) -> FactLedger:
        """Reconcile incoming facts against existing facts.

        Parameters
        ----------
        incoming : list[MemoryFact]
            New facts extracted from the current turn.
        existing : list[MemoryFact]
            ALL facts (active + historical) in working memory.

        Returns
        -------
        FactLedger
            Complete ledger with all facts, active subset, and operations log.
            Historical (invalidated/superseded) facts are preserved.
        """
        # Preserve ALL existing facts in the ledger
        all_facts: dict[str, MemoryFact] = {f.fact_id: f for f in existing}
        active_ids: set[str] = {f.fact_id for f in existing if f.is_active}
        operations_log: list[dict[str, Any]] = []

        for new_fact in incoming:
            new_fact.primary_category = self._validate_category(new_fact.primary_category)

            # Find candidates by subject + predicate (NOT just subject+period)
            candidates = self._find_candidates_spdv(new_fact, [all_facts[fid] for fid in active_ids])

            if not candidates:
                # ADD — no matching subject+predicate pair
                new_fact.status = "active"
                all_facts[new_fact.fact_id] = new_fact
                active_ids.add(new_fact.fact_id)
                operations_log.append({
                    "operation": MemoryOperation.ADD.value,
                    "fact_id": new_fact.fact_id,
                    "reason": "no_matching_subject_predicate",
                })
                continue

            # Resolve against best candidate
            best, op = self._resolve_spdv(new_fact, candidates)

            if op == MemoryOperation.ADD:
                # Different period or distinct fact → ADD as new active fact
                new_fact.status = "active"
                all_facts[new_fact.fact_id] = new_fact
                active_ids.add(new_fact.fact_id)
                operations_log.append({
                    "operation": MemoryOperation.ADD.value,
                    "fact_id": new_fact.fact_id,
                    "reason": "distinct_period_or_compatible_fact",
                })
                continue

            if op == MemoryOperation.NONE:
                # Same value, but possibly a NEW independent source confirming
                # it — that's corroboration, not a no-op. Still merge
                # source_ids and re-derive evidence_quality, or a second
                # source agreeing with the first would be silently dropped.
                for sid in new_fact.source_ids:
                    if sid not in best.source_ids:
                        best.source_ids.append(sid)
                best.evidence_quality = evidence_quality_from_sources(best.source_ids)
                operations_log.append({
                    "operation": MemoryOperation.NONE.value,
                    "fact_id": new_fact.fact_id,
                    "matched_id": best.fact_id,
                    "reason": "semantic_equivalent",
                })
                continue

            if op == MemoryOperation.UPDATE:
                # OPTION A: Preserve original fact_id, record revision history
                revision_entry = {
                    "previous_text": best.text,
                    "previous_value": best.value,
                    "previous_unit": best.unit,
                    "previous_period": best.period,
                    "previous_evidence_quality": best.evidence_quality,
                    "previous_source_ids": list(best.source_ids),
                    "revised_at": _now_iso(),
                }
                if not best.revision_history:
                    best.revision_history = []
                best.revision_history.append(revision_entry)

                # Update the existing fact in-place
                best.text = new_fact.text
                best.normalized_text = new_fact.normalized_text or _normalize_fact_text(new_fact.text)
                best.turn_id = new_fact.turn_id
                best.value = new_fact.value
                best.unit = new_fact.unit
                best.period = new_fact.period
                best.subject = new_fact.subject or best.subject
                best.predicate = new_fact.predicate or best.predicate
                # Merge source IDs, then re-derive evidence_quality from the
                # MERGED count — corroboration by a later round (a second
                # distinct source) must be able to upgrade quality, which is
                # lost if we just overwrite with the new round's own
                # (single-round) evidence_quality before merging.
                for sid in new_fact.source_ids:
                    if sid not in best.source_ids:
                        best.source_ids.append(sid)
                best.evidence_quality = evidence_quality_from_sources(best.source_ids)
                best.updated_at = _now_iso()
                # NOT using new_fact.fact_id — this preserves the existing ID
                operations_log.append({
                    "operation": MemoryOperation.UPDATE.value,
                    "fact_id": best.fact_id,
                    "old_text": revision_entry["previous_text"],
                    "new_text": new_fact.text,
                    "reason": "improved_detail_or_quality",
                })
                continue

            if op == MemoryOperation.INVALIDATE:
                # Mark old fact as invalidated; add new fact as active
                best.status = "invalidated"
                best.updated_at = _now_iso()
                new_fact.status = "active"
                # Note: supersedes on best is left empty — old fact stays in ledger
                all_facts[new_fact.fact_id] = new_fact
                active_ids.discard(best.fact_id)
                active_ids.add(new_fact.fact_id)
                operations_log.append({
                    "operation": MemoryOperation.INVALIDATE.value,
                    "fact_id": best.fact_id,
                    "replacement_id": new_fact.fact_id,
                    "old_text": best.text,
                    "new_text": new_fact.text,
                    "reason": "old_fact_invalidated_by_new_evidence",
                })
                continue

            if op == MemoryOperation.CONFLICT:
                # Both facts remain active with cross-references
                new_fact.status = "active"
                if best.fact_id not in new_fact.conflicts_with:
                    new_fact.conflicts_with = list(set(new_fact.conflicts_with + [best.fact_id]))
                all_facts[new_fact.fact_id] = new_fact
                active_ids.add(new_fact.fact_id)
                if new_fact.fact_id not in best.conflicts_with:
                    best.conflicts_with = list(set(best.conflicts_with + [new_fact.fact_id]))
                all_facts[best.fact_id] = best
                operations_log.append({
                    "operation": MemoryOperation.CONFLICT.value,
                    "fact_id": new_fact.fact_id,
                    "conflicts_with": best.fact_id,
                    "reason": "contradictory_values_from_different_sources",
                })
                continue

        return FactLedger(
            all_facts=list(all_facts.values()),
            active_fact_ids=active_ids,
            operations=operations_log,
        )

    # ------------------------------------------------------------------
    # SPDV-based matching
    # ------------------------------------------------------------------

    def _find_candidates_spdv(
        self,
        new_fact: MemoryFact,
        active: list[MemoryFact],
    ) -> list[MemoryFact]:
        """Find candidates by subject + predicate.

        Candidate matching requires BOTH subject AND predicate to match.
        Period alone does NOT trigger matching — different predicates with
        same subject+period are different facts.

        Uses predicate aliases from domain_config when available.
        """
        candidates: list[MemoryFact] = []

        new_subj = (new_fact.subject or "").lower().strip()
        new_pred = (new_fact.predicate or "").lower().strip()

        # If we have structured SPDV, use it
        if new_subj and new_pred:
            for fact in active:
                f_subj = (fact.subject or "").lower().strip()
                f_pred = (fact.predicate or "").lower().strip()

                if not f_subj or not f_pred:
                    continue

                # Must match BOTH subject AND predicate
                subj_match = self._same_subject(new_subj, f_subj)
                pred_match = self._same_predicate(new_pred, f_pred)

                if subj_match and pred_match:
                    candidates.append(fact)

            if candidates:
                return candidates

        # Fallback: text-based matching for facts without SPDV
        return self._find_candidates_text(new_fact, active)

    def _same_subject(self, a: str, b: str) -> bool:
        """Check if two subject strings refer to the same entity."""
        a_clean = a.lower().strip()
        b_clean = b.lower().strip()
        if a_clean == b_clean:
            return True
        # Partial containment
        if a_clean in b_clean or b_clean in a_clean:
            return True
        return False

    def _same_predicate(self, a: str, b: str) -> bool:
        """Check if two predicate strings refer to the same concept.

        Uses domain_config predicate aliases when available.
        """
        if self._domain_config is not None:
            return self._domain_config.are_predicates_equivalent(a, b)
        # Fallback: exact match
        return a.lower().strip() == b.lower().strip()

    @staticmethod
    def _periods_are_distinct(a: str | None, b: str | None) -> bool:
        """Check if two periods are clearly different time points."""
        if not a or not b:
            return False
        a_clean = a.lower().strip()
        b_clean = b.lower().strip()
        if a_clean == b_clean:
            return False
        # If both are years/dates and different → distinct
        import re
        a_year = re.search(r'\b(20\d{2})\b', a_clean)
        b_year = re.search(r'\b(20\d{2})\b', b_clean)
        if a_year and b_year and a_year.group(1) != b_year.group(1):
            return True
        return False

    def _find_candidates_text(
        self,
        new_fact: MemoryFact,
        active: list[MemoryFact],
    ) -> list[MemoryFact]:
        """Fallback: find candidates by text overlap (used when SPDV empty)."""
        new_norm = new_fact.normalized_text or _normalize_fact_text(new_fact.text)
        candidates = []
        for fact in active:
            old_norm = fact.normalized_text or _normalize_fact_text(fact.text)
            # Same category + significant token overlap (CJK-aware)
            if fact.primary_category == new_fact.primary_category:
                new_tokens = _cjk_aware_tokens(new_norm)
                old_tokens = _cjk_aware_tokens(old_norm)
                if new_tokens and old_tokens:
                    overlap = len(new_tokens & old_tokens) / max(len(new_tokens), len(old_tokens))
                    if overlap > 0.3:
                        candidates.append(fact)
        return candidates

    def _resolve_spdv(
        self,
        new_fact: MemoryFact,
        candidates: list[MemoryFact],
    ) -> tuple[MemoryFact, MemoryOperation]:
        """Determine operation against best SPDV candidate."""
        best = candidates[0]
        best_score = 0.0

        for c in candidates:
            score = self._spdv_match_score(new_fact, c)
            if score > best_score:
                best = c
                best_score = score

        # If periods are distinct, this is a different time-point fact → ADD
        if self._periods_are_distinct(new_fact.period, best.period):
            return best, MemoryOperation.ADD

        # Check for value equivalence → NONE
        if self._values_are_equivalent(new_fact, best):
            return best, MemoryOperation.NONE

        # Check for contradiction → CONFLICT
        if self._values_contradict(new_fact, best):
            if self._can_resolve(new_fact, best):
                return best, MemoryOperation.INVALIDATE
            return best, MemoryOperation.CONFLICT

        # Check for improvement → UPDATE
        if self._is_improvement(new_fact, best):
            return best, MemoryOperation.UPDATE

        # Different facts with same subject/predicate but compatible
        # If they're very different → ADD
        if best_score < 0.6:
            return best, MemoryOperation.ADD

        return best, MemoryOperation.NONE

    def _spdv_match_score(self, new_fact: MemoryFact, existing: MemoryFact) -> float:
        """Score SPDV match quality (0.0 to 1.0)."""
        score = 0.0

        # Subject match: 0.4
        ns = (new_fact.subject or "").lower().strip()
        es = (existing.subject or "").lower().strip()
        if ns and es and self._same_subject(ns, es):
            score += 0.4

        # Predicate match: 0.3
        np = (new_fact.predicate or "").lower().strip()
        ep = (existing.predicate or "").lower().strip()
        if np and ep and self._same_predicate(np, ep):
            score += 0.3

        # Period match: 0.2
        nper = (new_fact.period or "").lower().strip()
        eper = (existing.period or "").lower().strip()
        if nper and eper and nper == eper:
            score += 0.2

        # Category bonus: 0.1
        if new_fact.primary_category == existing.primary_category:
            score += 0.1

        return score

    @staticmethod
    def _values_are_equivalent(new_fact: MemoryFact, existing: MemoryFact) -> bool:
        """Check if two facts assert the same value."""
        nv = new_fact.value
        ev = existing.value
        if nv is not None and ev is not None:
            # Numeric comparison
            try:
                nvf = float(nv)
                evf = float(ev)
                if evf != 0:
                    diff = abs(nvf - evf) / abs(evf)
                    return diff < 0.05  # Within 5%
                return abs(nvf - evf) < 0.001
            except (ValueError, TypeError):
                pass
            # String comparison
            return str(nv).lower().strip() == str(ev).lower().strip()

        # Fall back to text equivalence (CJK-aware token overlap)
        nn = new_fact.normalized_text or _normalize_fact_text(new_fact.text)
        en = existing.normalized_text or _normalize_fact_text(existing.text)
        n_tokens = _cjk_aware_tokens(nn)
        e_tokens = _cjk_aware_tokens(en)
        if not n_tokens or not e_tokens:
            return False
        overlap = len(n_tokens & e_tokens) / max(len(n_tokens | e_tokens), 1)
        return overlap >= 0.85

    @staticmethod
    def _values_contradict(new_fact: MemoryFact, existing: MemoryFact) -> bool:
        """Check if two facts contradict each other.

        Uses both numeric comparison and keyword-based direction detection.
        Only considers contradiction when subject and predicate match.
        """
        # Must be same subject+predicate (checked before calling)
        # Numeric contradiction (significant difference)
        nv = new_fact.value
        ev = existing.value
        if nv is not None and ev is not None:
            try:
                nvf = float(nv)
                evf = float(ev)
                if evf != 0:
                    diff = abs(nvf - evf) / abs(evf)
                    if diff > 0.2:
                        return True
            except (ValueError, TypeError):
                pass

        # Keyword-based contradiction detection
        contra_pairs = [
            ("increase", "decrease"),
            ("growth", "decline"),
            ("profit", "loss"),
            ("low", "high"),
            ("strong", "weak"),
            ("leader", "follower"),
            ("expand", "shrink"),
            ("grew", "shrunk"),
            ("增长", "下降"),
            ("增长", "下滑"),
            ("上涨", "下跌"),
            ("盈利", "亏损"),
            ("利润", "亏损"),
            ("强劲", "疲软"),
            ("领先", "落后"),
            ("扩张", "收缩"),
            ("上升", "下降"),
        ]
        nt = new_fact.text.lower()
        et = existing.text.lower()
        for pos, neg in contra_pairs:
            if (pos in nt and neg in et) or (neg in nt and pos in et):
                # Must share same subject
                ns = (new_fact.subject or "").lower()
                es = (existing.subject or "").lower()
                if ns and es and ns == es:
                    return True
                # Or significant text overlap indicating same topic (CJK-aware)
                nn = _cjk_aware_tokens(_normalize_fact_text(new_fact.text))
                en = _cjk_aware_tokens(_normalize_fact_text(existing.text))
                overlap = len(nn & en) / max(len(nn | en), 1)
                if overlap > 0.25:
                    return True
        return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _validate_category(self, category: str) -> str:
        """Validate and fallback a primary category.

        Uses domain_config categories if available, otherwise hardcoded whitelist.
        """
        if self._domain_config is not None:
            valid = self._domain_config.all_categories
            if category in valid:
                return category
            return self._domain_config.fallback_category

        if self.category_whitelist and category not in self.category_whitelist:
            logger.debug(f"Category '{category}' not in whitelist; using 'other'")
            return "other"
        if category not in {
            "business_model", "growth", "risk", "competition",
            "financials", "other",
        }:
            return "other"
        return category

    @staticmethod
    def _can_resolve(new_fact: MemoryFact, existing: MemoryFact) -> bool:
        """Determine if contradiction can be resolved in favor of the new fact.

        Resolution is based ONLY on evidence quality, source authority, and
        explicit correction signals.  updated_at is a WRITE TIMESTAMP and is
        NOT used for conflict resolution.

        Returns True only when:
        - New fact has clearly higher evidence quality AND more sources
        - OR new fact explicitly corrects/restates old data
        """
        quality_order = {"high": 3, "medium": 2, "low": 1}
        new_q = quality_order.get(new_fact.evidence_quality, 1)
        old_q = quality_order.get(existing.evidence_quality, 1)

        # Higher quality + more sources → can resolve
        if new_q > old_q and new_fact.source_ids:
            return True

        # Explicit correction/restatement keywords in new fact text
        correction_keywords = ["corrected", "restated", "revised", "updated figure",
                               "更正", "修正", "重述"]
        new_text_lower = new_fact.text.lower()
        if any(kw in new_text_lower for kw in correction_keywords):
            if new_q >= old_q:
                return True

        # New fact has sources and old fact doesn't
        if new_fact.source_ids and not existing.source_ids:
            return True

        return False

    @staticmethod
    def _is_improvement(new_fact: MemoryFact, existing: MemoryFact) -> bool:
        """Check if the new fact is a meaningful improvement."""
        if len(new_fact.text) > len(existing.text) * 1.2:
            return True
        quality_order = {"high": 3, "medium": 2, "low": 0}
        if quality_order.get(new_fact.evidence_quality, 0) > quality_order.get(existing.evidence_quality, 0):
            return True
        if new_fact.source_ids and not existing.source_ids:
            return True
        return False
