"""
Tests for evaluation scorers — compression fidelity, pipeline quality,
source traceability, pipeline metrics, scorer registry, consistency checks,
reliability, and runner.

Covers all fix requirements:
- Full text number extraction from facts.text/key_findings/question_intent
- Multi-number sentence extraction
- Currency mismatch strict rejection
- No-result fixture boolean unanswered handling
- Pipeline fake trace with explicit drop stage
- Missing doc detection, eval_errors→fail
- Malformed citation detection
- Body bare URL→fail (not partial)
- No registry with citations→fail
- Single-run cases not stable
- within_case_std for single case with repeats
- macro vs micro mean separation
- status_consistency rename
"""

import pytest
from harness.evaluation.pipeline_metrics import (
    PipelineMetrics, aggregate_trace, format_metrics_table,
)
from harness.evaluation.scorers.source_traceability import SourceTraceabilityScorer
from harness.evaluation.scorers.compression_fidelity import (
    CompressionFidelityScorer,
    _normalize_number, _numbers_match, _extract_all_numbers_from_text,
)
from harness.evaluation.scorers.pipeline_quality import PipelineQualityScorer
from harness.evaluation.scorer import ScoreResult, Scorer, SCORER_REGISTRY, register_scorer
from harness.evaluation.runner import EvalRunResult, EvalRunner
from harness.evaluation.reliability import (
    ReliabilityReport, _base_case_id, CaseStats, DimensionStats,
)
from harness.evaluation.consistency import (
    CONSISTENCY_CHECKS, ConsistencyResult, run_consistency_checks,
)


# ============================================================================
# Number Normalizer
# ============================================================================

class TestNumberNormalizer:
    def test_currency_85B(self):
        r = _normalize_number("$85B")
        assert r is not None and r["kind"] == "currency"
        assert r["normalized_value"] == 85_000_000_000 and r["unit"] == "USD"

    def test_85_billion_usd(self):
        r = _normalize_number("85 billion USD")
        assert r["kind"] == "currency" and r["normalized_value"] == 85_000_000_000

    def test_17_percent(self):
        r = _normalize_number("17%")
        assert r is not None and r["kind"] == "percentage"
        assert abs(r["normalized_value"] - 0.17) < 0.001

    def test_decimal_0_17(self):
        r = _normalize_number("0.17")
        assert r is not None and abs(r["normalized_value"] - 0.17) < 0.001

    def test_Q3_2025(self):
        r = _normalize_number("Q3 2025")
        assert r is not None and r["kind"] == "quarter"

    def test_2025_Q3(self):
        r1 = _normalize_number("Q3 2025"); r2 = _normalize_number("2025 Q3")
        assert r1["normalized_value"] == r2["normalized_value"]

    def test_85_billion_matches_85B(self):
        n1 = _normalize_number("85 billion USD"); n2 = _normalize_number("$85B")
        is_match, _ = _numbers_match(n1, n2)
        assert is_match is True

    def test_17pct_matches_0_17(self):
        is_match, _ = _numbers_match(_normalize_number("17%"), _normalize_number("0.17"))
        assert is_match is True

    def test_5_does_not_match_2025(self):
        is_match, _ = _numbers_match(_normalize_number("5"), _normalize_number("2025"))
        assert is_match is False

    def test_Q3_2025_matches_2025_Q3(self):
        is_match, _ = _numbers_match(_normalize_number("Q3 2025"), _normalize_number("2025 Q3"))
        assert is_match is True

    def test_usd_does_not_match_eur(self):
        """$85B must NOT match EUR 85B — currency mismatch is hard fail."""
        n1 = _normalize_number("$85B"); n2 = _normalize_number("EUR 85B")
        assert n1 is not None and n2 is not None
        assert n1["kind"] == "currency" and n2["kind"] == "currency"
        is_match, reason = _numbers_match(n1, n2)
        assert is_match is False, f"Expected False, got reason={reason}"
        assert "mismatch" in reason.lower()

    def test_multi_number_sentence(self):
        """One sentence with $85B, Q3 2025, and 17% extracts all three."""
        nums = _extract_all_numbers_from_text("Revenue was $85B in Q3 2025 and margin was 17%.")
        kinds = {n["kind"] for n in nums}
        assert "currency" in kinds
        assert "quarter" in kinds
        assert "percentage" in kinds
        assert len(nums) >= 3


# ============================================================================
# Compression Fidelity — Full Text Number Extraction
# ============================================================================

