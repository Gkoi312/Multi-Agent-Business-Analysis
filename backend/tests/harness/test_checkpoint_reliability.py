"""
Checkpoint Reliability Tests — verify serialization round-trips.

These test that memory model objects survive to_dict() → from_dict()
cycles without data loss. This directly exercises the serialization
paths that LangGraph checkpoints rely on.
"""

import pytest
from harness.models.memory import (
    CompressedTurn,
    CoveragePolicy,
    MemoryFact,
    RunningSummary,
    SearchDigest,
    SourceRecord,
)
from harness.memory.working_memory import WorkingMemory


def _deep_diff(a: dict, b: dict, prefix: str = "") -> list[str]:
    """Return list of keys where a and b differ."""
    diffs: list[str] = []
    all_keys = set(a.keys()) | set(b.keys())
    for key in sorted(all_keys):
        path = f"{prefix}.{key}" if prefix else str(key)
        va = a.get(key)
        vb = b.get(key)
        if isinstance(va, dict) and isinstance(vb, dict):
            diffs.extend(_deep_diff(va, vb, path))
        elif isinstance(va, list) and isinstance(vb, list):
            if len(va) != len(vb):
                diffs.append(f"{path}: length {len(va)} vs {len(vb)}")
            else:
                for i, (ia, ib) in enumerate(zip(va, vb)):
                    if isinstance(ia, dict) and isinstance(ib, dict):
                        diffs.extend(_deep_diff(ia, ib, f"{path}[{i}]"))
                    elif ia != ib:
                        diffs.append(f"{path}[{i}]: {ia!r} vs {ib!r}")
        elif va != vb:
            diffs.append(f"{path}: {va!r} vs {vb!r}")
    return diffs


class TestCompressedTurnRoundtrip:
    def test_with_facts(self):
        fact = MemoryFact(
            text="Revenue was $25.18B in Q3 2025",
            primary_category="financials",
            source_ids=["S1"],
            evidence_quality="high",
            subject="revenue",
            predicate="value",
            value=25.18,
            unit="billion_usd",
            period="Q3 2025",
        )
        turn = CompressedTurn(
            question_intent="Understand Tesla Q3 financial performance",
            facts=[fact],
            numbers_mentioned=[{"value": "25.18", "unit": "billion_usd", "context": "revenue"}],
        )
        d = turn.to_dict()
        restored = CompressedTurn.from_dict(d)

        assert restored.question_intent == turn.question_intent
        assert len(restored.facts) == 1
        assert restored.facts[0].text == "Revenue was $25.18B in Q3 2025"
        assert restored.facts[0].source_ids == ["S1"]
        assert restored.numbers_mentioned == [{"value": "25.18", "unit": "billion_usd", "context": "revenue"}]

    def test_empty_turn(self):
        turn = CompressedTurn()
        d = turn.to_dict()
        restored = CompressedTurn.from_dict(d)
        assert restored.question_intent == ""
        assert restored.facts == []

    def test_with_error(self):
        turn = CompressedTurn(
            question_intent="Test",
            compression_error="LLM returned empty JSON after all retries",
        )
        d = turn.to_dict()
        restored = CompressedTurn.from_dict(d)
        assert "empty JSON" in restored.compression_error


class TestWorkingMemoryRoundtrip:
    def test_basic_roundtrip(self):
        wm = WorkingMemory(
            coverage_policy=CoveragePolicy(
                required_for_full_report={"business_model": 3, "growth": 3},
                required_for_early_stop={"business_model": 2, "growth": 2},
            ),
        )
        wm.add_fact(
            "revenue was $25.18 billion in Q3 2025",
            category="financials",
            source_ids=["S1"],
            evidence_quality="high",
            value=25.18,
            unit="billion_usd",
            period="Q3 2025",
        )
        wm.add_fact(
            "growth rate 8% YoY",
            category="growth",
            source_ids=["S1"],
            evidence_quality="high",
            value=8.0,
            unit="percent",
        )

        d = wm.to_dict()
        restored = WorkingMemory.from_dict(d)

        assert restored.active_fact_count() == wm.active_fact_count()
        assert restored.turns_completed == wm.turns_completed
        assert restored.knowledge_gaps == wm.knowledge_gaps

    def test_empty_working_memory(self):
        wm = WorkingMemory()
        d = wm.to_dict()
        restored = WorkingMemory.from_dict(d)
        assert restored.active_fact_count() == 0
        assert restored.turns_completed == 0


class TestSearchDigestRoundtrip:
    def test_basic_roundtrip(self):
        digest = SearchDigest(
            query="Tesla Q3 2025 revenue",
            source_ids=["src_001", "src_002"],
            evidence_snippets=["Revenue was $25.18B", "Gross margin 19.8%"],
            tokens_before=5000,
            tokens_after=800,
            source_registry={
                "src_001": SourceRecord(
                    source_id="src_001",
                    url="https://example.com/tesla-q3",
                    title="Tesla Q3 Results",
                ),
            },
        )
        d = digest.to_dict()
        restored = SearchDigest.from_dict(d)

        assert restored.query == "Tesla Q3 2025 revenue"
        assert restored.source_ids == ["src_001", "src_002"]
        assert restored.tokens_before == 5000
        assert restored.tokens_after == 800
        assert "src_001" in restored.source_registry
        assert restored.source_registry["src_001"].url == "https://example.com/tesla-q3"


class TestRunningSummaryRoundtrip:
    def test_basic_roundtrip(self):
        rs = RunningSummary(
            summary="So far we've covered Tesla's Q3 financials and AI strategy.",
            summarized_message_ids={"msg_001", "msg_002"},
            version=2,
        )
        d = rs.to_dict()
        restored = RunningSummary.from_dict(d)

        assert restored.summary == rs.summary
        assert restored.version == 2


class TestCrossModelConsistency:
    """Verify that different model serialization paths don't break each other."""

    def test_fact_ledger_via_working_memory(self):
        """A fact ingested into WorkingMemory should survive serialization."""
        wm = WorkingMemory()
        wm.add_fact(
            "market share 30%",
            category="competition",
            source_ids=["S5"],
            evidence_quality="medium",
            value=30,
            unit="percent",
        )

        d = wm.to_dict()
        restored = WorkingMemory.from_dict(d)

        active = restored.active_facts
        assert len(active) == 1
        assert active[0].text == "market share 30%"
        assert active[0].value == 30
