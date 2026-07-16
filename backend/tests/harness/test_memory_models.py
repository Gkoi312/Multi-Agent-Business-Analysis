"""
Tests for harness.models.memory — MemoryFact, RunningSummary, SearchDigest,
CompressedTurn, MergedMemory, and serialization round-trips.
"""
import json
import pytest

from harness.models.memory import (
    MemoryFact,
    MemoryOperation,
    RunningSummary,
    SearchDigest,
    CompressedTurn,
    MergedMemory,
    ToolPruneResult,
    ContextAssemblyResult,
    CoveragePolicy,
    _extract_json,
    _normalize_fact_text,
    _stable_message_id,
    _now_iso,
)


# ===========================================================================
# MemoryFact tests
# ===========================================================================


class TestMemoryFact:
    def test_default_construction(self):
        """MemoryFact auto-generates fact_id, created_at, updated_at."""
        f = MemoryFact(text="Test fact")
        assert f.fact_id
        assert f.created_at
        assert f.updated_at
        assert f.status == "active"
        assert f.primary_category == "other"

    def test_to_dict_from_dict_roundtrip(self):
        """Round-trip preserves all fields including set/list types."""
        f = MemoryFact(
            text="Revenue grew 30% YoY",
            primary_category="growth",
            source_ids=["src1", "src2"],
            evidence_quality="high",
            confidence=0.95,
            turn_id=1,
            conflicts_with=["fact-2"],
            supersedes="fact-old-1",
        )
        d = f.to_dict()
        f2 = MemoryFact.from_dict(d)
        assert f2.text == f.text
        assert f2.primary_category == f.primary_category
        assert f2.source_ids == f.source_ids
        assert f2.evidence_quality == f.evidence_quality
        assert f2.confidence == 0.95
        assert f2.supersedes == "fact-old-1"

    def test_is_active(self):
        f = MemoryFact(text="Active", status="active")
        assert f.is_active
        f.status = "invalidated"
        assert not f.is_active

    def test_each_fact_has_unique_id(self):
        """Each fact gets its own stable ID."""
        f1 = MemoryFact(text="A")
        f2 = MemoryFact(text="B")
        assert f1.fact_id != f2.fact_id

    def test_explicit_fact_id_preserved(self):
        """Explicitly provided fact_id is kept."""
        f = MemoryFact(fact_id="my-custom-id", text="Custom")
        assert f.fact_id == "my-custom-id"

    def test_primary_category_one_only(self):
        """A fact has exactly one primary_category."""
        f = MemoryFact(text="Revenue and risk", primary_category="growth")
        assert f.primary_category == "growth"
        # Setting to other should replace
        f.primary_category = "risk"
        assert f.primary_category == "risk"


# ===========================================================================
# RunningSummary tests
# ===========================================================================


class TestRunningSummary:
    def test_default_construction(self):
        rs = RunningSummary()
        assert rs.summary == ""
        assert rs.summarized_message_ids == set()
        assert rs.last_summarized_message_id is None
        assert rs.version == 0

    def test_to_dict_from_dict_roundtrip(self):
        rs = RunningSummary(
            summary="User shared revenue data.",
            summarized_message_ids={"msg-1", "msg-2"},
            last_summarized_message_id="msg-2",
            version=3,
        )
        d = rs.to_dict()
        rs2 = RunningSummary.from_dict(d)
        assert rs2.summary == rs.summary
        assert rs2.summarized_message_ids == {"msg-1", "msg-2"}
        assert rs2.last_summarized_message_id == "msg-2"
        assert rs2.version == 3


# ===========================================================================
# SearchDigest tests
# ===========================================================================