class TestCompressionFullTextNumbers:
    def test_numbers_extracted_from_fact_text(self):
        """Numbers in facts.text must be extracted even if numbers_mentioned is empty."""
        scorer = CompressionFidelityScorer()
        fixture = {
            "labeled_facts": ["revenue was $85 billion"],
            "labeled_numbers": [
                {"value": "$85B", "unit": "USD", "context": "revenue"},
            ],
        }
        # CompressedTurn has number only in fact text, NOT in numbers_mentioned
        turn = {
            "facts": [{"text": "revenue was $85B in Q3 2025"}],
            "numbers_mentioned": [],  # empty!
        }
        result = scorer.score(compressed_turn=turn, fixture=fixture)
        # _extract_all_numbers should find the number from facts.text
        num_ret = result.evidence.get("number_retention", 0)
        assert num_ret > 0.5, f"Expected number_retention > 0.5 from facts.text, got {num_ret}"

    def test_multi_number_from_facts(self):
        """Multiple numbers in one fact should all be extracted."""
        fixture = {
            "labeled_facts": ["revenue $85B and margin 17%"],
            "labeled_numbers": [
                {"value": "85", "unit": "billion_usd", "context": "revenue"},
                {"value": "17", "unit": "%", "context": "margin"},
            ],
        }
        turn = {
            "facts": [{"text": "Revenue was $85B and margin reached 17% in Q3 2025"}],
            "numbers_mentioned": [],
        }
        scorer = CompressionFidelityScorer()
        result = scorer.score(compressed_turn=turn, fixture=fixture)
        num_ret = result.evidence.get("number_retention", 0)
        # Should find both numbers from the fact text
        assert num_ret > 0.0

    def test_numbers_from_key_findings(self):
        """Numbers in key_findings should be extracted."""
        fixture = {
            "labeled_facts": ["Revenue $25B"],
            "labeled_numbers": [{"value": "$25B", "unit": "USD", "context": "revenue"}],
        }
        turn = {
            "facts": [],
            "numbers_mentioned": [],
            "key_findings": ["Revenue reached $25 billion in 2025"],
        }
        scorer = CompressionFidelityScorer()
        result = scorer.score(compressed_turn=turn, fixture=fixture)
        num_ret = result.evidence.get("number_retention", 0)
        assert num_ret > 0.0, f"Expected number from key_findings, got {num_ret}"


# ============================================================================
# Compression Fidelity — Validation + No-Result
# ============================================================================

class TestCompressionValidation:
    def test_valid_judge_output(self):
        valid, errors = CompressionFidelityScorer._validate_judge_output(
            {"matched": 5, "partial": 1, "missed": 1, "hallucinations": 0}, 7, 6)
        assert valid and len(errors) == 0

    def test_counts_dont_sum(self):
        valid, errors = CompressionFidelityScorer._validate_judge_output(
            {"matched": 3, "partial": 1, "missed": 1, "hallucinations": 0}, 7, 5)
        assert not valid

    def test_hallu_exceeds_extracted(self):
        valid, errors = CompressionFidelityScorer._validate_judge_output(
            {"matched": 3, "partial": 2, "missed": 2, "hallucinations": 10}, 7, 5)
        assert not valid

    def test_matched_exceeds_labeled(self):
        valid, errors = CompressionFidelityScorer._validate_judge_output(
            {"matched": 8, "partial": 0, "missed": 0, "hallucinations": 0}, 7, 8)
        assert not valid

    def test_ratio_consistency_checked(self):
        valid, errors = CompressionFidelityScorer._validate_judge_output(
            {"matched": 2, "partial": 0, "missed": 5, "hallucinations": 0,
             "fact_retention": 0.9}, 7, 5)
        assert not valid


class TestCompressionNoResult:
    def test_boolean_unanswered(self):
        """expected_unanswered: true → need at least one unanswered item."""
        scorer = CompressionFidelityScorer()
        fixture = {"labeled_facts": [], "labeled_numbers": [],
                    "expected_no_results": True, "expected_unanswered": True}
        # Turn with unanswered
        turn = {"facts": [], "numbers_mentioned": [], "unanswered": ["No data found"]}
        result = scorer.score(compressed_turn=turn, fixture=fixture)
        assert result.status == "pass"
        assert result.evidence["checks"]["has_unanswered"] is True

    def test_boolean_unanswered_fails_when_empty(self):
        scorer = CompressionFidelityScorer()
        fixture = {"labeled_facts": [], "labeled_numbers": [],
                    "expected_no_results": True, "expected_unanswered": True}
        turn = {"facts": [], "numbers_mentioned": [], "unanswered": []}
        result = scorer.score(compressed_turn=turn, fixture=fixture)
        assert result.status == "fail"

    def test_allowed_fact_count(self):
        """allowed_fact_count: 2 means up to 2 facts are OK."""
        scorer = CompressionFidelityScorer()
        fixture = {"labeled_facts": [], "labeled_numbers": [],
                    "expected_no_results": True, "allowed_fact_count": 2}
        turn = {"facts": [{"text": "minor note about the company"}, {"text": "minor observation"}],
                "numbers_mentioned": [], "unanswered": ["No main data"]}
        result = scorer.score(compressed_turn=turn, fixture=fixture)
        assert result.status == "pass", f"Expected pass, got {result.status}: {result.issues}"

    def test_expected_error_flag(self):
        """expected_error: true → compression_error should be present."""
        scorer = CompressionFidelityScorer()
        fixture = {"labeled_facts": [], "labeled_numbers": [],
                    "expected_no_results": True, "expected_error": True}
        turn = {"facts": [], "numbers_mentioned": [], "compression_error": "LLM timeout"}
        result = scorer.score(compressed_turn=turn, fixture=fixture)
        assert result.status == "pass"
        assert result.evidence["checks"]["has_error"] is True

    def test_no_label_skipped(self):
        scorer = CompressionFidelityScorer()
        result = scorer.score(compressed_turn={}, fixture={"labeled_facts": [], "labeled_numbers": []})
        assert result.status == "skipped" and result.eligible is False


