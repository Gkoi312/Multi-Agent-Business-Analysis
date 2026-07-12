"""
Tests for consistency checks — pure code, no LLM needed.

Verifies all built-in consistency rules against well-formed and malformed
state dicts.
"""

import pytest
from harness.evaluation.consistency import (
    CONSISTENCY_CHECKS,
    ConsistencyResult,
    run_consistency_checks,
)


class TestTurnsCompletedMatchesCompressed:
    def test_matching(self):
        state = {
            "working_memory": {"turns_completed": 3},
            "compressed_turns": [{"facts": []}, {"facts": []}, {"facts": []}],
        }
        ok, detail = CONSISTENCY_CHECKS[0]["check"](state)
        assert ok
        assert detail == ""

    def test_mismatch(self):
        state = {
            "working_memory": {"turns_completed": 5},
            "compressed_turns": [{"facts": []}, {"facts": []}],
        }
        ok, detail = CONSISTENCY_CHECKS[0]["check"](state)
        assert not ok
        assert "turns_completed=5" in detail

    def test_missing_fields(self):
        state = {}
        ok, _detail = CONSISTENCY_CHECKS[0]["check"](state)
        assert ok  # 0 == 0, trivially passes


class TestSourceIdsSequential:
    def test_sequential(self):
        state = {
            "source_registry": {"S1": {}, "S2": {}, "S3": {}},
        }
        ok, _detail = CONSISTENCY_CHECKS[1]["check"](state)
        assert ok

    def test_gap(self):
        state = {
            "source_registry": {"S1": {}, "S3": {}, "S5": {}},
        }
        ok, detail = CONSISTENCY_CHECKS[1]["check"](state)
        assert not ok
        assert "S2" in detail or "gap" in detail.lower()

    def test_empty_registry(self):
        ok, _detail = CONSISTENCY_CHECKS[1]["check"]({"source_registry": {}})
        assert ok


class TestFactSourcesInRegistry:
    def test_valid_sources(self):
        state = {
            "source_registry": {"S1": {}, "S2": {}},
            "working_memory": {
                "facts": [
                    {"fact_id": "f1", "source_ids": ["S1"]},
                    {"fact_id": "f2", "source_ids": ["S1", "S2"]},
                ]
            },
        }
        ok, _detail = CONSISTENCY_CHECKS[2]["check"](state)
        assert ok

    def test_url_as_source_id(self):
        state = {
            "source_registry": {},
            "working_memory": {
                "facts": [
                    {"fact_id": "f1", "source_ids": ["https://example.com"]},
                ]
            },
        }
        ok, detail = CONSISTENCY_CHECKS[2]["check"](state)
        assert not ok
        assert "URL" in detail

    def test_orphan_source(self):
        state = {
            "source_registry": {"S1": {}},
            "working_memory": {
                "facts": [
                    {"fact_id": "f1", "source_ids": ["S99"]},
                ]
            },
        }
        ok, _detail = CONSISTENCY_CHECKS[2]["check"](state)
        assert not ok


class TestWorkflowEventsNoDuplicates:
    def test_no_duplicates(self):
        state = {
            "workflow_events": [
                {"event": "compress.completed", "payload": {"turn": 1}},
                {"event": "compress.completed", "payload": {"turn": 2}},
                {"event": "memory.updated", "payload": {"turn": 1}},
            ]
        }
        ok, _detail = CONSISTENCY_CHECKS[5]["check"](state)
        assert ok

    def test_duplicate(self):
        state = {
            "workflow_events": [
                {"event": "compress.completed", "payload": {"turn": 1}},
                {"event": "compress.completed", "payload": {"turn": 1}},
            ]
        }
        ok, _detail = CONSISTENCY_CHECKS[5]["check"](state)
        assert not ok


class TestRunConsistencyChecks:
    def test_all_pass_on_valid_state(self):
        """A well-formed state should pass all checks."""
        state = {
            "working_memory": {
                "turns_completed": 2,
                "facts": [
                    {"fact_id": "f1", "source_ids": ["S1"], "text": "revenue $100B"},
                    {"fact_id": "f2", "source_ids": ["S2"], "text": "growth 15%"},
                ],
            },
            "compressed_turns": [
                {"facts": [{"fact_id": "f1", "text": "revenue $100B", "source_ids": ["S1"]}]},
                {"facts": [{"fact_id": "f2", "text": "growth 15%", "source_ids": ["S2"]}]},
            ],
            "source_registry": {"S1": {}, "S2": {}},
            "llm_metrics": [
                {"node": "interview.ask_question", "total_tokens": 500},
                {"node": "interview.generate_answer", "total_tokens": 800},
            ],
            "workflow_events": [
                {"event": "compress.completed", "payload": {"turn": 1}},
                {"event": "compress.completed", "payload": {"turn": 2}},
            ],
        }
        result = run_consistency_checks(state)
        assert isinstance(result, ConsistencyResult)
        # Should have most rules passing — at minimum the "error" severity ones
        error_violations = [v for v in result.violations if v["severity"] == "error"]
        assert len(error_violations) == 0, f"Unexpected error violations: {error_violations}"

    def test_result_fields(self):
        result = run_consistency_checks({})
        assert isinstance(result.passed, bool)
        assert result.total_rules > 0
        assert result.passed_rules + result.failed_rules == result.total_rules

    def test_result_to_dict(self):
        result = run_consistency_checks({})
        d = result.to_dict()
        assert "passed" in d
        assert "total_rules" in d
        assert "violations" in d