class TestSearchDigest:
    def test_default_construction(self):
        sd = SearchDigest()
        assert sd.query == ""
        assert sd.source_ids == []
        assert sd.evidence_snippets == []

    def test_to_dict_from_dict_roundtrip(self):
        sd = SearchDigest(
            query="OpenAI revenue 2025",
            source_ids=["src-001", "src-002"],
            evidence_snippets=["OpenAI generated $1.6B in 2024."],
            extracted_claims=["OpenAI revenue is growing"],
            tokens_before=5000,
            tokens_after=800,
        )
        d = sd.to_dict()
        sd2 = SearchDigest.from_dict(d)
        assert sd2.query == sd.query
        assert sd2.source_ids == sd.source_ids
        assert sd2.tokens_before == 5000
        assert sd2.tokens_after == 800


# ===========================================================================
# CompressedTurn tests
# ===========================================================================


class TestCompressedTurn:
    def test_from_dict_defaults(self):
        ct = CompressedTurn.from_dict({})
        assert ct.question_intent == ""
        assert ct.key_findings == []
        assert ct.evidence_quality == "medium"

    def test_to_dict_from_dict_roundtrip(self):
        ct = CompressedTurn(
            question_intent="What is OpenAI's revenue model?",
            key_findings=["API and ChatGPT subscriptions", "Enterprise licensing"],
            evidence_quality="high",
            sources_cited=["https://example.com/1"],
            unanswered="",
        )
        d = ct.to_dict()
        ct2 = CompressedTurn.from_dict(d)
        assert ct2.question_intent == ct.question_intent
        assert ct2.key_findings == ct.key_findings
        assert ct2.evidence_quality == ct.evidence_quality

    def test_compression_error_field(self):
        ct = CompressedTurn(
            question_intent="Test",
            key_findings=["Fallback fact"],
            evidence_quality="low",
            compression_error="JSON parse failed",
        )
        assert ct.compression_error == "JSON parse failed"
        d = ct.to_dict()
        ct2 = CompressedTurn.from_dict(d)
        assert ct2.compression_error == "JSON parse failed"

    def test_format_includes_compression_error(self):
        ct = CompressedTurn(
            question_intent="Test",
            key_findings=[],
            evidence_quality="low",
            compression_error="LLM timeout",
        )
        formatted = ct.format()
        assert "Compression error" in formatted


# ===========================================================================
# MergedMemory tests
# ===========================================================================


class TestMergedMemory:
    def test_default_coverage_keys(self):
        mm = MergedMemory()
        assert "business_model" in mm.coverage
        assert "growth" in mm.coverage
        assert "risk" in mm.coverage
        assert "competition" in mm.coverage
        assert "financials" in mm.coverage
        assert "other" in mm.coverage

    def test_classify_fact_only_one_category(self):
        """Each fact receives exactly one primary category."""
        cat = MergedMemory._classify_fact("Risk increased as growth slowed to 5% YoY")
        assert cat == "risk"

    def test_unclassified_goes_to_other(self):
        """Facts that don't match any category go to 'other', not business_model."""
        cat = MergedMemory._classify_fact("The company was founded in 2010 by two engineers")
        assert cat == "other"

    def test_chinese_fact_classification(self):
        """Chinese facts should be classified correctly."""
        cat = MergedMemory._classify_fact("收入增长30%，利润率达40%")
        assert cat == "growth"

    def test_risk_chinese_fact(self):
        cat = MergedMemory._classify_fact("公司面临严重的监管风险和合规问题")
        assert cat == "risk"

    def test_from_working_memory_derives_correctly(self):
        """MergedMemory.from_working_memory derives all stats from active facts."""
        from harness.memory.working_memory import WorkingMemory as WM
        wm = WM()
        wm.add_fact("Revenue grew 30%", category="growth", source_ids=["S1"], evidence_quality="high")
        wm.add_fact("New product launch", category="business_model", source_ids=["S2"], evidence_quality="high")
        mm = wm.to_merged_memory()
        assert mm.total_facts >= 1
        assert mm.independent_source_count >= 1

    def test_has_sufficient_coverage(self):
        mm = MergedMemory(
            coverage={"business_model": 3, "growth": 3, "risk": 3, "competition": 2, "financials": 2},
            independent_source_count=3,
        )
        policy = CoveragePolicy(min_independent_sources=2, unresolved_conflicts_block_stop=False)
        assert mm.has_sufficient_coverage(policy)

    def test_insufficient_coverage_when_gaps(self):
        mm = MergedMemory(
            coverage={"business_model": 1, "growth": 1, "risk": 1, "competition": 0, "financials": 0},
        )
        policy = CoveragePolicy(unresolved_conflicts_block_stop=False)
        assert not mm.has_sufficient_coverage(policy)

    def test_unresolved_conflict_blocks_sufficient(self):
        mm = MergedMemory(
            coverage={"business_model": 3, "growth": 3, "risk": 3, "competition": 2, "financials": 2},
            unresolved_conflicts=["conflict-1"],
            independent_source_count=2,
        )
        policy = CoveragePolicy(min_independent_sources=1)
        assert not mm.has_sufficient_coverage(policy)

    def test_to_dict_from_dict_roundtrip(self):
        mm = MergedMemory(
            total_facts=5,
            coverage={"business_model": 2, "growth": 1, "risk": 0, "competition": 1, "financials": 1, "other": 0},
            knowledge_gaps=["risk", "financials"],
            risk_flags=["Some risk"],
            unresolved_questions=["Q1"],
            unresolved_conflicts=[],
            used_sources={"https://a.com/1"},
            independent_source_count=1,
        )
        d = mm.to_dict()
        mm2 = MergedMemory.from_dict(d)
        assert mm2.total_facts == 5
        assert mm2.knowledge_gaps == ["risk", "financials"]
        assert mm2.used_sources == {"https://a.com/1"}