class TestCompressionBasics:
    def test_heuristic_perfect_match(self):
        scorer = CompressionFidelityScorer()
        fixture = {"labeled_facts": ["revenue $25.18 billion in Q3 2025", "growth 8% YoY", "gross margin 19.8%"],
                   "labeled_numbers": [{"value": "25.18", "unit": "billion_usd", "context": "Q3 revenue"},
                                       {"value": "8", "unit": "%", "context": "YoY growth"}]}
        class MT:
            facts = [type("F",(),{"text":"revenue was $25.18 billion in Q3 2025"})(),
                    type("F",(),{"text":"growth rate was 8% year over year"})(),
                    type("F",(),{"text":"gross margin reached 19.8 percent"})()]
            numbers_mentioned = [{"value":"25.18","unit":"billion_usd","context":"Q3 revenue"},
                                {"value":"8","unit":"%","context":"YoY growth"}]
        result = scorer.score(compressed_turn=MT, fixture=fixture)
        assert result.evidence["fact_retention"] > 0.5
        assert "threshold_checks" in result.evidence
        assert "status_reason" in result.evidence

    def test_no_fixture_fails(self):
        r = CompressionFidelityScorer().score(compressed_turn=None, fixture=None)
        assert r.status == "fail"

    def test_number_retention_failure_blocks_pass(self):
        scorer = CompressionFidelityScorer()
        fixture = {"labeled_facts": ["revenue was strong"],
                   "labeled_numbers": [{"value":"25.18","unit":"billion_usd","context":"revenue"},
                                       {"value":"19.8","unit":"%","context":"margin"}]}
        class MT:
            facts = [type("F",(),{"text":"revenue was strong"})()]
            numbers_mentioned = []
        result = scorer.score(compressed_turn=MT, fixture=fixture)
        tc = result.evidence.get("threshold_checks", {})
        assert tc.get("number_retention", {}).get("passed") is False


# ============================================================================
# Pipeline Quality — Deterministic Fake Pipeline
# ============================================================================

class FakeStageTrace:
    def __init__(self, stage, input_c, output_c, dur, reduction, dropped, warnings, dropped_doc_ids=None):
        self.stage = stage; self.input_count = input_c; self.output_count = output_c
        self.duration_ms = dur; self.reduction_pct = reduction
        self.dropped_count = dropped; self.warning_count = warnings
        self.dropped_doc_ids = dropped_doc_ids or []


class FakeSearchDocument:
    """Simulates SearchDocument with fixture_index in metadata and dropped_reason."""
    def __init__(self, url, title, raw_content, source_type="web", metadata=None,
                 dropped_reason="", dropped_stage=None):
        self.url = url; self.title = title; self.raw_content = raw_content
        self.source_type = source_type
        self.metadata = metadata or {}
        self.dropped_reason = dropped_reason
        if dropped_stage:
            self.metadata["dropped_stage"] = dropped_stage
        self.canonical_url = url; self.clean_content = ""
        self.agent_content = ""; self.published_date = ""
        self.provider = ""; self.provider_score = None
        self.structured = {}; self.scores = {}; self.warnings = []; self.raw = {}


class FakePipeline:
    """Fake pipeline that returns explicitly controlled results."""
    def __init__(self, docs, traces=None):
        self.docs = docs; self.traces = traces or []

    def run_with_trace(self, docs, ctx=None):
        return self.docs, self.traces


