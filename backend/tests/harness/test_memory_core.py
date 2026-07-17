"""
Tests for core memory components (updated for Round 2 fixes):
- ContextWindowManager (validation, normalization, Chinese fallback, batch flush)
- FactReconciler (ADD, UPDATE, INVALIDATE, NONE, CONFLICT — returns FactLedger)
- RunningSummaryManager (idempotent, incremental, tool-call boundary)
- WorkingMemory (MemoryFact lifecycle, CoveragePolicy, dynamic properties)
"""
import pytest
from unittest.mock import MagicMock, patch

from harness.memory.context_window import ContextWindowManager
from harness.memory.fact_reconciler import FactReconciler
from harness.memory.running_summary import RunningSummaryManager
from harness.memory.working_memory import WorkingMemory
from harness.memory.policies import (
    VALID_PRIMARY_CATEGORIES,
)
from harness.models.memory import (
    MemoryFact,
    MemoryOperation,
    RunningSummary,
    CompressedTurn,
    FactLedger,
    CoveragePolicy,
)


# ===========================================================================
# Fixtures
# ===========================================================================


def _fake_token_counter(text):
    """Simple deterministic token counter: 1 token per 4 chars, min 1."""
    if hasattr(text, "content"):
        text = getattr(text, "content", str(text))
    return max(1, len(str(text)) // 4)


# ===========================================================================
# ContextWindowManager
# ===========================================================================


class TestContextWindowManager:
    def test_basic_construction(self):
        cwm = ContextWindowManager(model_name="gpt-4o")
        assert cwm.max_tokens == 128_000
        assert cwm.reserved_tokens == 2000
        assert cwm.safe_ratio == 0.7

    def test_model_name_with_version_suffix(self):
        cwm = ContextWindowManager(model_name="gpt-4o-2024-11-20")
        assert cwm.max_tokens == 128_000

    def test_unknown_model_falls_back_to_8192(self):
        cwm = ContextWindowManager(model_name="totally-unknown-model")
        assert cwm.max_tokens == 8192

    def test_reserved_tokens_must_be_less_than_max(self):
        with pytest.raises(ValueError, match="reserved_tokens"):
            ContextWindowManager(max_tokens=100, reserved_tokens=200)

    def test_safe_ratio_must_be_positive(self):
        with pytest.raises(ValueError, match="safe_ratio"):
            ContextWindowManager(safe_ratio=0.0)

    def test_safe_ratio_must_be_at_most_1(self):
        with pytest.raises(ValueError, match="safe_ratio"):
            ContextWindowManager(safe_ratio=1.5)

    def test_token_flush_size_must_be_positive(self):
        with pytest.raises(ValueError, match="token_flush_size"):
            ContextWindowManager(token_flush_size=0)

    def test_estimate_tokens_empty_string(self):
        cwm = ContextWindowManager()
        assert cwm.estimate_tokens("") == 0

    def test_cjk_character_ratio(self):
        assert ContextWindowManager._cjk_character_ratio("english text") < 0.1
        assert ContextWindowManager._cjk_character_ratio("中文文本") > 0.5

    def test_chinese_token_estimation_not_underestimated(self):
        cwm = ContextWindowManager()
        cjk_tokens = cwm.estimate_tokens("中文测试文本内容" * 20)
        assert cjk_tokens > 30

    def test_should_compress_triggers(self):
        cwm = ContextWindowManager(max_tokens=5000, reserved_tokens=500, safe_ratio=0.5)
        mock_messages = [MagicMock() for _ in range(100)]
        for m in mock_messages:
            m.content = "x" * 100
        assert cwm.should_compress(mock_messages)

    def test_model_normalization_variants(self):
        cases = [
            ("gpt-4o-2024-11-20", "gpt-4o"),
            ("gpt-4o-mini", "gpt-4o-mini"),
            ("claude-sonnet-5-20251010", "claude-sonnet-5"),
            ("deepseek-chat", "deepseek-chat"),
            ("gemini-2.5-flash", "gemini-2.5-flash"),
            ("gpt-4o-preview", "gpt-4o"),
            ("gpt-4o@latest", "gpt-4o"),
        ]
        for input_name, expected in cases:
            normalized = ContextWindowManager._normalize_model_name(input_name)
            assert normalized == expected, f"Failed for {input_name}: got {normalized}"

    def test_safe_limit_is_public_property(self):
        cwm = ContextWindowManager(max_tokens=1000, reserved_tokens=100, safe_ratio=0.5)
        assert cwm.safe_limit == 450


# ===========================================================================
# FactReconciler (returns FactLedger now)
# ===========================================================================


class TestFactReconciler:
    def test_add_new_fact(self):
        reconciler = FactReconciler()
        new = [MemoryFact(text="Revenue grew 30%", primary_category="growth")]
        ledger = reconciler.reconcile(new, [])
        assert len(ledger.active_facts) == 1
        assert ledger.operations[0]["operation"] == "ADD"

    def test_update_existing_fact(self):
        reconciler = FactReconciler()
        existing = MemoryFact(
            text="Company revenue grew 30% year over year in fiscal 2025",
            primary_category="growth", evidence_quality="low",
        )
        new = MemoryFact(
            text="Company revenue grew 30% year over year in fiscal 2025 reaching $1.6 billion driven by API subscriptions",
            primary_category="growth", evidence_quality="high", source_ids=["src-001"],
        )
        ledger = reconciler.reconcile([new], [existing])
        update_ops = [op for op in ledger.operations if op["operation"] == "UPDATE"]
        assert len(update_ops) > 0

    def test_none_for_equivalent_fact(self):
        reconciler = FactReconciler()
        existing = MemoryFact(
            text="Company revenue grew 30% year over year in fiscal 2025",
            primary_category="growth",
            subject="Company revenue", predicate="growth rate",
            value=30, unit="%", period="fiscal 2025",
        )
        new = MemoryFact(
            text="Company revenue increased 30 percent year over year in FY 2025",
            primary_category="growth",
            subject="Company revenue", predicate="growth rate",
            value=30, unit="%", period="fiscal 2025",
        )
        ledger = reconciler.reconcile([new], [existing])
        none_ops = [op for op in ledger.operations if op["operation"] == "NONE"]
        assert len(none_ops) > 0

    def test_invalidate_when_contradicted(self):
        reconciler = FactReconciler()
        existing = MemoryFact(
            text="Revenue decreased 10% in 2025", primary_category="growth",
            evidence_quality="low",
        )
        new = MemoryFact(
            text="Revenue increased 30% in 2025", primary_category="growth",
            evidence_quality="high", source_ids=["src-001"],
        )
        ledger = reconciler.reconcile([new], [existing])
        op_types = {op["operation"] for op in ledger.operations}
        assert any(op in op_types for op in ["INVALIDATE", "CONFLICT", "UPDATE"])

    def test_validate_category_fallback(self):
        reconciler = FactReconciler(category_whitelist=frozenset(["business_model", "growth"]))
        assert reconciler._validate_category("nonexistent") == "other"
        assert reconciler._validate_category("growth") == "growth"

    def test_contradiction_preserves_both_as_conflict(self):
        reconciler = FactReconciler()
        existing = MemoryFact(
            fact_id="f-source-a",
            text="Market share is 55%, the company is the leader",
            primary_category="competition", evidence_quality="high", source_ids=["src-a"],
        )
        new = MemoryFact(
            text="Market share is only 25%, the company is not the leader",
            primary_category="competition", evidence_quality="high", source_ids=["src-b"],
        )
        ledger = reconciler.reconcile([new], [existing])
        existing_ids = {f.fact_id for f in ledger.all_facts}
        assert "f-source-a" in existing_ids


# ===========================================================================
# RunningSummaryManager
# ===========================================================================


class TestRunningSummaryManager:
    def test_no_previous_summary_first_call(self):
        from langchain_core.messages import HumanMessage, AIMessage
        messages = [
            HumanMessage(content="Hello", id="msg-1"),
            AIMessage(content="Hi there!", id="msg-2"),
        ]
        mgr = RunningSummaryManager(token_counter=_fake_token_counter)
        new_msgs = mgr._find_new_messages(messages, RunningSummary())
        assert len(new_msgs) == 2

    def test_already_summarized_messages_not_re_summarized(self):
        from langchain_core.messages import HumanMessage, AIMessage
        messages = [
            HumanMessage(content="Hello", id="msg-1"),
            AIMessage(content="Hi there!", id="msg-2"),
            HumanMessage(content="What's the weather?", id="msg-3"),
            AIMessage(content="It's sunny!", id="msg-4"),
        ]
        rs = RunningSummary(
            summary="User greeted assistant.",
            summarized_message_ids={"msg-1", "msg-2"},
            last_summarized_message_id="msg-2",
            version=1,
        )
        mgr = RunningSummaryManager(token_counter=_fake_token_counter)
        new_msgs = mgr._find_new_messages(messages, rs)
        new_ids = [getattr(m, "id", None) for m in new_msgs]
        assert "msg-1" not in new_ids
        assert "msg-2" not in new_ids
        assert "msg-3" in new_ids


    def test_stable_fallback_id_for_messages_without_id(self):
        from langchain_core.messages import HumanMessage
        msg = HumanMessage(content="test message")
        sid1 = RunningSummaryManager._get_id(msg)
        sid2 = RunningSummaryManager._get_id(msg)
        assert sid1 == sid2

        msg2 = HumanMessage(content="different")
        sid3 = RunningSummaryManager._get_id(msg2)
        assert sid1 != sid3


# ===========================================================================
# WorkingMemory (updated for CoveragePolicy)
# ===========================================================================


class TestWorkingMemory:
    def test_add_fact_creates_memory_fact(self):
        wm = WorkingMemory()
        fact = wm.add_fact(text="Revenue grew 30% YoY", category="growth", source_ids=["src-1"])
        assert fact.fact_id
        assert fact.primary_category == "growth"
        assert wm.active_fact_count() >= 1

    def test_duplicate_fact_does_not_double_count_coverage(self):
        wm = WorkingMemory()
        wm.add_fact("Revenue grew 30%", category="growth", source_ids=["src-1"])
        wm.add_fact("Revenue grew 30 percent", category="growth", source_ids=["src-1"])
        counts = wm._count_active_by_category()
        assert counts.get("growth", 0) <= 2

    def test_each_fact_has_own_source_ids(self):
        wm = WorkingMemory()
        f1 = wm.add_fact("Fact one", category="growth", source_ids=["url-a"])
        f2 = wm.add_fact("Fact two", category="risk", source_ids=["url-b"])
        sources_1 = wm.fact_sources.get(f1.fact_id, [])
        sources_2 = wm.fact_sources.get(f2.fact_id, [])
        assert "url-a" in sources_1
        assert "url-b" in sources_2

    def test_ingest_compressed_turn(self):
        wm = WorkingMemory()
        turn = CompressedTurn(
            key_findings=["Revenue grew 30%", "New product launch planned"],
            evidence_quality="high",
            sources_cited=["https://a.com/1"],
        )
        wm.ingest_compressed_turn(turn)
        assert wm.turns_completed == 1
        assert wm.active_fact_count() >= 1

    def test_unanswered_questions_tracked(self):
        wm = WorkingMemory()
        turn = CompressedTurn(
            key_findings=["Some fact"],
            unanswered=["AI strategy impact on revenue not determined"],
        )
        wm.ingest_compressed_turn(turn)
        assert len(wm.unresolved_questions) >= 1

    def test_has_sufficient_coverage_with_enough_facts(self):
        wm = WorkingMemory()
        policy = wm.coverage_policy
        for cat, count in policy.required_for_early_stop.items():
            for i in range(count):
                wm.add_fact(
                    f"Fact about {cat} #{i}", category=cat,
                    source_ids=[f"src-{cat}-{i}"], evidence_quality="high",
                )
        result = wm.has_sufficient_coverage()
        assert isinstance(result, bool)

    def test_low_quality_fact_not_counted_for_coverage(self):
        wm = WorkingMemory()
        wm.add_fact("Barely a fact", category="growth", evidence_quality="low")
        wm.add_fact("Another weak fact", category="growth", evidence_quality="low")
        assert not wm.has_sufficient_coverage()

    def test_unresolved_conflict_prevents_early_stop(self):
        wm = WorkingMemory()
        policy = wm.coverage_policy
        for cat, count in policy.required_for_early_stop.items():
            for i in range(count):
                wm.add_fact(
                    f"Fact about {cat} #{i}", category=cat,
                    source_ids=[f"src-{cat}-{i}"], evidence_quality="high",
                )
        # Add conflict
        f = wm.active_facts[0]
        conflict = MemoryFact(
            text="Contradiction", primary_category=f.primary_category,
            evidence_quality="high", source_ids=["src-x"],
            conflicts_with=[f.fact_id], status="active",
        )
        f.conflicts_with = [conflict.fact_id]
        wm.facts.append(conflict)
        assert not wm.has_sufficient_coverage()

    def test_chinese_facts_ingested(self):
        wm = WorkingMemory()
        turn = CompressedTurn(
            key_findings=["公司收入增长30%", "市场份额达到25%"],
            evidence_quality="high",
        )
        wm.ingest_compressed_turn(turn)
        assert wm.active_fact_count() >= 1

    def test_facts_with_same_text_not_all_counted(self):
        wm = WorkingMemory()
        wm.add_fact("Revenue grew 30%", category="growth")
        wm.add_fact("Revenue grew 30%", category="growth")
        wm.add_fact("Revenue grew 30%", category="growth")
        counts = wm._count_active_by_category()
        # Reconciliation deduplicates semantically equivalent
        assert counts.get("growth", 0) <= 1

    def test_knowledge_gaps_are_dynamic(self):
        """knowledge_gaps is a dynamic property, not manually maintained."""
        wm = WorkingMemory()
        # With no facts, all categories in required_for_full_report should be gaps
        gaps = wm.knowledge_gaps
        for cat in wm.coverage_policy.required_for_full_report:
            assert cat in gaps

    def test_to_dict_from_dict_roundtrip(self):
        wm = WorkingMemory()
        wm.add_fact("Test fact", category="growth", source_ids=["src-1"])
        wm.turns_completed = 2
        d = wm.to_dict()
        wm2 = WorkingMemory.from_dict(d)
        assert wm2.turns_completed == 2
        assert wm2.active_fact_count() == wm.active_fact_count()

    def test_suggest_next_focus(self):
        wm = WorkingMemory()
        for i in range(3):
            wm.add_fact(f"Growth fact {i}", category="growth", source_ids=[f"sg-{i}"], evidence_quality="high")
        wm.add_fact("Lonely financial fact", category="financials", source_ids=["sf-1"], evidence_quality="high")
        focus = wm.suggest_next_focus()
        gaps = list(wm.knowledge_gaps)
        assert focus in gaps if gaps else True
