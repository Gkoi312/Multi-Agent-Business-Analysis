"""
Pipeline Quality Scorer — measures pipeline accuracy using labeled fixtures.

Key fixes:
- Uses metadata["fixture_index"] for stable identification (not enumerate)
- Does NOT fake per-stage attribution — only reports stage when explicitly known
- Always computes missing docs via set difference (not just when actual_dropped empty)
- eval_errors force status=fail
- reason_accuracy is None/not_evaluable when no expected drops
- Per-stage metrics: only when stage attribution coverage is sufficient
"""

from __future__ import annotations

from typing import Any

from harness.evaluation.scorer import ScoreResult, Scorer
from harness.tools.pipeline import ToolContext, ToolPipeline
from harness.tools.search.base import SearchDocument
from harness.tools.search.cleaner import SEARCH_PIPELINE_FULL

DEFAULT_REASON_ALIASES: dict[str, set[str]] = {
    "duplicate": {"exact_dedup", "near_dedup"},
    "duplicate_url": {"exact_dedup"},
    "near_duplicate": {"near_dedup"},
    "irrelevant": {"relevance"},
    "low_relevance": {"relevance"},
    "empty_content": {"clean_text"},
    "low_quality": {"quality"},
    "spam": {"quality"},
    "malformed": {"clean_text", "format"},
    "output_guard": {"output_guard"},
    "structure": {"structure"},
}

DEFAULT_PIPELINE_THRESHOLDS: dict[str, float] = {
    "drop_precision_min": 0.95,
    "drop_recall_min": 0.90,
    "false_drop_rate_max": 0.10,
    "reason_accuracy_min": 0.90,
}

_ALL_STAGE_NAMES = [
    "canonicalize_url", "clean_text", "exact_dedup", "near_dedup",
    "relevance", "quality", "structure", "output_guard", "format",
]


