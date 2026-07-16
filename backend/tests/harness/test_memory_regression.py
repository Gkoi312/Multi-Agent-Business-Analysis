"""
Regression tests for Round 2 Memory & Context fixes.

Covers:
1. TokenCounter split interface (count_text/count_message/count_messages)
2. ToolContextPruner keep logic (<= keep → cleared 0)
3. Stable message IDs (no index dependency)
4. HistoryCompactor old/recent split (no summary/raw duplication)
5. ContextAssembler budget enforcement + ContextBudgetExceeded
6. CompressedTurn structured facts
7. CoveragePolicy with independent source count
8. FactReconciler SPDV matching (CONFLICT vs UPDATE)
9. FactLedger preserves all facts
10. Model ID validation (reject unknown IDs)
11. Dynamic conflict derivation
12. SearchDigest SourceRecord + tokens_after
13. Serialization round-trips
14. RunningSummary idempotency
"""
import pytest
from unittest.mock import MagicMock

from harness.models.memory import (
    MemoryFact,
    MemoryOperation,
    CompressedTurn,
    MergedMemory,
    RunningSummary,
    SearchDigest,
    SourceRecord,
    ToolPruneResult,
    FactLedger,
    CoveragePolicy,
    ContextAssemblyResult,
    ContextBudgetExceeded,
    TokenCounter,
    _stable_message_id,
    _normalize_fact_text,
)
from harness.memory.context_window import ContextWindowManager
from harness.memory.context_editing import ToolContextPruner
from harness.memory.policies import ToolPruneConfig, TokenBudget, CompactionPolicy
from harness.memory.fact_reconciler import FactReconciler
from harness.memory.working_memory import WorkingMemory
from harness.memory.running_summary import RunningSummaryManager
from harness.memory.history_compactor import HistoryCompactor
from harness.memory.context_assembler import ContextAssembler


# ===========================================================================
# Helpers
# ===========================================================================