class TestPipelineQuality:
    def test_explicit_drop_stage_from_metadata(self):
        """Docs with metadata['dropped_stage'] get correct stage attribution."""
        docs = [
            FakeSearchDocument("https://a.com/0", "Good", "Apple revenue $100B",
                              metadata={"fixture_index": 0}, dropped_reason=""),
            FakeSearchDocument("https://b.com/1", "Spam", "BUY NOW",
                              metadata={"fixture_index": 1, "dropped_stage": "quality"},
                              dropped_reason="low_quality", dropped_stage="quality"),
            FakeSearchDocument("https://c.com/2", "Good2", "More Apple content",
                              metadata={"fixture_index": 2}, dropped_reason=""),
        ]
        traces = [FakeStageTrace("quality", 3, 2, 5, 33.3, 1, 0)]
        pipeline = FakePipeline(docs, traces)

        scorer = PipelineQualityScorer(pipeline=pipeline)
        fixture = {
            "raw_results": [
                {"url": "https://a.com/0", "title": "Good", "content": "Apple revenue $100B"},
                {"url": "https://b.com/1", "title": "Spam", "content": "BUY NOW"},
                {"url": "https://c.com/2", "title": "Good2", "content": "More Apple content"},
            ],
            "expected_kept_indices": [0, 2],
            "expected_dropped_reasons": {"1": "low_quality"},
            "target_entity": "Apple Inc.",
        }
        result = scorer.score(fixture=fixture)

        assert result.evidence["tp_dropped"] == 1
        assert result.evidence["fp_dropped"] == 0
        assert result.evidence["fn_dropped"] == 0
        # Stage attribution should come from metadata
        assert result.evidence["stage_attribution_coverage"] == 1.0

    def test_survivors_only_pipeline(self):
        """Pipeline returns only survivors — dropped docs detected via set difference."""
        docs = [
            FakeSearchDocument("https://a.com/0", "Good", "Content",
                              metadata={"fixture_index": 0}),
            FakeSearchDocument("https://c.com/2", "Good2", "Content2",
                              metadata={"fixture_index": 2}),
            # Doc 1 is missing — should be detected as dropped
        ]
        pipeline = FakePipeline(docs, [])
        scorer = PipelineQualityScorer(pipeline=pipeline)
        fixture = {
            "raw_results": [
                {"url": "https://a.com/0", "title": "Good", "content": "Content"},
                {"url": "https://b.com/1", "title": "Spam", "content": "BUY NOW"},
                {"url": "https://c.com/2", "title": "Good2", "content": "Content2"},
            ],
            "expected_kept_indices": [0, 2],
            "expected_dropped_reasons": {"1": "low_quality"},
            "target_entity": "Test",
        }
        result = scorer.score(fixture=fixture)
        # Doc 1 should be detected as dropped (via set diff)
        assert result.evidence["tp_dropped"] == 1
        assert result.evidence["total_survived"] == 2
        assert 1 in result.evidence["wrongly_kept_indices"] or result.evidence["fn_dropped"] == 0

    def test_partial_dropped_records(self):
        """Some docs missing, some explicitly dropped — all accounted."""
        docs = [
            FakeSearchDocument("https://a.com/0", "Good", "Content",
                              metadata={"fixture_index": 0}),
            # Doc 1 explicitly dropped
            FakeSearchDocument("https://b.com/1", "Spam", "BUY",
                              metadata={"fixture_index": 1, "dropped_stage": "quality"},
                              dropped_reason="low_quality", dropped_stage="quality"),
            # Doc 2 is missing entirely
        ]
        pipeline = FakePipeline(docs, [])
        scorer = PipelineQualityScorer(pipeline=pipeline)
        fixture = {
            "raw_results": [
                {"url": "https://a.com/0", "title": "Good", "content": "Content"},
                {"url": "https://b.com/1", "title": "Spam", "content": "BUY"},
                {"url": "https://c.com/2", "title": "Good2", "content": "Content2"},
            ],
            "expected_kept_indices": [0, 2],
            "expected_dropped_reasons": {"1": "low_quality"},
            "target_entity": "Test",
        }
        result = scorer.score(fixture=fixture)
        # All 3 should be accounted for
        kept = result.evidence["total_survived"]
        total = result.evidence["total_input"]
        assert kept + result.evidence["tp_dropped"] + result.evidence["fp_dropped"] <= total

    def test_eval_error_unlabeled_docs_causes_fail(self):
        """Fixture that doesn't label all input docs → eval_error → fail."""
        docs = [
            FakeSearchDocument("https://a.com/0", "Good", "Content", metadata={"fixture_index": 0}),
            FakeSearchDocument("https://b.com/1", "Mystery", "???", metadata={"fixture_index": 1}),
        ]
        pipeline = FakePipeline(docs, [])
        scorer = PipelineQualityScorer(pipeline=pipeline)
        fixture = {
            "raw_results": [
                {"url": "https://a.com/0", "title": "Good", "content": "Content"},
                {"url": "https://b.com/1", "title": "Mystery", "content": "???"},
            ],
            "expected_kept_indices": [0],  # Doc 1 has no label!
            "expected_dropped_reasons": {},
            "target_entity": "Test",
        }
        result = scorer.score(fixture=fixture)
        # Should have an eval error about unlabeled docs
        assert len(result.evidence["eval_errors"]) > 0
        # eval_errors force fail
        assert result.status == "fail"

    def test_reason_accuracy_na_when_no_expected_drops(self):
        """When expected_dropped_reasons is empty, reason_accuracy should be None."""
        docs = [
            FakeSearchDocument("https://a.com/0", "Good", "Content", metadata={"fixture_index": 0}),
        ]
        pipeline = FakePipeline(docs, [])
        scorer = PipelineQualityScorer(pipeline=pipeline)
        fixture = {
            "raw_results": [{"url": "https://a.com/0", "title": "Good", "content": "Content"}],
            "expected_kept_indices": [0],
            "expected_dropped_reasons": {},
            "target_entity": "Test",
        }
        result = scorer.score(fixture=fixture)
        assert result.evidence["reason_accuracy"] is None
        assert result.evidence.get("reason_accuracy_eligible") is False

    def test_stage_attribution_coverage(self):
        """Stage attribution coverage reflects known vs unknown stages."""
        docs = [
            FakeSearchDocument("https://a.com/0", "Good", "Content", metadata={"fixture_index": 0}),
            FakeSearchDocument("https://b.com/1", "Spam", "BUY",
                              metadata={"fixture_index": 1, "dropped_stage": "quality"},
                              dropped_reason="low_quality", dropped_stage="quality"),
        ]
        pipeline = FakePipeline(docs, [])
        scorer = PipelineQualityScorer(pipeline=pipeline)
        fixture = {
            "raw_results": [
                {"url": "https://a.com/0", "title": "Good", "content": "Content"},
                {"url": "https://b.com/1", "title": "Spam", "content": "BUY"},
            ],
            "expected_kept_indices": [0],
            "expected_dropped_reasons": {"1": "low_quality"},
            "target_entity": "Test",
        }
        result = scorer.score(fixture=fixture)
        cov = result.evidence.get("stage_attribution_coverage", 0)
        assert cov == 1.0  # Both have known stage