class PipelineQualityScorer(Scorer):
    dimension = "pipeline_quality"
    layer = "component"

    def __init__(self, pipeline: ToolPipeline | None = None,
                 reason_aliases: dict[str, set[str]] | None = None,
                 thresholds: dict[str, float] | None = None):
        self.pipeline = pipeline or ToolPipeline(SEARCH_PIPELINE_FULL)
        self.reason_aliases = reason_aliases or DEFAULT_REASON_ALIASES
        self.thresholds = {**DEFAULT_PIPELINE_THRESHOLDS, **(thresholds or {})}

    def score(self, fixture: dict[str, Any] | None = None, **kwargs: Any) -> ScoreResult:
        if fixture is None:
            return ScoreResult(dimension=self.dimension, layer=self.layer,
                              value=0, max_value=2, normalized=0, status="fail",
                              details="No fixture provided", issues=["Missing fixture"])

        raw_results = fixture.get("raw_results", []) or []
        expected_kept_indices: set[int] = set(fixture.get("expected_kept_indices", []) or [])
        expected_dropped_raw = fixture.get("expected_dropped_reasons", {}) or {}
        target_entity = str(fixture.get("target_entity", "") or "")

        expected_dropped: dict[int, str] = {}
        for k, v in expected_dropped_raw.items():
            try:
                expected_dropped[int(k)] = str(v)
            except (ValueError, TypeError):
                expected_dropped[int(k) if isinstance(k, int) else k] = str(v)

        if not raw_results:
            return ScoreResult.skipped(dimension=self.dimension, layer=self.layer,
                                      reason="Empty fixture — no documents to evaluate")

        total_input = len(raw_results)
        all_input_indices = set(range(total_input))

        # Build SearchDocuments with fixture_index in metadata
        docs: list[SearchDocument] = []
        for idx, raw in enumerate(raw_results):
            docs.append(self._make_doc(raw, fixture_index=idx))

        ctx = ToolContext(target_entity=target_entity, target_focus="", source_type="web")
        cleaned, trace = self.pipeline.run_with_trace(docs, ctx)

        # --- Classify kept/dropped by fixture_index ---
        actual_kept: set[int] = set()
        explicitly_dropped: dict[int, str] = {}     # fixture_index → dropped_reason
        actual_drop_stage: dict[int, str] = {}       # fixture_index → stage
        untraceable_count = 0

        for doc in cleaned:
            fix_idx = doc.metadata.get("fixture_index")
            if fix_idx is None:
                fix_idx = self._find_fixture_index_by_url(doc, raw_results)
            if fix_idx is None:
                untraceable_count += 1
                continue
            if doc.dropped_reason:
                explicitly_dropped[fix_idx] = doc.dropped_reason
                actual_drop_stage[fix_idx] = self._resolve_drop_stage(doc, trace)
            else:
                actual_kept.add(fix_idx)

        # ALWAYS compute missing docs via set difference
        explicitly_dropped_indices = set(explicitly_dropped.keys())
        missing = all_input_indices - actual_kept - explicitly_dropped_indices
        for mi in missing:
            explicitly_dropped[mi] = "dropped_by_pipeline"
            actual_drop_stage[mi] = "unknown"

        actual_dropped_indices = set(explicitly_dropped.keys())
        union_set = actual_kept | actual_dropped_indices
        intersection_set = actual_kept & actual_dropped_indices

        eval_errors: list[str] = []
        if union_set != all_input_indices:
            still_missing = all_input_indices - union_set
            eval_errors.append(f"Evaluation error: {len(still_missing)} doc(s) unaccounted: {sorted(still_missing)}")
        if intersection_set:
            eval_errors.append(f"Evaluation error: {len(intersection_set)} doc(s) in both kept and dropped: {sorted(intersection_set)}")
        if untraceable_count > 0:
            eval_errors.append(f"Evaluation error: {untraceable_count} doc(s) untraceable to fixture")
        # Fixture coverage check
        fixture_labeled = expected_kept_indices | set(expected_dropped.keys())
        if fixture_labeled != all_input_indices:
            unlabeled = all_input_indices - fixture_labeled
            eval_errors.append(f"Fixture coverage: {len(unlabeled)} doc(s) have no expected label: {sorted(unlabeled)}")

        # --- Core metrics ---
        tp_dropped = len(actual_dropped_indices & set(expected_dropped.keys()))
        fp_dropped = len(actual_dropped_indices - set(expected_dropped.keys()))
        fn_dropped = len(set(expected_dropped.keys()) - actual_dropped_indices)
        tn_kept = len(expected_kept_indices & actual_kept)

        drop_precision = tp_dropped / (tp_dropped + fp_dropped) if (tp_dropped + fp_dropped) > 0 else 1.0
        drop_recall = tp_dropped / (tp_dropped + fn_dropped) if (tp_dropped + fn_dropped) > 0 else 1.0
        keep_precision = tn_kept / len(actual_kept) if actual_kept else 1.0
        keep_recall = tn_kept / len(expected_kept_indices) if expected_kept_indices else 1.0

        false_drop_count = fp_dropped
        expected_kept_count = len(expected_kept_indices)
        false_drop_rate = false_drop_count / expected_kept_count if expected_kept_count > 0 else 0.0

        # --- Per-stage metrics (only with known attribution) ---
        stage_attributed = sum(1 for stg in actual_drop_stage.values() if stg != "unknown")
        stage_total = len(actual_drop_stage)
        stage_attribution_coverage = stage_attributed / stage_total if stage_total > 0 else 1.0

        per_stage_quality: dict[str, Any] = {}
        if stage_attribution_coverage >= 0.5:  # Only compute if we have meaningful attribution
            for stage_name in _ALL_STAGE_NAMES:
                stage_dropped = {idx for idx, stg in actual_drop_stage.items() if stg == stage_name}
                stage_expected = {idx for idx, reason in expected_dropped.items()
                                  if self._reason_matches_stage(reason, stage_name)}
                stage_tp = len(stage_dropped & stage_expected)
                stage_fp = len(stage_dropped - stage_expected)
                stage_fn = len(stage_expected - stage_dropped)
                n_total = stage_tp + stage_fp + stage_fn
                if n_total > 0:
                    per_stage_quality[stage_name] = {
                        "precision": round(stage_tp / (stage_tp + stage_fp), 4) if (stage_tp + stage_fp) > 0 else None,
                        "recall": round(stage_tp / (stage_tp + stage_fn), 4) if (stage_tp + stage_fn) > 0 else None,
                        "false_drop_rate": round(stage_fp / (stage_tp + stage_fp), 4) if (stage_tp + stage_fp) > 0 else None,
                        "tp": stage_tp, "fp": stage_fp, "fn": stage_fn,
                        "evaluable": (stage_tp + stage_fp) > 0,  # precision/fdr only meaningful if >0
                    }

        # --- Reason accuracy (only when expected_dropped is non-empty) ---
        if expected_dropped:
            reason_results = self._compute_reason_accuracy(expected_dropped, explicitly_dropped, actual_drop_stage)
        else:
            reason_results = {"reason_accuracy": None, "stage_accuracy": None,
                             "reason_accuracy_eligible": False, "confusion_matrix": [], "wrong_reason_indices": []}

        # --- Wrongly classified indices ---
        wrongly_dropped_indices = sorted(actual_dropped_indices - set(expected_dropped.keys()))
        wrongly_kept_indices = sorted(set(expected_dropped.keys()) - actual_dropped_indices)
        wrong_reason_indices = sorted(reason_results.get("wrong_reason_indices", []))

        # --- Composite score ---
        ra = reason_results.get("reason_accuracy")
        ra_val = ra if ra is not None else 1.0  # Don't penalize when N/A
        composite = round(0.30 * drop_precision + 0.25 * drop_recall
                         + 0.25 * (1.0 - false_drop_rate) + 0.20 * ra_val, 4)

        # --- Hard threshold checks (skip reason_accuracy when N/A) ---
        threshold_checks = {
            "drop_precision": {"value": round(drop_precision, 4),
                "threshold": self.thresholds["drop_precision_min"],
                "passed": drop_precision >= self.thresholds["drop_precision_min"]},
            "drop_recall": {"value": round(drop_recall, 4),
                "threshold": self.thresholds["drop_recall_min"],
                "passed": drop_recall >= self.thresholds["drop_recall_min"]},
            "false_drop_rate": {"value": round(false_drop_rate, 4),
                "threshold": self.thresholds["false_drop_rate_max"],
                "passed": false_drop_rate <= self.thresholds["false_drop_rate_max"]},
        }
        if ra is not None:
            threshold_checks["reason_accuracy"] = {"value": round(ra, 4),
                "threshold": self.thresholds["reason_accuracy_min"],
                "passed": ra >= self.thresholds["reason_accuracy_min"]}

        core_passed = all(threshold_checks[k]["passed"] for k in threshold_checks)
        partial_checks = [drop_recall >= 0.70, false_drop_rate <= 0.25,
                         (ra is None or ra >= 0.50)]

        # eval_errors force fail
        if eval_errors:
            status = "fail"
            status_reason = f"Evaluation errors detected: {'; '.join(eval_errors)}"
        elif core_passed:
            status = "pass"
            status_reason = "All core hard thresholds met"
        elif all(partial_checks):
            status = "partial"
            status_reason = "Partial thresholds met but core thresholds not all satisfied"
        else:
            status = "fail"
            status_reason = "One or more hard thresholds breached"

        issues: list[str] = list(eval_errors)
        for cn, ch in threshold_checks.items():
            if not ch["passed"]:
                direction = "≥" if "precision" in cn or "recall" in cn or "accuracy" in cn else "≤"
                issues.append(f"{cn} = {ch['value']:.2%} (threshold: {direction} {ch['threshold']:.2%})")

        return ScoreResult(
            dimension=self.dimension, layer=self.layer,
            value=round(composite * 2, 1), max_value=2, normalized=composite,
            status=status,
            details=(f"Drop: precision={drop_precision:.0%}, recall={drop_recall:.0%}. "
                    f"FDR: {false_drop_rate:.0%} ({false_drop_count}/{expected_kept_count}). "
                    f"Reason acc: {ra if ra is not None else 'N/A'}. "
                    f"TP={tp_dropped}, FP={fp_dropped}, FN={fn_dropped}, TN={tn_kept}. "
                    f"Status: {status} — {status_reason}"),
            issues=issues,
            evidence={
                "drop_precision": round(drop_precision, 4),
                "drop_recall": round(drop_recall, 4),
                "keep_precision": round(keep_precision, 4),
                "keep_recall": round(keep_recall, 4),
                "false_drop_rate": round(false_drop_rate, 4),
                "false_drop_count": false_drop_count,
                "expected_kept_count": expected_kept_count,
                "tp_dropped": tp_dropped, "fp_dropped": fp_dropped,
                "fn_dropped": fn_dropped, "tn_kept": tn_kept,
                "total_input": total_input, "total_survived": len(actual_kept),
                "total_fixture_count": total_input,
                "per_stage_quality": per_stage_quality,
                "stage_attribution_coverage": round(stage_attribution_coverage, 4),
                "reason_accuracy": ra,
                "reason_accuracy_eligible": reason_results.get("reason_accuracy_eligible", ra is not None),
                "stage_accuracy": reason_results.get("stage_accuracy"),
                "reason_confusion_matrix": reason_results.get("confusion_matrix", []),
                "wrongly_dropped_indices": wrongly_dropped_indices,
                "wrongly_kept_indices": wrongly_kept_indices,
                "wrong_reason_indices": wrong_reason_indices,
                "composite_score": composite,
                "thresholds": self.thresholds, "threshold_checks": threshold_checks,
                "status_reason": status_reason, "eval_errors": eval_errors,
            },
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_doc(raw: dict[str, Any], fixture_index: int) -> SearchDocument:
        return SearchDocument(
            url=str(raw.get("url", "") or f"https://fixture.example/doc_{fixture_index}"),
            title=str(raw.get("title", "") or ""),
            raw_content=str(raw.get("content", "") or raw.get("snippet", "") or ""),
            source_type=str(raw.get("source_type", "web") or "web"),
            metadata={"fixture_index": fixture_index, "fixture_doc_id": f"fixture_{fixture_index}"},
        )

    @staticmethod
    def _find_fixture_index_by_url(doc: SearchDocument, raw_results: list[dict[str, Any]]) -> int | None:
        doc_url = (doc.url or "").lower()
        for idx, raw in enumerate(raw_results):
            if doc_url and doc_url == str(raw.get("url", "")).lower():
                return idx
        return None

    @staticmethod
    def _resolve_drop_stage(doc: SearchDocument, trace: list[Any]) -> str:
        """Resolve drop stage from explicit metadata or dropped_reason content.

        Priority:
        1. doc.metadata["dropped_stage"] or doc.metadata["drop_stage"]
        2. trace-based lookup (exact match against ALL stable identifiers)
        3. Heuristic from dropped_reason text
        4. "unknown" — do NOT attribute to first stage with drops
        """
        # 1. Explicit metadata
        for key in ("dropped_stage", "drop_stage"):
            val = doc.metadata.get(key)
            if val:
                return str(val)

        # 2. Trace-based lookup — match against ALL stable identifiers
        # Build candidate IDs from every available identifier on the doc
        candidate_ids: set[str] = set()
        for attr in ("url", "canonical_url"):
            val = getattr(doc, attr, None)
            if val:
                candidate_ids.add(str(val))
        for meta_key in ("fixture_doc_id", "fixture_index", "doc_id", "source_id"):
            val = doc.metadata.get(meta_key)
            if val is not None and str(val) != "":
                candidate_ids.add(str(val))

        for t in trace:
            dropped_ids = getattr(t, "dropped_doc_ids", None) or []
            # Normalise trace IDs to strings for exact set intersection
            trace_ids = {str(x) for x in dropped_ids}
            if candidate_ids & trace_ids:
                return getattr(t, "stage", "unknown")

        # 3. Heuristic from reason (more conservative — don't guess)
        reason = (doc.dropped_reason or "").lower()
        keyword_map = [
            ("exact", "exact_dedup"), ("near_dup", "near_dedup"), ("near", "near_dedup"),
            ("similar", "near_dedup"), ("relevance", "relevance"), ("irrelevant", "relevance"),
            ("quality", "quality"), ("spam", "quality"), ("low", "quality"),
            ("structure", "structure"), ("output_guard", "output_guard"), ("guard", "output_guard"),
            ("format", "format"), ("url", "canonicalize_url"), ("canonical", "canonicalize_url"),
            ("empty", "clean_text"), ("clean", "clean_text"), ("content", "clean_text"),
            ("truncated", "clean_text"),
        ]
        for kw, stage in keyword_map:
            if kw in reason:
                return stage

        # 4. Don't guess — use "unknown" (NEVER attribute to first stage with drops)
        return "unknown"

    def _reason_matches_stage(self, fixture_reason: str, stage_name: str) -> bool:
        reason_lower = fixture_reason.lower().strip()
        aliases = self.reason_aliases.get(reason_lower, set())
        if not aliases:
            return reason_lower in stage_name.lower() or stage_name.lower() in reason_lower
        return stage_name.lower() in aliases

    def _compute_reason_accuracy(self, expected_dropped: dict[int, str],
                                  actual_dropped: dict[int, str],
                                  actual_drop_stage: dict[int, str]) -> dict[str, Any]:
        common_indices = set(expected_dropped.keys()) & set(actual_dropped.keys())
        if not common_indices:
            return {"reason_accuracy": 0.0, "stage_accuracy": 0.0,
                    "reason_accuracy_eligible": True, "confusion_matrix": [], "wrong_reason_indices": []}

        reason_correct = 0; stage_correct = 0
        confusion: list[dict[str, Any]] = []; wrong_reason_idx: list[int] = []

        for idx in sorted(common_indices):
            expected_reason = expected_dropped[idx]
            actual_reason = actual_dropped[idx]
            actual_stage = actual_drop_stage.get(idx, "unknown")
            reason_ok = (self._reason_matches_stage(expected_reason, actual_stage)
                        or expected_reason.lower() in actual_reason.lower()
                        or actual_reason.lower() in expected_reason.lower())
            stage_ok = self._reason_matches_stage(expected_reason, actual_stage)
            if reason_ok: reason_correct += 1
            else: wrong_reason_idx.append(idx)
            if stage_ok: stage_correct += 1
            confusion.append({"fixture_index": idx, "expected_reason": expected_reason,
                             "actual_reason": actual_reason, "actual_stage": actual_stage,
                             "reason_correct": reason_ok, "stage_correct": stage_ok})

        n = len(common_indices)
        return {"reason_accuracy": round(reason_correct / n, 4), "stage_accuracy": round(stage_correct / n, 4),
                "reason_accuracy_eligible": True, "confusion_matrix": confusion,
                "wrong_reason_indices": wrong_reason_idx}
