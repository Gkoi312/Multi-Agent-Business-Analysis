"""
Tests for harness.memory.nodes — generic LangGraph node factories for the
compress / update_memory / compact_history / continue-router segment of a
memory-driven research loop.

These are exercised standalone (no LangGraph StateGraph needed) since the
factories just return plain ``dict -> dict`` / ``dict -> str`` callables.
"""
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from harness.memory.nodes import (
    build_working_memory_from_state,
    format_working_memory_context,
    make_compact_history_node,
    make_compress_node,
    make_should_continue_router,
    make_update_memory_node,
)
from harness.memory.policies import MemoryDomainConfig
from harness.memory.working_memory import WorkingMemory
from harness.models.memory import CompressedTurn, CoveragePolicy, MemoryFact


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def domain_config():
    return MemoryDomainConfig(
        categories=["business_model", "growth", "risk"],
        coverage_policy=CoveragePolicy(
            required_for_full_report={"business_model": 1, "growth": 1, "risk": 1},
            required_for_early_stop={"business_model": 1},
            min_independent_sources=1,
        ),
    )


def _fact(category="business_model", text="Revenue is $1B"):
    return MemoryFact(
        fact_id="f1", text=text, primary_category=category,
        source_ids=["S1"], evidence_quality="high",
    )


def _compressed_turn_dict(facts=None):
    turn = CompressedTurn(
        question_intent="What is the revenue model?",
        facts=facts if facts is not None else [_fact()],
    )
    return turn.to_dict()


# ===========================================================================
# make_compress_node
# ===========================================================================


class TestMakeCompressNode:
    def test_appends_compressed_turn(self):
        mock_compressor = MagicMock()
        compressed = CompressedTurn(question_intent="intent", facts=[_fact()])
        mock_compressor.compress_completed_turn.return_value = compressed

        node = make_compress_node(mock_compressor)
        state = {
            "messages": [HumanMessage(content="Q1"), AIMessage(content="A1")],
            "context": ["some search result"],
            "turn_count": 1,
            "compressed_turns": [],
        }
        result = node(state)

        assert len(result["compressed_turns"]) == 1
        assert result["compressed_turns"][0]["question_intent"] == "intent"
        assert result["workflow_events"][0]["event"] == "compress.completed"
        mock_compressor.compress_completed_turn.assert_called_once()

    def test_prefers_current_turn_registry_over_full_registry(self):
        mock_compressor = MagicMock()
        mock_compressor.compress_completed_turn.return_value = CompressedTurn()

        node = make_compress_node(mock_compressor)
        state = {
            "messages": [HumanMessage(content="Q1"), AIMessage(content="A1")],
            "context": [],
            "turn_count": 1,
            "compressed_turns": [],
            "_current_turn_registry": {"S2": {"url": "https://x"}},
            "source_registry": {"S1": {"url": "https://old"}},
        }
        node(state)

        _, kwargs = mock_compressor.compress_completed_turn.call_args
        assert kwargs["source_registry"] == {"S2": {"url": "https://x"}}

    def test_exception_is_caught_and_reported_as_event(self):
        mock_compressor = MagicMock()
        mock_compressor.compress_completed_turn.side_effect = RuntimeError("boom")

        node = make_compress_node(mock_compressor)
        result = node({"messages": [HumanMessage(content="Q"), AIMessage(content="A")]})

        assert result["workflow_events"][0]["event"] == "compress.failed"
        assert "boom" in result["workflow_events"][0]["payload"]["error"]


# ===========================================================================
# make_update_memory_node
# ===========================================================================


class TestMakeUpdateMemoryNode:
    def test_ingests_only_unprocessed_turns(self, domain_config):
        node = make_update_memory_node(domain_config)
        state = {
            "working_memory": {},
            "compressed_turns": [_compressed_turn_dict(), _compressed_turn_dict()],
            "turn_count": 2,
        }
        result = node(state)

        wm = WorkingMemory.from_dict(result["working_memory"])
        assert wm.turns_completed == 2
        assert wm.active_fact_count() >= 1
        assert result["workflow_events"][0]["event"] == "memory.updated"

    def test_is_idempotent_across_repeated_calls_with_same_history(self, domain_config):
        node = make_update_memory_node(domain_config)
        state = {"working_memory": {}, "compressed_turns": [_compressed_turn_dict()], "turn_count": 1}

        first = node(state)
        state["working_memory"] = first["working_memory"]
        second = node(state)  # no new turns to ingest

        wm2 = WorkingMemory.from_dict(second["working_memory"])
        assert wm2.turns_completed == 1


# ===========================================================================
# make_compact_history_node
# ===========================================================================


class TestMakeCompactHistoryNode:
    def test_skips_when_below_threshold(self):
        mock_compressor = MagicMock()
        mock_compressor.should_compact_history.return_value = False

        node = make_compact_history_node(mock_compressor)
        result = node({"messages": [], "turn_count": 1, "compressed_turns": []})

        assert result["workflow_events"][0]["event"] == "compact_history.skipped"
        mock_compressor.compact_history.assert_not_called()

    def test_compacts_when_over_threshold(self):
        mock_compressor = MagicMock()
        mock_compressor.should_compact_history.return_value = True
        updated_rs = MagicMock()
        updated_rs.version = 2
        updated_rs.to_dict.return_value = {"version": 2}
        usage = {"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160}
        mock_compressor.compact_history.return_value = ([], updated_rs, usage)

        node = make_compact_history_node(mock_compressor)
        result = node({"messages": [], "turn_count": 5, "compressed_turns": []})

        assert result["running_summary"] == {"version": 2}
        assert result["workflow_events"][0]["payload"]["version"] == 2
        assert result["llm_metrics"][0]["node"] == "interview.compact_history"
        assert result["llm_metrics"][0]["prompt_tokens"] == 120
        assert result["llm_metrics"][0]["completion_tokens"] == 40
        assert result["llm_metrics"][0]["total_tokens"] == 160


# ===========================================================================
# make_should_continue_router
# ===========================================================================


class TestMakeShouldContinueRouter:
    def test_stops_at_turn_budget(self):
        router = make_should_continue_router(continue_node="ask", stop_node="save")
        assert router({"max_num_turns": 2, "turn_count": 2}) == "save"

    def test_continues_when_under_budget_and_no_working_memory(self):
        router = make_should_continue_router(continue_node="ask", stop_node="save")
        assert router({"max_num_turns": 3, "turn_count": 1}) == "ask"

    def test_stops_early_on_sufficient_coverage(self, domain_config):
        wm = WorkingMemory(coverage_policy=domain_config.coverage_policy, domain_config=domain_config)
        wm.ingest_compressed_turn(CompressedTurn(facts=[_fact("business_model")]))

        router = make_should_continue_router(continue_node="ask", stop_node="save")
        result = router({"max_num_turns": 5, "turn_count": 1, "working_memory": wm.to_dict()})

        assert result == "save"


# ===========================================================================
# Shared helpers
# ===========================================================================


class TestSharedHelpers:
    def test_build_working_memory_from_state_injects_domain_config(self, domain_config):
        wm = build_working_memory_from_state({}, domain_config)
        assert wm.domain_config is domain_config

    def test_format_working_memory_context_empty_state(self):
        assert format_working_memory_context({}) == ""

    def test_format_working_memory_context_includes_compressed_rounds(self):
        state = {"compressed_turns": [_compressed_turn_dict()]}
        text = format_working_memory_context(state)
        assert "Compressed prior rounds" in text