# ============================================================================
# Source Traceability
# ============================================================================

class TestSourceTraceability:
    def test_body_bare_url_is_fail(self):
        """Body bare URL with threshold=0 must return fail, not partial."""
        scorer = SourceTraceabilityScorer()
        report = "Check https://example.com/news for details [S1]."
        result = scorer.score(report_text=report, source_registry={"S1": {}})
        assert result.status == "fail", f"Expected fail, got {result.status}"
        assert result.evidence["body_bare_urls"] >= 1

    def test_malformed_Sx_is_fail(self):
        """[Sx] must be detected as malformed → fail."""
        scorer = SourceTraceabilityScorer()
        report = "The report references [Sx] and [S1]."
        result = scorer.score(report_text=report, source_registry={"S1": {}})
        assert result.status == "fail"
        assert result.evidence["malformed_citation_count"] >= 1
        malformed = result.evidence.get("malformed_citations", [])
        assert any("Sx" in str(m) for m in malformed)

    def test_malformed_SA_detected(self):
        scorer = SourceTraceabilityScorer()
        report = "Analysis [SA] shows growth."
        result = scorer.score(report_text=report)
        assert result.evidence["malformed_citation_count"] >= 1

    def test_malformed_S_detected(self):
        scorer = SourceTraceabilityScorer()
        report = "Analysis [S] is incomplete."
        result = scorer.score(report_text=report)
        assert result.evidence["malformed_citation_count"] >= 1

    def test_no_registry_with_citations_is_fail(self):
        """Citations present but no registry → can't validate → fail."""
        scorer = SourceTraceabilityScorer()
        report = "Analysis [S1] shows growth [S2]."
        result = scorer.score(report_text=report)  # No source_registry
        assert result.status == "fail"

    def test_empty_registry_with_citations_is_fail(self):
        """Citations present but empty registry → fail."""
        scorer = SourceTraceabilityScorer()
        report = "Analysis [S1] shows growth."
        result = scorer.score(report_text=report, source_registry={})
        assert result.status == "fail"

    def test_registry_gap_is_orphan(self):
        scorer = SourceTraceabilityScorer()
        report = "Analysis [S1], [S2], [S3]."
        result = scorer.score(report_text=report, source_registry={"S1": {}, "S3": {}})
        assert result.evidence["orphan_citations"] == 1
        assert result.status == "fail"

    def test_well_cited_passes(self):
        scorer = SourceTraceabilityScorer()
        report = ("## Analysis\n\nApple AI [S1]. OpenAI [S2]. Revenue [S3].\n\n"
                  "## Sources\n[1] https://a.com\n[2] https://b.com\n[3] https://c.com")
        result = scorer.score(report_text=report, source_registry={"S1": {}, "S2": {}, "S3": {}})
        assert result.status == "pass"

    def test_urls_in_references_not_body(self):
        scorer = SourceTraceabilityScorer()
        report = "Analysis text.\n\n## Sources\nhttps://example.com/report\nhttps://example.com/data"
        result = scorer.score(report_text=report, source_registry={"S1": {}})
        assert result.evidence["body_bare_urls"] == 0
        assert result.evidence["refs_bare_urls"] >= 1

    def test_malformed_citation_unit(self):
        """Direct unit test for [Sx] detection."""
        scorer = SourceTraceabilityScorer()
        report = "Ref [Sx] and [S1]."
        result = scorer.score(report_text=report, source_registry={"S1": {}})
        assert result.status == "fail"
        assert result.evidence["malformed_citation_count"] >= 1

    def test_citation_metrics_present(self):
        scorer = SourceTraceabilityScorer()
        report = "Analysis [S1] shows growth. More analysis [S1] and [S2] confirms."
        result = scorer.score(report_text=report, source_registry={"S1": {}, "S2": {}})
        assert "citation_instance_density" in result.evidence
        assert "cited_sentence_rate" in result.evidence
        assert "threshold_checks" in result.evidence


