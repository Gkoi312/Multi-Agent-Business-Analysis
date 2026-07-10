"""
Runtime State — generic TypedDict schemas and reducers for graph templates.

Each graph mode has a recommended base state shape.  Domain adapters can extend
or compose these with domain-specific keys.
"""
from __future__ import annotations

import operator
from typing import Annotated, Any

from langgraph.graph import MessagesState
from typing_extensions import TypedDict

# ---------------------------------------------------------------------------
# Reducers
# ---------------------------------------------------------------------------


def keep_latest(_, new: Any) -> Any:
    """Reducer for scalar state keys updated by parallel branches.

    The LAST writer wins — suitable for keys like ``status``, ``mode``,
    or configuration values that aren't append-only.
    """
    return new


def merge_lists(a: list, b: list) -> list:
    """Reducer that concatenates two lists (append-only semantics).

    Use when parallel branches each contribute items to a shared list,
    e.g. ``sections``, ``events``, ``llm_metrics``.
    """
    return (a or []) + (b or [])


def merge_dicts(a: dict, b: dict) -> dict:
    """Reducer that shallow-merges two dicts (last-write-wins per key).

    Use when parallel branches contribute to a shared metadata dict.
    """
    merged = dict(a or {})
    merged.update(b or {})
    return merged


# ---------------------------------------------------------------------------
# State mixins
# ---------------------------------------------------------------------------


class RuntimeStateMixin(TypedDict, total=False):
    """Keys that every graph template expects in state.

    Domain adapters should include these in their state schema.
    """

    # -- execution control --
    mode: Annotated[str, keep_latest]
    status: Annotated[str, keep_latest]
    error: Annotated[str, keep_latest]
    failed_stage: Annotated[str, keep_latest]

    # -- review / human-in-the-loop --
    human_analyst_feedback: str
    review_version: Annotated[int, keep_latest]
    _review_target: Annotated[str, keep_latest]
    _gate_meta: Annotated[dict, keep_latest]
    _last_review_feedback: Annotated[str, keep_latest]

    # -- workflow observability --
    workflow_events: Annotated[list, merge_lists]
    llm_metrics: Annotated[list, merge_lists]

    # -- fan-out artifacts --
    sections: Annotated[list, merge_lists]


# ---------------------------------------------------------------------------
# Plan-Execute state
# ---------------------------------------------------------------------------


class PlanExecuteState(RuntimeStateMixin):
    """State for the ``plan_execute`` graph mode.

    Flow:  prepare → plan → review_gate → (revise | fan-out execute) → assemble → finalize

    Domains extend this with business-specific keys.
    """

    # -- planning --
    research_query: str
    max_items: int  # e.g. max_analysts, max_sections, …
    plan: Annotated[dict, keep_latest]  # serialised plan object
    plan_items: list  # the items to fan-out over (e.g. analysts)

    # -- execution results --
    assembled_output: Annotated[str, keep_latest]  # final composed result


# ---------------------------------------------------------------------------
# Debate state
# ---------------------------------------------------------------------------


class DebateState(RuntimeStateMixin):
    """State for the ``debate`` graph mode.

    Flow:  present_positions → cross_examine → judge_ruling
    """

    topic: str
    positions: Annotated[list, merge_lists]  # each position is a dict with {role, argument, evidence}
    cross_examinations: Annotated[list, merge_lists]
    ruling: Annotated[dict, keep_latest]
    final_verdict: Annotated[str, keep_latest]


# ---------------------------------------------------------------------------
# Research state
# ---------------------------------------------------------------------------


class ResearchState(RuntimeStateMixin):
    """State for the ``research`` graph mode.

    Flow:  investigate → evaluate → (loop | produce_output)

    This is a recursive research loop — the graph keeps investigating
    until a sufficiency condition is met.
    """

    research_query: str
    investigation_round: Annotated[int, keep_latest]
    max_rounds: Annotated[int, keep_latest]

    findings: Annotated[list, merge_lists]
    sufficiency_score: Annotated[float, keep_latest]  # 0.0 .. 1.0
    sufficiency_threshold: Annotated[float, keep_latest]

    final_output: Annotated[str, keep_latest]