# ===========================================================================
# JSON extraction helpers
# ===========================================================================


class TestJSONExtraction:
    def test_extract_json_plain(self):
        assert _extract_json('{"a": 1}') == {"a": 1}

    def test_extract_json_markdown_fence(self):
        result = _extract_json('```json\n{"a": 1}\n```')
        assert result == {"a": 1}

    def test_extract_json_in_text(self):
        result = _extract_json('prefix {"a": 1, "b": 2} suffix')
        assert result == {"a": 1, "b": 2}

    def test_extract_json_invalid_returns_empty(self):
        assert _extract_json("not json at all") == {}


# ===========================================================================
# Fact normalization
# ===========================================================================


class TestFactNormalization:
    def test_normalize_fact_text(self):
        norm = _normalize_fact_text("The company has strong revenue growth.")
        assert "the" not in norm
        assert "has" not in norm
        assert "company strong revenue growth" in norm


# ===========================================================================
# Stable ID helpers
# ===========================================================================


class TestStableIDs:
    def test_stable_message_id_no_index_dependency(self):
        """_stable_message_id no longer takes index — uses intrinsic properties."""
        from langchain_core.messages import HumanMessage
        msg = HumanMessage(content="test")
        # New signature: (msg, occurrence_key="")
        sid = _stable_message_id(msg, occurrence_key="")
        assert isinstance(sid, str)
        assert len(sid) > 0


# ===========================================================================
# ToolPruneResult tests
# ===========================================================================


class TestToolPruneResult:
    def test_reduction_ratio(self):
        r = ToolPruneResult(tokens_before=1000, tokens_after=600, tokens_reclaimed=400)
        assert r.reduction_ratio == 0.4

    def test_zero_before(self):
        r = ToolPruneResult(tokens_before=0)
        assert r.reduction_ratio == 0.0


# ===========================================================================
# MemoryOperation enum
# ===========================================================================


class TestMemoryOperation:
    def test_all_operations_present(self):
        assert MemoryOperation.ADD.value == "ADD"
        assert MemoryOperation.UPDATE.value == "UPDATE"
        assert MemoryOperation.INVALIDATE.value == "INVALIDATE"
        assert MemoryOperation.NONE.value == "NONE"
        assert MemoryOperation.CONFLICT.value == "CONFLICT"