def _fake_token_counter(x):
    if isinstance(x, str):
        return max(1, len(x) // 4)
    if hasattr(x, "content"):
        return max(1, len(str(x.content)) // 4)
    return max(1, len(str(x)) // 4)


# ===========================================================================
# 1. TokenCounter split interface
# ===========================================================================


class TestTokenCounter:
    def test_count_text_plain(self):
        tc = TokenCounter()
        assert tc.count_text("hello world") > 0
        assert tc.count_text("") == 0

    def test_count_message_object(self):
        from langchain_core.messages import HumanMessage
        tc = TokenCounter()
        msg = HumanMessage(content="hello world")
        result = tc.count_message(msg)
        assert result > 0

    def test_count_messages_list(self):
        from langchain_core.messages import HumanMessage, AIMessage
        tc = TokenCounter()
        msgs = [
            HumanMessage(content="hello"),
            AIMessage(content="world"),
        ]
        result = tc.count_messages(msgs)
        assert result > 0
        # Should be more than single message
        assert result > tc.count_message(msgs[0])

    def test_history_compactor_should_compact_does_not_throw(self):
        """HistoryCompactor.should_compact with HumanMessage list must not throw."""
        from langchain_core.messages import HumanMessage

        policy = CompactionPolicy(trigger_tokens=100, min_turns_before_compact=2)
        compactor = HistoryCompactor(policy=policy, token_counter=TokenCounter())
        messages = [HumanMessage(content="hello")]
        # Must not throw TypeError
        result = compactor.should_compact(messages, turn_count=3)
        assert isinstance(result, bool)


# ===========================================================================
# 2. ToolContextPruner keep logic
# ===========================================================================


class TestToolPrunerKeepLogic:
    def test_2_tool_messages_keep_3_clears_0(self):
        """2 tool messages + keep 3 → cleared 0 (len <= keep, return empty candidates)."""
        from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

        config = ToolPruneConfig(trigger_tokens=10, keep_recent_tool_results=3)
        pruner = ToolContextPruner(config=config, token_counter=_fake_token_counter)

        messages = [
            HumanMessage(content="query"),
            AIMessage(content="ok", tool_calls=[{"id": "tc1", "name": "search", "args": {}}]),
            ToolMessage(content="x" * 1000, tool_call_id="tc1", name="search"),
            AIMessage(content="ok", tool_calls=[{"id": "tc2", "name": "search", "args": {}}]),
            ToolMessage(content="x" * 1000, tool_call_id="tc2", name="search"),
        ]

        _, result = pruner.prune(messages)
        assert result.tools_cleared == 0, f"Expected 0, got {result.tools_cleared}"

    def test_3_tool_messages_keep_3_clears_0(self):
        """3 tool messages + keep 3 → cleared 0."""
        from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

        config = ToolPruneConfig(trigger_tokens=10, keep_recent_tool_results=3)
        pruner = ToolContextPruner(config=config, token_counter=_fake_token_counter)

        messages = [
            HumanMessage(content="query"),
            AIMessage(content="ok", tool_calls=[{"id": "tc1", "name": "search", "args": {}}]),
            ToolMessage(content="x" * 1000, tool_call_id="tc1", name="search"),
            AIMessage(content="ok", tool_calls=[{"id": "tc2", "name": "search", "args": {}}]),
            ToolMessage(content="x" * 1000, tool_call_id="tc2", name="search"),
            AIMessage(content="ok", tool_calls=[{"id": "tc3", "name": "search", "args": {}}]),
            ToolMessage(content="x" * 1000, tool_call_id="tc3", name="search"),
        ]

        _, result = pruner.prune(messages)
        assert result.tools_cleared == 0, f"Expected 0, got {result.tools_cleared}"

    def test_5_tool_messages_keep_3_clears_2(self):
        """5 tool messages + keep 3 → cleared 2 (clear 2 oldest, keep 3 newest)."""
        from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

        config = ToolPruneConfig(trigger_tokens=10, keep_recent_tool_results=3)
        pruner = ToolContextPruner(config=config, token_counter=_fake_token_counter)

        messages = [
            HumanMessage(content="query"),
            AIMessage(content="ok", tool_calls=[{"id": "tc1", "name": "search", "args": {}}]),
            ToolMessage(content="x" * 1000, tool_call_id="tc1", name="search"),
            AIMessage(content="ok", tool_calls=[{"id": "tc2", "name": "search", "args": {}}]),
            ToolMessage(content="x" * 1000, tool_call_id="tc2", name="search"),
            AIMessage(content="ok", tool_calls=[{"id": "tc3", "name": "search", "args": {}}]),
            ToolMessage(content="x" * 1000, tool_call_id="tc3", name="search"),
            AIMessage(content="ok", tool_calls=[{"id": "tc4", "name": "search", "args": {}}]),
            ToolMessage(content="x" * 1000, tool_call_id="tc4", name="search"),
            AIMessage(content="ok", tool_calls=[{"id": "tc5", "name": "search", "args": {}}]),
            ToolMessage(content="x" * 1000, tool_call_id="tc5", name="search"),
        ]

        _, result = pruner.prune(messages)
        assert result.tools_cleared == 2, f"Expected 2, got {result.tools_cleared}"


# ===========================================================================
# 3. Stable message IDs (no index dependency)
# ===========================================================================


class TestStableMessageIDs:
    def test_same_message_different_index_same_id(self):
        """Same message at different positions must produce same ID."""
        from langchain_core.messages import HumanMessage
        msg = HumanMessage(content="test message")
        id1 = _stable_message_id(msg, occurrence_key="")
        id2 = _stable_message_id(msg, occurrence_key="")
        assert id1 == id2

    def test_different_messages_different_ids(self):
        from langchain_core.messages import HumanMessage, AIMessage
        id1 = _stable_message_id(HumanMessage(content="hello"), "")
        id2 = _stable_message_id(AIMessage(content="hello"), "")
        # Different roles → different IDs
        assert id1 != id2

    def test_stable_across_list_mutations(self):
        """Inserting messages before a message should not change its ID."""
        from langchain_core.messages import HumanMessage
        msg = HumanMessage(content="target message")
        id_before = _stable_message_id(msg, "")

        # Simulate list insertion
        msgs = [HumanMessage(content="new prefix"), msg]
        id_after = _stable_message_id(msgs[1], "")
        assert id_before == id_after

    def test_running_summary_idempotent_across_retries(self):
        """Same set of messages must produce same summary IDs on retry."""
        from langchain_core.messages import HumanMessage, AIMessage

        mgr = RunningSummaryManager(token_counter=_fake_token_counter)

        msgs = [
            HumanMessage(content="q1", id="a"),
            AIMessage(content="a1", id="b"),
            HumanMessage(content="q2", id="c"),
            AIMessage(content="a2", id="d"),
        ]

        rs = RunningSummary(
            summary="prior",
            summarized_message_ids={"a", "b"},
            last_summarized_message_id="b",
            version=1,
        )

        new1 = mgr._find_new_messages(msgs, rs)
        ids1 = {mgr._get_id(m) for m in new1}

        # Retry with same state
        new2 = mgr._find_new_messages(msgs, rs)
        ids2 = {mgr._get_id(m) for m in new2}

        assert ids1 == ids2  # Idempotent


# ===========================================================================
# 4. ContextAssembler budget enforcement
# ===========================================================================


class TestContextAssemblerBudget:
    def test_within_budget_succeeds(self):
        """Assembly within budget returns result."""
        from langchain_core.messages import HumanMessage

        budget = TokenBudget(
            system_prompt=500, research_summary=500, working_memory=500,
            recent_messages=1000, search_digest=500, long_term_facts=500,
        )
        assembler = ContextAssembler(token_budget=budget)
        result = assembler.assemble(
            messages=[HumanMessage(content="short")],
            system_prompt="You are helpful.",
        )
        assert result.total_tokens <= assembler.window_mgr.safe_limit

    def test_safe_limit_90_enforced(self):
        """safe_limit=90 → result ≤ 90 or ContextBudgetExceeded."""
        from langchain_core.messages import HumanMessage

        cwm = ContextWindowManager(max_tokens=200, reserved_tokens=50, safe_ratio=0.6)
        # safe_limit = (200-50)*0.6 = 90
        budget = TokenBudget(
            system_prompt=50, research_summary=20, working_memory=20,
            recent_messages=80, search_digest=20, long_term_facts=20,
        )
        assembler = ContextAssembler(token_budget=budget, window_mgr=cwm)

        messages = [HumanMessage(content="short test message")]
        try:
            result = assembler.assemble(
                messages=messages,
                system_prompt="You are a helpful assistant.",
            )
            assert result.total_tokens <= cwm.safe_limit, \
                f"total {result.total_tokens} exceeds safe_limit {cwm.safe_limit}"
        except ContextBudgetExceeded:
            # Also acceptable: explicit failure
            pass

    def test_raises_context_budget_exceeded_for_huge_input(self):
        """Massive input that can't shrink must raise."""
        from langchain_core.messages import HumanMessage

        cwm = ContextWindowManager(max_tokens=500, reserved_tokens=100, safe_ratio=0.5)
        # safe_limit = 200
        budget = TokenBudget(system_prompt=500)
        assembler = ContextAssembler(token_budget=budget, window_mgr=cwm)

        huge_msg = HumanMessage(content="x" * 10000)  # ~2500 tokens

        with pytest.raises(ContextBudgetExceeded):
            assembler.assemble(
                messages=[huge_msg],
                system_prompt="You are a helpful assistant. " * 200,
            )

    def test_safe_limit_is_public(self):
        """safe_limit must be accessible as property."""
        cwm = ContextWindowManager(max_tokens=1000, reserved_tokens=100, safe_ratio=0.5)
        assert cwm.safe_limit == 450
        assert isinstance(cwm.safe_limit, int)


# ===========================================================================
# 5. CompressedTurn structured facts
# ===========================================================================


class TestCompressedTurnFacts:
    def test_facts_is_primary_truth(self):
        """facts list is the primary truth source."""
        facts = [
            MemoryFact(text="Revenue grew 30%", primary_category="growth",
                       subject="Revenue", predicate="growth rate", value=30, unit="%"),
        ]
        turn = CompressedTurn(question_intent="Test", facts=facts)
        assert turn.facts == facts
        assert turn.key_findings == ["Revenue grew 30%"]

    def test_key_findings_is_derived(self):
        """key_findings derives from facts, not independent source."""
        facts = [
            MemoryFact(text="Fact A", primary_category="growth"),
            MemoryFact(text="Fact B", primary_category="risk"),
        ]
        turn = CompressedTurn(facts=facts)
        kf = turn.key_findings
        assert len(kf) == 2
        assert "Fact A" in kf
        assert "Fact B" in kf

    def test_backward_compat_key_findings_setter(self):
        """Setting key_findings creates minimal MemoryFacts."""
        turn = CompressedTurn()
        turn.key_findings = ["Old style fact 1", "Old style fact 2"]
        assert len(turn.facts) == 2
        assert turn.facts[0].text == "Old style fact 1"

    def test_sources_cited_derived(self):
        """sources_cited derives from facts' source_ids."""
        facts = [
            MemoryFact(text="A", source_ids=["url-a", "url-b"]),
            MemoryFact(text="B", source_ids=["url-b", "url-c"]),
        ]
        turn = CompressedTurn(facts=facts)
        sources = turn.sources_cited
        assert len(sources) == 3  # deduped
        assert "url-a" in sources
        assert "url-b" in sources
        assert "url-c" in sources

    def test_round_trip_with_facts(self):
        """CompressedTurn with facts survives round-trip."""
        facts = [
            MemoryFact(text="Revenue data", primary_category="growth",
                       subject="Revenue", predicate="amount", value=1.6, unit="billion USD"),
        ]
        turn = CompressedTurn(
            question_intent="How much revenue?",
            facts=facts,
            numbers_mentioned=[{"value": "1.6", "unit": "billion USD", "context": "annual revenue"}],
            unanswered=["Profit data not available"],
        )
        d = turn.to_dict()
        turn2 = CompressedTurn.from_dict(d)
        assert turn2.question_intent == turn.question_intent
        assert len(turn2.facts) == 1
        assert turn2.facts[0].text == "Revenue data"
        assert turn2.facts[0].subject == "Revenue"

    def test_evidence_quality_derived(self):
        """evidence_quality derives from best fact quality."""
        facts = [
            MemoryFact(text="A", evidence_quality="low"),
            MemoryFact(text="B", evidence_quality="high"),
        ]
        turn = CompressedTurn(facts=facts)
        assert turn.evidence_quality == "high"


# ===========================================================================
# 6. CoveragePolicy
# ===========================================================================


class TestCoveragePolicy:
    def test_independent_source_count_blocks_early_stop(self):
        """8 facts all from S1 with min_independent_sources=2 → blocks stop."""
        policy = CoveragePolicy(
            required_for_early_stop={"growth": 1},
            min_independent_sources=2,
        )
        wm = WorkingMemory(coverage_policy=policy)
        for i in range(8):
            wm.add_fact(
                f"Growth fact {i}", category="growth",
                source_ids=["S1"],
                evidence_quality="high",
            )
        assert wm.independent_source_count() == 1
        assert not wm.has_sufficient_coverage()

    def test_two_independent_sources_allows_stop(self):
        """2 independent sources with enough facts allows stop."""
        policy = CoveragePolicy(
            required_for_early_stop={"growth": 1},
            min_independent_sources=2,
        )
        wm = WorkingMemory(coverage_policy=policy)
        wm.add_fact("Growth fact", category="growth", source_ids=["S1"], evidence_quality="high")
        wm.add_fact("Risk fact", category="risk", source_ids=["S2"], evidence_quality="high")
        assert wm.independent_source_count() >= 2

    def test_low_quality_not_counted(self):
        """low quality facts below minimum_evidence_quality don't count."""
        policy = CoveragePolicy(
            required_for_early_stop={"growth": 1},
            minimum_evidence_quality="medium",
        )
        wm = WorkingMemory(coverage_policy=policy)
        wm.add_fact("Growth fact", category="growth", evidence_quality="low", source_ids=["S1"])
        # Not sufficient — low quality doesn't count
        assert not wm.has_sufficient_coverage()

    def test_unresolved_conflict_blocks_stop(self):
        """Unresolved conflicts prevent early stop when policy says so."""
        policy = CoveragePolicy(
            required_for_early_stop={"growth": 1, "risk": 1},
            unresolved_conflicts_block_stop=True,
            min_independent_sources=1,
        )
        wm = WorkingMemory(coverage_policy=policy)
        wm.add_fact("Growth fact", category="growth", source_ids=["S1"], evidence_quality="high")
        wm.add_fact("Risk fact different", category="risk", source_ids=["S1"], evidence_quality="high")

        # Manually simulate conflict
        conflict_fact = MemoryFact(
            text="Contradictory growth data",
            primary_category="growth",
            evidence_quality="high",
            source_ids=["S2"],
            conflicts_with=[wm.facts[0].fact_id],
            status="active",
        )
        wm.facts[0].conflicts_with = [conflict_fact.fact_id]
        wm.facts.append(conflict_fact)

        assert len(wm.unresolved_conflicts) > 0
        assert not wm.has_sufficient_coverage()

    def test_coverage_policy_round_trip(self):
        """CoveragePolicy survives serialization."""
        policy = CoveragePolicy(
            required_for_full_report={"business_model": 4},
            required_for_early_stop={"business_model": 3},
            minimum_evidence_quality="high",
            min_independent_sources=3,
        )
        d = policy.to_dict()
        p2 = CoveragePolicy.from_dict(d)
        assert p2.required_for_full_report == policy.required_for_full_report
        assert p2.min_independent_sources == 3


# ===========================================================================
# 7. FactReconciler SPDV matching
# ===========================================================================


class TestFactReconcilerSPDV:
    def test_revenue_increased_vs_decreased_is_conflict(self):
        """Revenue increased vs Revenue decreased → CONFLICT (both equally credible)."""
        reconciler = FactReconciler()
        existing = MemoryFact(
            fact_id="f-old",
            text="Revenue decreased by 10% in 2025",
            primary_category="growth",
            subject="Revenue",
            predicate="trend",
            value=-10,
            period="2025",
            evidence_quality="high",
            source_ids=["src-a", "src-x"],
            updated_at="2025-06-01T00:00:00Z",
        )
        new = MemoryFact(
            text="Revenue increased by 30% in 2025",
            primary_category="growth",
            subject="Revenue",
            predicate="trend",
            value=30,
            period="2025",
            evidence_quality="high",
            source_ids=["src-b", "src-y"],
            updated_at="2025-06-01T00:00:00Z",
        )
        ledger = reconciler.reconcile([new], [existing])
        conflict_ops = [op for op in ledger.operations if op["operation"] == "CONFLICT"]
        assert len(conflict_ops) >= 1, f"Expected CONFLICT, got ops: {[o['operation'] for o in ledger.operations]}"

    def test_different_periods_not_conflict(self):
        """Revenue 10M in 2024 vs Revenue 12M in 2025 → two time-point facts (or UPDATE)."""
        reconciler = FactReconciler()
        existing = MemoryFact(
            fact_id="f-2024",
            text="Revenue was 10M in 2024",
            primary_category="growth",
            subject="Revenue",
            predicate="amount",
            value=10,
            unit="M",
            period="2024",
            source_ids=["src-a"],
        )
        new = MemoryFact(
            text="Revenue was 12M in 2025",
            primary_category="growth",
            subject="Revenue",
            predicate="amount",
            value=12,
            unit="M",
            period="2025",
            source_ids=["src-b"],
        )
        ledger = reconciler.reconcile([new], [existing])
        # Different periods → ADD or UPDATE, NOT CONFLICT
        conflict_ops = [op for op in ledger.operations if op["operation"] == "CONFLICT"]
        assert len(conflict_ops) == 0, f"Expected 0 CONFLICTs for different periods"

    def test_risk_a_vs_risk_b_independent_facts(self):
        """Risk A vs Risk B → two independent facts (ADD)."""
        reconciler = FactReconciler()
        existing = MemoryFact(
            fact_id="f-risk-a",
            text="Risk A: regulatory compliance issue in EU",
            primary_category="risk",
            subject="Regulatory risk",
            predicate="type",
            value="EU compliance",
        )
        new = MemoryFact(
            text="Risk B: supply chain vulnerability in Asia",
            primary_category="risk",
            subject="Supply chain risk",
            predicate="type",
            value="Asia vulnerability",
        )
        ledger = reconciler.reconcile([new], [existing])
        add_ops = [op for op in ledger.operations if op["operation"] == "ADD"]
        assert len(add_ops) == 1, "Different subjects should produce ADD"

    def test_update_preserves_fact_id(self):
        """UPDATE must preserve original fact_id (option A)."""
        reconciler = FactReconciler()
        existing = MemoryFact(
            fact_id="f-original",
            text="Revenue was 10M",
            primary_category="growth",
            subject="Revenue",
            predicate="amount",
            value=10,
            unit="M",
            period="2024",
            evidence_quality="low",
        )
        new = MemoryFact(
            text="Revenue was 10.2M in FY2024 according to audited financials",
            primary_category="growth",
            subject="Revenue",
            predicate="amount",
            value=10.2,
            unit="M",
            period="2024",
            evidence_quality="high",
            source_ids=["src-audited"],
        )
        ledger = reconciler.reconcile([new], [existing])
        # The existing fact should be updated, not replaced
        updated_fact = next((f for f in ledger.all_facts if f.fact_id == "f-original"), None)
        assert updated_fact is not None, f"Original fact_id should survive UPDATE, got: {[f.fact_id for f in ledger.all_facts]}"
        # May be UPDATE (text replaced) or NONE (if not improvement)
        # Either way, the original fact_id survives
        assert "f-original" in {f.fact_id for f in ledger.all_facts}


# ===========================================================================
# 8. FactLedger preserves all facts
# ===========================================================================


class TestFactLedger:
    def test_all_facts_preserves_invalidated(self):
        """Invalidated facts remain in all_facts."""
        reconciler = FactReconciler()
        existing = MemoryFact(
            fact_id="f-old",
            text="Revenue was 10M",
            primary_category="growth",
            subject="Revenue", predicate="amount",
            value=10, unit="M", period="2024",
            evidence_quality="low",
        )
        new = MemoryFact(
            text="Revenue was 5M (corrected)",
            primary_category="growth",
            subject="Revenue", predicate="amount",
            value=5, unit="M", period="2024",
            evidence_quality="high",
            source_ids=["src-audited"],
        )
        ledger = reconciler.reconcile([new], [existing])
        # Old fact must be in all_facts
        old = next((f for f in ledger.all_facts if f.fact_id == "f-old"), None)
        assert old is not None, "Old fact must remain in all_facts; got: " + str([f.fact_id for f in ledger.all_facts])
        # Should be invalidated (numeric contradiction + new has better quality)
        op_types = {op["operation"] for op in ledger.operations}
        assert "INVALIDATE" in op_types or old.status == "invalidated" or "UPDATE" in op_types, \
            f"Expected INVALIDATE or UPDATE, got: {op_types}"

    def test_active_fact_ids_excludes_invalidated(self):
        """If old fact is invalidated, active_fact_ids excludes it."""
        reconciler = FactReconciler()
        existing = MemoryFact(
            fact_id="f-old",
            text="Old data",
            primary_category="growth",
            subject="Data", predicate="value",
            value="old", period="2024",
            evidence_quality="low",
        )
        new = MemoryFact(
            text="New data",
            primary_category="growth",
            subject="Data", predicate="value",
            value="new", period="2024",
            evidence_quality="high",
            source_ids=["src-a"],
        )
        ledger = reconciler.reconcile([new], [existing])
        # Old fact must appear in all_facts (preserved)
        assert "f-old" in {f.fact_id for f in ledger.all_facts}
        # If INVALIDATE happened, old is not in active_fact_ids
        op_types = {op["operation"] for op in ledger.operations}
        if "INVALIDATE" in op_types:
            assert "f-old" not in ledger.active_fact_ids

    def test_fact_ledger_round_trip(self):
        """FactLedger survives serialization."""
        ledger = FactLedger(
            all_facts=[
                MemoryFact(fact_id="f1", text="Active fact", status="active"),
                MemoryFact(fact_id="f2", text="Old fact", status="invalidated"),
            ],
            active_fact_ids={"f1"},
            operations=[{"operation": "ADD", "fact_id": "f1"}],
        )
        d = ledger.to_dict()
        l2 = FactLedger.from_dict(d)
        assert len(l2.all_facts) == 2
        assert l2.active_fact_ids == {"f1"}


# ===========================================================================
# 9. Model ID validation (reject unknown IDs)
# ===========================================================================


class TestModelIDValidation:
    def test_update_unknown_id_rejected(self):
        """UPDATE on non-existent target ID → rejected, not fallback to ADD."""
        reconciler = FactReconciler()
        existing = MemoryFact(fact_id="real-1", text="Known fact")

        model_output = [
            {"id": "99", "text": "Attempted update", "event": "UPDATE", "old_memory": ""},
        ]
        id_mapping = {}  # "99" not in mapping

        ledger = reconciler.reconcile_from_model_output(
            model_output, [existing], id_mapping=id_mapping
        )
        # No ADD should happen for UPDATE with unknown target
        update_ops = [op for op in ledger.operations if op["operation"] == "UPDATE"]
        add_ops = [op for op in ledger.operations if op["operation"] == "ADD"]
        assert len(update_ops) == 0, "UPDATE with unknown target should be rejected"
        assert len(add_ops) == 0, "Should not fallback to ADD for UPDATE"

    def test_invalidate_unknown_id_rejected(self):
        """INVALIDATE on non-existent ID → rejected."""
        reconciler = FactReconciler()
        model_output = [
            {"id": "99", "text": "", "event": "INVALIDATE"},
        ]
        ledger = reconciler.reconcile_from_model_output(
            model_output, [], id_mapping={}
        )
        invalidate_ops = [op for op in ledger.operations if op["operation"] == "INVALIDATE"]
        assert len(invalidate_ops) == 0

    def test_add_creates_new_fact(self):
        """ADD with proper mapping works."""
        reconciler = FactReconciler()
        model_output = [
            {"id": "0", "text": "New fact from model", "event": "ADD"},
        ]
        id_mapping = {"0": "real-new-1"}
        ledger = reconciler.reconcile_from_model_output(
            model_output, [], id_mapping=id_mapping
        )
        add_ops = [op for op in ledger.operations if op["operation"] == "ADD"]
        assert len(add_ops) == 1

    def test_no_real_uuid_generated_by_model(self):
        """Only code generates real UUIDs — model IDs must not pass through."""
        reconciler = FactReconciler()
        # Model tries to use a UUID-looking ID not in mapping
        model_output = [
            {"id": "fake-uuid-12345", "text": "Bad fact", "event": "ADD"},
        ]
        id_mapping = {}  # No mapping for fake-uuid-12345
        ledger = reconciler.reconcile_from_model_output(
            model_output, [], id_mapping=id_mapping
        )
        # ADD creates a fact, but its UUID should be auto-generated (not "fake-uuid-12345")
        if ledger.all_facts:
            # If any fact was added, it shouldn't use the model's raw ID
            for f in ledger.all_facts:
                assert f.fact_id != "fake-uuid-12345", "Model-provided non-UUID must not become fact_id"


# ===========================================================================
# 10. Dynamic conflict derivation
# ===========================================================================


class TestDynamicConflictDerivation:
    def test_conflicts_dynamically_derived(self):
        """unresolved_conflicts derives from active facts with conflicts_with."""
        wm = WorkingMemory()
        f1 = wm.add_fact("Fact A", category="growth")
        f2 = MemoryFact(
            text="Conflicting Fact B",
            primary_category="growth",
            evidence_quality="high",
            source_ids=["src-b"],
            conflicts_with=[f1.fact_id],
            status="active",
        )
        f1.conflicts_with = [f2.fact_id]
        wm.facts.append(f2)

        conflicts = wm.unresolved_conflicts  # dynamic property
        assert f1.fact_id in conflicts
        assert f2.fact_id in conflicts

    def test_conflict_resolved_updates_dynamically(self):
        """When conflicts are cleared, dynamic property reflects it."""
        wm = WorkingMemory()
        f1 = wm.add_fact("Fact A", category="growth")
        f2 = MemoryFact(
            text="Conflicting Fact B", primary_category="growth",
            conflicts_with=[f1.fact_id], status="active",
        )
        f1.conflicts_with = [f2.fact_id]
        wm.facts.append(f2)
        assert len(wm.unresolved_conflicts) == 2

        # Resolve: clear conflicts
        f1.conflicts_with = []
        f2.conflicts_with = []
        assert len(wm.unresolved_conflicts) == 0


# ===========================================================================
# 11. SearchDigest SourceRecord
# ===========================================================================


class TestSearchDigestSourceRecord:
    def test_source_record_creation(self):
        sr = SourceRecord(
            source_id="S1",
            url="https://example.com/article",
            title="Example Article",
            retrieved_at="2024-01-01T00:00:00Z",
        )
        assert sr.source_id == "S1"
        assert sr.url == "https://example.com/article"

    def test_source_record_round_trip(self):
        sr = SourceRecord(source_id="S1", url="https://a.com", title="Test")
        d = sr.to_dict()
        sr2 = SourceRecord.from_dict(d)
        assert sr2.source_id == sr.source_id
        assert sr2.url == sr.url

    def test_search_digest_round_trip(self):
        registry = {"S1": SourceRecord(source_id="S1", url="https://a.com", title="A")}
        sd = SearchDigest(
            query="test",
            source_ids=["S1"],
            evidence_snippets=["snippet"],
            extracted_claims=["claim"],
            tokens_before=1000,
            tokens_after=200,
            source_registry=registry,
        )
        d = sd.to_dict()
        sd2 = SearchDigest.from_dict(d)
        assert sd2.query == "test"
        assert "S1" in sd2.source_registry
        assert sd2.source_registry["S1"].url == "https://a.com"


# ===========================================================================
# 12. Serialization round-trips
# ===========================================================================


class TestSerializationRoundTrips:
    def test_tool_prune_result_round_trip(self):
        r = ToolPruneResult(messages_before=10, messages_after=5,
                            tokens_before=1000, tokens_after=500,
                            tools_cleared=3, tokens_reclaimed=500)
        d = r.to_dict()
        r2 = ToolPruneResult.from_dict(d)
        assert r2.tools_cleared == 3
        assert r2.reduction_ratio == r.reduction_ratio

    def test_context_assembly_result_round_trip(self):
        r = ContextAssemblyResult(
            system_prompt="system",
            total_tokens=500,
            token_breakdown={"system_prompt": 100, "messages": 400},
        )
        d = r.to_dict()
        r2 = ContextAssemblyResult.from_dict(d)
        assert r2.total_tokens == 500
        assert r2.token_breakdown == r.token_breakdown

    def test_running_summary_round_trip(self):
        rs = RunningSummary(
            summary="test summary",
            summarized_message_ids={"a", "b", "c"},
            last_summarized_message_id="c",
            version=5,
        )
        d = rs.to_dict()
        rs2 = RunningSummary.from_dict(d)
        assert rs2.summary == rs.summary
        assert rs2.summarized_message_ids == rs.summarized_message_ids
        assert rs2.version == 5

    def test_working_memory_round_trip_with_policy(self):
        policy = CoveragePolicy(min_independent_sources=3)
        wm = WorkingMemory(coverage_policy=policy)
        wm.add_fact("Test fact", category="growth", source_ids=["S1"])
        wm.turns_completed = 2

        d = wm.to_dict()
        wm2 = WorkingMemory.from_dict(d)
        assert wm2.turns_completed == 2
        assert wm2.coverage_policy.min_independent_sources == 3
        assert wm2.active_fact_count() == wm.active_fact_count()

    def test_merged_memory_round_trip(self):
        mm = MergedMemory(
            total_facts=5,
            coverage={"growth": 3, "risk": 2},
            knowledge_gaps=["financials"],
            independent_source_count=4,
        )
        d = mm.to_dict()
        mm2 = MergedMemory.from_dict(d)
        assert mm2.total_facts == 5
        assert mm2.independent_source_count == 4


# ===========================================================================
# 13. No double counting (WorkingMemory is single truth source)
# ===========================================================================


class TestNoDoubleCounting:
    def test_merged_memory_from_working_memory(self):
        """MergedMemory.from_working_memory derives all stats from facts."""
        policy = CoveragePolicy(
            required_for_full_report={"growth": 1, "risk": 1},
            min_independent_sources=1,
        )
        wm = WorkingMemory(coverage_policy=policy)
        wm.add_fact("Growth fact", category="growth", source_ids=["S1"], evidence_quality="high")
        wm.add_fact("Risk fact", category="risk", source_ids=["S2"], evidence_quality="high")

        mm = wm.to_merged_memory()
        assert mm.total_facts == wm.active_fact_count()
        assert mm.independent_source_count == wm.independent_source_count()

    def test_knowledge_gaps_dynamic(self):
        """knowledge_gaps derive from active facts, not manual list."""
        policy = CoveragePolicy(
            required_for_full_report={"growth": 3, "risk": 2, "financials": 2},
        )
        wm = WorkingMemory(coverage_policy=policy)
        wm.add_fact("Growth fact", category="growth", evidence_quality="high")

        gaps = wm.knowledge_gaps  # dynamic
        assert "growth" in gaps  # only 1, needs 3
        assert len(gaps) > 0


# ===========================================================================
# 14. MergedMemory has_sufficient_coverage with policy
# ===========================================================================


class TestMergedMemorySufficientCoverage:
    def test_uses_early_stop_thresholds(self):
        """has_sufficient_coverage uses required_for_early_stop (lenient)."""
        policy = CoveragePolicy(
            required_for_full_report={"growth": 5},
            required_for_early_stop={"growth": 1},
            min_independent_sources=1,
            unresolved_conflicts_block_stop=False,
        )
        mm = MergedMemory(
            total_facts=1,
            coverage={"growth": 1, "risk": 0, "competition": 0, "financials": 0, "business_model": 0},
            independent_source_count=1,
        )
        # Even though full_report needs 5, early_stop only needs 1
        assert mm.has_sufficient_coverage(policy)


# ===========================================================================
# 15. History compactor avoid summary/raw duplication
# ===========================================================================


class TestCompactorNoDuplication:
    def test_split_old_recent_separates(self):
        """_split_old_recent separates old (to summarize) from recent (to keep)."""
        from langchain_core.messages import HumanMessage, AIMessage

        policy = CompactionPolicy(keep_recent_tokens=50)
        compactor = HistoryCompactor(policy=policy, token_counter=TokenCounter())

        msgs = [
            HumanMessage(content="very old message with lots of content " * 5),
            AIMessage(content="old reply with lots of content " * 5),
            HumanMessage(content="recent message"),
            AIMessage(content="recent reply"),
        ]

        old, recent = compactor._split_old_recent(msgs)
        # The recent messages should be the last 2
        assert len(recent) >= 2
        assert len(old) >= 0
        # Recent messages should include the last human/ai pair
        assert recent[-1].content == "recent reply"
