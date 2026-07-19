"""
Memory-loop node factories — generic LangGraph node callables for the
"compress → update working memory → compact history → continue-or-stop"
segment of a multi-turn research loop.

These factories contain zero domain vocabulary. They only read/write the
state keys ``messages``, ``turn_count``, ``max_num_turns``,
``compressed_turns``, ``working_memory``, ``running_summary`` and
``_current_turn_registry`` / ``source_registry`` — the same generic
contract any domain's TypedDict state already needs to satisfy to use
``harness.memory`` at all (see ``domains/due_diligence/schemas.py`` for
the reference implementation of that contract).

A domain builds its research-loop graph by wiring these factory outputs
in as nodes, alongside its own domain-specific ``ask`` / ``search`` /
``answer`` / ``produce`` nodes (which own the prompts and therefore can't
be generic). Example::

    from harness.memory.nodes import (
        make_compress_node,
        make_update_memory_node,
        make_compact_history_node,
        make_should_continue_router,
    )

    builder.add_node("compress", make_compress_node(self.compressor))
    builder.add_node("update_memory", make_update_memory_node(self._domain_config))
    builder.add_node("compact_history", make_compact_history_node(self.compressor))
    builder.add_conditional_edges(
        "compact_history",
        make_should_continue_router(continue_node="ask_question", stop_node="save_interview"),
        ["ask_question", "save_interview"],
    )
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from harness.memory.compressor import IncrementalCompressor
from harness.memory.policies import MemoryDomainConfig
from harness.memory.working_memory import WorkingMemory
from harness.models.memory import CompressedTurn, RunningSummary

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared helper — reconstruct WorkingMemory from state
# ---------------------------------------------------------------------------


def build_working_memory_from_state(
    state: dict[str, Any], domain_config: MemoryDomainConfig
) -> WorkingMemory:
    """Reconstruct a ``WorkingMemory`` from ``state["working_memory"]``.

    Always injects *domain_config* (and a matching ``FactReconciler``), since
    a fresh deserialisation from ``dict`` doesn't carry non-serialisable
    config objects.
    """
    wm_dict = state.get("working_memory") or {}
    if wm_dict:
        wm = WorkingMemory.from_dict(wm_dict)
    else:
        wm = WorkingMemory(
            coverage_policy=domain_config.coverage_policy,
            domain_config=domain_config,
        )
    if wm.domain_config is None:
        wm.domain_config = domain_config
    if wm._reconciler is None or wm._reconciler._domain_config is None:
        from harness.memory.fact_reconciler import FactReconciler
        wm._reconciler = FactReconciler(domain_config=domain_config)
    return wm


def format_working_memory_context(state: dict[str, Any]) -> str:
    """Build a prompt-ready block summarising what's been learned so far.

    Combines the formatted ``WorkingMemory`` with the last two compressed
    turns. Used by domain ``ask``/``answer`` nodes to brief the LLM.
    """
    wm_dict = state.get("working_memory") or {}
    compressed = state.get("compressed_turns") or []

    if not wm_dict and not compressed:
        return ""

    parts: list[str] = []

    if wm_dict:
        wm = WorkingMemory.from_dict(wm_dict)
        if wm.active_fact_count() > 0:
            parts.append(wm.format())

    if compressed:
        recent = compressed[-2:]
        parts.append("\n## Compressed prior rounds")
        for i, turn_dict in enumerate(recent):
            try:
                turn = CompressedTurn.from_dict(turn_dict)
                turn_num = len(compressed) - len(recent) + i + 1
                parts.append(f"\n### Round {turn_num}")
                parts.append(turn.format())
            except Exception:
                pass

    return "\n".join(parts) if parts else ""


# ---------------------------------------------------------------------------
# Node factories
# ---------------------------------------------------------------------------


def make_compress_node(compressor: IncrementalCompressor) -> Callable[[dict], dict]:
    """Build a ``compress`` node: turns the latest Q&A into a ``CompressedTurn``.

    Reads the last human/ai message pair from ``state["messages"]`` and the
    current-turn source registry (``state["_current_turn_registry"]``,
    falling back to ``state["source_registry"]``). Appends the result to
    ``state["compressed_turns"]``.
    """

    def _compress(state: dict[str, Any]) -> dict:
        try:
            question, answer = IncrementalCompressor.extract_last_question_and_answer(
                state["messages"]
            )

            current_registry = state.get("_current_turn_registry") or {}
            if not current_registry:
                current_registry = state.get("source_registry") or {}

            turn_count = int(state.get("turn_count", 1) or 1)
            logger.info("Compressing turn %s", turn_count)

            compressed = compressor.compress_completed_turn(
                question=question,
                answer=answer,
                source_registry=current_registry,
            )

            compressed_history: list[dict] = list(state.get("compressed_turns", []) or [])
            compressed_history.append(compressed.to_dict())

            fact_count = len(compressed.facts) if compressed.facts else len(compressed.key_findings)
            logger.info(
                "Turn %s compressed: %s facts, quality=%s",
                turn_count, fact_count, compressed.evidence_quality,
            )

            return {
                "compressed_turns": compressed_history,
                "workflow_events": [
                    {
                        "event": "compress.completed",
                        "payload": {"turn": turn_count, "facts_extracted": fact_count},
                    }
                ],
            }

        except Exception as e:
            logger.error("Error compressing turn: %s", e)
            return {
                "workflow_events": [
                    {"event": "compress.failed", "payload": {"error": str(e)}}
                ],
            }

    return _compress


def make_update_memory_node(domain_config: MemoryDomainConfig) -> Callable[[dict], dict]:
    """Build an ``update_memory`` node: syncs ``WorkingMemory`` from compressed turns.

    Ingests only turns not yet processed (``compressed_turns[wm.turns_completed:]``),
    so it's safe to call every loop iteration without double-counting facts.
    """

    def _update_memory(state: dict[str, Any]) -> dict:
        try:
            wm = build_working_memory_from_state(state, domain_config)
            compressed_history: list[dict] = list(state.get("compressed_turns", []) or [])

            turns_to_ingest = compressed_history[wm.turns_completed:]
            for turn_dict in turns_to_ingest:
                turn = CompressedTurn.from_dict(turn_dict) if isinstance(turn_dict, dict) else turn_dict
                wm.ingest_compressed_turn(turn)

            snapshot = wm.to_merged_memory()
            turn_count = int(state.get("turn_count", 1) or 1)
            logger.info(
                "Working memory updated (turn %s): %s total facts, %s active, gaps=%s, conflicts=%s",
                turn_count, snapshot.total_facts, wm.active_fact_count(),
                wm.knowledge_gaps, len(wm.unresolved_conflicts),
            )

            return {
                "working_memory": wm.to_dict(),
                "memory_snapshot": snapshot.to_dict(),
                "workflow_events": [
                    {
                        "event": "memory.updated",
                        "payload": {
                            "total_facts": snapshot.total_facts,
                            "knowledge_gaps": wm.knowledge_gaps,
                            "risk_flag_count": len(wm.risk_flags),
                            "unresolved_conflicts": len(wm.unresolved_conflicts),
                        },
                    }
                ],
            }

        except Exception as e:
            logger.error("Error updating working memory: %s", e)
            return {
                "workflow_events": [
                    {"event": "memory.update_failed", "payload": {"error": str(e)}}
                ],
            }

    return _update_memory


def make_compact_history_node(compressor: IncrementalCompressor) -> Callable[[dict], dict]:
    """Build a ``compact_history`` node: compacts the message history under token pressure.

    No-ops (cheaply) when ``compressor.should_compact_history(...)`` says the
    window isn't under pressure yet.
    """

    def _compact_history(state: dict[str, Any]) -> dict:
        try:
            messages = state["messages"]
            rs_dict = state.get("running_summary") or {}
            running_summary = RunningSummary.from_dict(rs_dict) if rs_dict else None

            turn_count = int(state.get("turn_count", 1) or 1)
            wm_str = format_working_memory_context(state)
            ct_str = IncrementalCompressor.format_compressed_turns([
                CompressedTurn.from_dict(d) if isinstance(d, dict) else d
                for d in (state.get("compressed_turns") or [])
            ])

            if not compressor.should_compact_history(
                messages, turn_count=turn_count,
                working_memory_str=wm_str, compressed_turns_str=ct_str,
            ):
                return {
                    "workflow_events": [
                        {"event": "compact_history.skipped", "payload": {"reason": "below_threshold"}}
                    ],
                }

            logger.info("Compacting conversation history (turn %s)", turn_count)
            _, updated_rs = compressor.compact_history(
                messages, running_summary=running_summary,
            )

            version = updated_rs.version if updated_rs is not None else (
                running_summary.version if running_summary else 0
            )
            result: dict[str, Any] = {
                "workflow_events": [
                    {"event": "compact_history.completed", "payload": {"version": version}}
                ],
            }
            if updated_rs is not None:
                result["running_summary"] = updated_rs.to_dict()
            return result

        except Exception as e:
            logger.error("Error during history compaction: %s", e)
            return {
                "workflow_events": [
                    {"event": "compact_history.failed", "payload": {"error": str(e)}}
                ],
            }

    return _compact_history


def make_should_continue_router(
    *, continue_node: str, stop_node: str
) -> Callable[[dict], str]:
    """Build the loop-vs-stop router for a memory-driven research loop.

    Stops when either the turn budget (``max_num_turns``) is exhausted, or
    ``WorkingMemory.has_sufficient_coverage()`` says the configured
    ``coverage_policy`` is satisfied — both purely config-driven, no
    domain-specific category names appear here.
    """

    def _should_continue(state: dict[str, Any]) -> str:
        max_turns = int(state.get("max_num_turns", 1) or 1)
        turn_count = int(state.get("turn_count", 0) or 0)

        if turn_count >= max_turns:
            return stop_node

        wm_dict = state.get("working_memory") or {}
        if wm_dict:
            wm = WorkingMemory.from_dict(wm_dict)
            if wm.has_sufficient_coverage():
                logger.info(
                    "Coverage sufficient — stopping early (turn %s, %s facts, %s conflicts)",
                    turn_count, wm.active_fact_count(), len(wm.unresolved_conflicts),
                )
                return stop_node

        return continue_node

    return _should_continue