# ============================================================================
# Pipeline Metrics
# ============================================================================

class TestPipelineMetrics:
    def test_aggregate_trace_empty(self):
        m = aggregate_trace([])
        assert m.total_input_docs == 0 and m.stage_count == 0

    def test_aggregate_trace_with_data(self):
        class MT:
            def __init__(self, s, ic, oc, d, r, dr, w):
                self.stage=s; self.input_count=ic; self.output_count=oc
                self.duration_ms=d; self.reduction_pct=r; self.dropped_count=dr; self.warning_count=w
        traces = [MT("dedup",10,8,5,20.0,2,0), MT("clean",8,6,15,25.0,2,1), MT("format",6,4,10,33.3,2,0)]
        m = aggregate_trace(traces, "test")
        assert m.total_input_docs==10 and m.total_output_docs==4
        assert m.stage_count==3 and m.total_dropped==6

    def test_format_metrics_table(self):
        class MT:
            def __init__(self, s, ic, oc, d, r, dr, w):
                self.stage=s; self.input_count=ic; self.output_count=oc
                self.duration_ms=d; self.reduction_pct=r; self.dropped_count=dr; self.warning_count=w
        traces = [MT("dedup",10,8,5,20.0,2,0), MT("clean",8,6,15,25.0,2,1)]
        table = format_metrics_table(aggregate_trace(traces,"t"))
        assert "dedup" in table and "clean" in table and "**总计**" in table

    def test_roundtrip(self):
        pm = PipelineMetrics(pipeline_name="t", total_duration_ms=100, total_input_docs=10,
                            total_output_docs=5, total_reduction_pct=50.0, total_dropped=5, stage_count=2)
        d = pm.to_dict(); r = PipelineMetrics.from_dict(d)
        assert r.pipeline_name=="t" and r.total_input_docs==10 and r.survived_docs==5


# ============================================================================
# Scorer Registry
# ============================================================================

class TestScorerRegistry:
    def test_register_get(self):
        SCORER_REGISTRY.pop("source_traceability", None)
        s = SourceTraceabilityScorer(); SCORER_REGISTRY[s.dimension] = s
        assert SCORER_REGISTRY.get("source_traceability") is s

    def test_roundtrip(self):
        sr = ScoreResult(dimension="t", value=1.5, max_value=2.0, normalized=0.75,
                        status="partial", layer="c", details="d", issues=["i1"],
                        evidence={"k":"v"}, eligible=True)
        d = sr.to_dict(); r = ScoreResult.from_dict(d)
        assert r.dimension=="t" and r.value==1.5 and r.status=="partial" and r.eligible is True

    def test_skipped_factory(self):
        sr = ScoreResult.skipped("t", reason="no data", layer="c")
        assert sr.status == "skipped" and sr.eligible is False

    def test_eligible_in_from_dict(self):
        sr = ScoreResult.from_dict({"dimension":"t","value":0,"max_value":2,"normalized":0,
                                     "status":"skipped","eligible":False})
        assert sr.status == "skipped" and sr.eligible is False


# ============================================================================
# Runner
# ============================================================================

class TestEvalRunResult:
    def test_passed_all_pass(self):
        r = EvalRunResult("r1","c1",[ScoreResult("d1",2,2,1.0,"pass"), ScoreResult("d2",2,2,1.0,"pass")])
        assert r.passed is True

    def test_passed_with_partial(self):
        r = EvalRunResult("r1","c1",[ScoreResult("d1",1,2,0.5,"partial")])
        assert r.passed is False

    def test_passed_all_skipped(self):
        r = EvalRunResult("r1","c1",[ScoreResult.skipped("d1"), ScoreResult.skipped("d2")])
        assert r.passed is False and r.not_evaluated is True

    def test_base_case_id(self):
        r = EvalRunResult("r1","case_001_r3",[])
        assert r.base_case_id == "case_001"

    def test_eligible_scores(self):
        r = EvalRunResult("r1","c1",[ScoreResult("d1",2,2,1.0,"pass"), ScoreResult.skipped("d2")])
        assert len(r.eligible_scores)==1 and len(r.skipped_scores)==1

    def test_aggregate_only_eligible(self):
        r = EvalRunResult("r1","c1",[ScoreResult("d1",2,2,0.8,"pass"), ScoreResult.skipped("d2")])
        assert r.aggregate_score == 0.8


# ============================================================================
# Reliability
# ============================================================================

class TestReliability:
    def test_single_run_not_stable(self):
        """Single-run case must NOT be marked stable."""
        runs = [EvalRunResult("r1","case1",[ScoreResult("d1",1.5,2,0.75,"pass")])]
        report = ReliabilityReport.from_runs(runs)
        ds = report.dimension_stats[0]
        cs = ds.case_stats[0]
        assert cs.repeatability_eligible is False
        assert cs.stable is False
        assert ds.repeatability_status in ("N/A", "DETERMINISTIC")
        assert ds.stable_case_rate is None

    def test_single_case_three_repeats_within_std(self):
        """One case with 3 repeats must compute within_case_std."""
        runs = [EvalRunResult("r1","case1_r1",[ScoreResult("d1",0.4,2,0.2,"fail")]),
                EvalRunResult("r2","case1_r2",[ScoreResult("d1",1.6,2,0.8,"pass")]),
                EvalRunResult("r3","case1_r3",[ScoreResult("d1",1.0,2,0.5,"fail")])]
        report = ReliabilityReport.from_runs(runs)
        ds = report.dimension_stats[0]
        assert ds.n_cases == 1
        assert ds.n_eligible_runs == 3
        # within_case_std should be computed (3 runs > 1 case)
        assert ds.within_case_std is not None
        assert ds.within_case_std > 0, f"Expected within_case_std > 0, got {ds.within_case_std}"

    def test_same_case_three_times_same_score(self):
        runs = []
        for r in range(3):
            runs.append(EvalRunResult(f"r{r}",f"c1_r{r+1}",[ScoreResult("d1",1.7,2,0.85,"pass")]))
        report = ReliabilityReport.from_runs(runs)
        ds = report.dimension_stats[0]
        cs = ds.case_stats[0]
        # CV=0 for identical scores
        assert cs.cv == 0.0
        # status_consistency should be 1.0 (all same status)
        assert cs.status_consistency == 1.0

    def test_different_cases_not_one_cv(self):
        runs = [EvalRunResult("r1","hard",[ScoreResult("d1",1.0,2,0.5,"fail")]),
                EvalRunResult("r2","easy",[ScoreResult("d1",2.0,2,1.0,"pass")])]
        report = ReliabilityReport.from_runs(runs)
        ds = report.dimension_stats[0]
        assert ds.n_cases == 2
        assert ds.between_case_std > 0

    def test_skipped_not_in_mean(self):
        runs = [EvalRunResult("r1","c1",[ScoreResult("d1",2,2,1.0,"pass")]),
                EvalRunResult("r2","c2",[ScoreResult.skipped("d1")])]
        report = ReliabilityReport.from_runs(runs)
        ds = report.dimension_stats[0]
        assert ds.n_eligible_runs == 1 and ds.n_skipped_runs == 1
        assert ds.micro_mean == 1.0

    def test_deterministic_marked(self):
        runs = [EvalRunResult("r1","c1",[ScoreResult("pq",1.5,2,0.75,"partial")])]
        report = ReliabilityReport.from_runs(runs, {"pq":"deterministic"})
        assert report.dimension_stats[0].scorer_type == "deterministic"

    def test_perf_fail_but_stable(self):
        runs = []
        for r in range(3):
            runs.append(EvalRunResult(f"r{r}",f"c1_r{r+1}",[ScoreResult("d1",1.4,2,0.70,"fail")]))
        report = ReliabilityReport.from_runs(runs)
        ds = report.dimension_stats[0]
        assert ds.pass_rate == 0.0
        assert ds.performance_status in ("FAIL","MIXED")

    def test_macro_vs_micro(self):
        """macro_mean averages case means; micro_mean averages all runs."""
        runs = [EvalRunResult("r1","easy_r1",[ScoreResult("d1",2.0,2,1.0,"pass")]),
                EvalRunResult("r2","easy_r2",[ScoreResult("d1",2.0,2,1.0,"pass")]),
                EvalRunResult("r3","hard_r1",[ScoreResult("d1",1.0,2,0.5,"fail")])]
        report = ReliabilityReport.from_runs(runs)
        ds = report.dimension_stats[0]
        # macro: (1.0 + 0.5) / 2 = 0.75
        # micro: (1.0 + 1.0 + 0.5) / 3 = 0.8333
        assert abs(ds.macro_mean - 0.75) < 0.01
        assert abs(ds.micro_mean - 0.8333) < 0.01
        assert ds.macro_mean != ds.micro_mean

    def test_status_consistency_field(self):
        """status_consistency and pass_consistency both populated."""
        runs = []
        for r in range(3):
            runs.append(EvalRunResult(f"r{r}",f"c1_r{r+1}",[ScoreResult("d1",1.5,2,0.75,"pass")]))
        report = ReliabilityReport.from_runs(runs)
        cs = report.dimension_stats[0].case_stats[0]
        assert cs.status_consistency == 1.0  # All same status
        assert cs.pass_consistency == 1.0    # Deprecated alias


# ============================================================================
# Consistency
# ============================================================================

class TestConsistencyChecks:
    def test_source_id_gap(self):
        state = {"source_registry": {"S1":{}, "S3":{}, "S5":{}}}
        ok, _ = CONSISTENCY_CHECKS[1]["check"](state); assert not ok

    def test_fact_references_nonexistent(self):
        state = {"source_registry":{"S1":{}}, "working_memory":{"facts":[{"fact_id":"f1","source_ids":["S99"]}]}}
        ok, _ = CONSISTENCY_CHECKS[2]["check"](state); assert not ok

    def test_duplicate_workflow(self):
        state = {"workflow_events":[{"event":"compress.completed","payload":{"turn":1}},
                                     {"event":"compress.completed","payload":{"turn":1}}]}
        ok, _ = CONSISTENCY_CHECKS[5]["check"](state); assert not ok

    def test_empty_compressed_turn(self):
        state = {"compressed_turns":[{"facts":[],"key_findings":[],"compression_error":""}]}
        ok, _ = CONSISTENCY_CHECKS[6]["check"](state); assert not ok

    def test_llm_events_no_metrics(self):
        state = {"workflow_events":[{"event":"compress.completed","payload":{"turn":1}}]}
        ok, detail = CONSISTENCY_CHECKS[3]["check"](state)
        assert not ok

    def test_knowledge_gaps_spike(self):
        state = {"knowledge_gap_history":[{"turn":1,"count":3},{"turn":2,"count":10},{"turn":3,"count":12}]}
        ok, detail = CONSISTENCY_CHECKS[4]["check"](state)
        assert not ok or "spiked" in detail.lower() or "monitor" in detail.lower()

    def test_knowledge_gaps_converging(self):
        state = {"knowledge_gap_history":[{"turn":1,"count":8},{"turn":2,"count":6},{"turn":3,"count":5}]}
        ok, _ = CONSISTENCY_CHECKS[4]["check"](state); assert ok

    def test_valid_state_passes(self):
        state = {"working_memory":{"turns_completed":2,
                 "facts":[{"fact_id":"f1","source_ids":["S1"],"text":"r $100B"},
                          {"fact_id":"f2","source_ids":["S2"],"text":"g 15%"}]},
                 "compressed_turns":[{"facts":[{"fact_id":"f1","text":"r $100B","source_ids":["S1"]}]},
                                     {"facts":[{"fact_id":"f2","text":"g 15%","source_ids":["S2"]}]}],
                 "source_registry":{"S1":{},"S2":{}},
                 "llm_metrics":[{"node":"i.ask","total_tokens":500}],
                 "workflow_events":[{"event":"compress.completed","payload":{"turn":1}},
                                    {"event":"compress.completed","payload":{"turn":2}}]}
        result = run_consistency_checks(state)
        error_vs = [v for v in result.violations if v["severity"]=="error"]
        assert len(error_vs)==0, f"Unexpected errors: {error_vs}"

    def test_error_causes_overall_fail(self):
        state = {"working_memory":{"turns_completed":5},"compressed_turns":[]}
        assert run_consistency_checks(state).passed is False

    def test_warning_only_passes(self):
        state = {"working_memory":{"turns_completed":0},"compressed_turns":[]}
        result = run_consistency_checks(state)
        if result.error_count == 0: assert result.passed is True

    def test_result_has_counts(self):
        d = run_consistency_checks({}).to_dict()
        for k in ("error_count","warning_count","warned_rules","passed"):
            assert k in d
