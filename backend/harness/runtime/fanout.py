"""
Fan-out / Fan-in — parallel dispatch and result collection for LangGraph.

The ``Send``-based fan-out is the heart of the "map" phase in map-reduce
workflows:  a list of items is turned into parallel sub-graph invocations.
This module provides generic helpers so domain code doesn't need to write
boilerplate Send loops.

Usage sketch (domain code)::

    from harness.runtime.fanout import fan_out

    def dispatch_interviews(state: ResearchGraphState):
        return fan_out(
            items=state["analysts"],
            target="conduct_interview",
            payload_fn=lambda a: {
                "analyst": a,
                "messages": [HumanMessage(content=f"Research: {state['research_query']}")],
            },
            base_payload={
                "max_num_turns": state.get("max_num_turns", 1),
                "turn_count": 0,
            },
        )
"""
from __future__ import annotations

from typing import Any, Callable

from langgraph.types import Send


# ---------------------------------------------------------------------------
# Core fan-out
# ---------------------------------------------------------------------------


def fan_out(
    items: list[Any],
    target: str,
    payload_fn: Callable[[Any], dict[str, Any]] | None = None,
    base_payload: dict[str, Any] | None = None,
) -> list[Send]:
    """Create a ``Send`` for each item, dispatching to *target*.

    This is the standard LangGraph map-phase primitive.  Use it as the
    return value of a node that sits immediately before a fan-out boundary.

    Parameters
    ----------
    items:
        The collection to fan out over.  One ``Send`` is produced per item.
    target:
        Name of the node each item will be sent to.
    payload_fn:
        Called once per item.  Must return a dict.  The item is passed as
        the sole argument.  If omitted, the item itself is used as the
        payload (it must be a dict).
    base_payload:
        Merged into every item's payload.  Item-level keys take precedence.

    Returns
    -------
    list[Send]
        Suitable as the return value of a ``StateGraph`` node.

    Example::

        def start_interviews(state):
            return fan_out(
                items=state["analysts"],
                target="conduct_interview",
                payload_fn=lambda a: {"analyst": a},
                base_payload={"max_num_turns": 3},
            )
    """
    base = dict(base_payload or {})
    sends: list[Send] = []

    for item in items:
        if payload_fn is not None:
            payload = payload_fn(item)
        elif isinstance(item, dict):
            payload = dict(item)
        else:
            raise TypeError(
                f"fan_out item must be a dict or payload_fn must be provided. "
                f"Got {type(item).__name__}: {item!r}"
            )
        merged = {**base, **payload}
        sends.append(Send(target, merged))

    return sends


# ---------------------------------------------------------------------------
# Fan-out with conditional skip
# ---------------------------------------------------------------------------


def fan_out_if(
    items: list[Any],
    target: str,
    payload_fn: Callable[[Any], dict[str, Any]],
    base_payload: dict[str, Any] | None = None,
    *,
    predicate: Callable[[Any], bool] | None = None,
    fallback_target: str = "",
    fallback_payload_fn: Callable[[Any], dict[str, Any]] | None = None,
) -> list[Send] | str:
    """Fan-out with optional filtering.

    If *predicate* is given, only items that pass it are dispatched to
    *target*.  Items that fail the predicate go to *fallback_target* instead
    (if provided).

    Returns ``list[Send]`` when used as a fan-out node, or a single ``str``
    node name when there are zero items (routing to END / skip node).
    """
    if not items:
        return fallback_target or "__end__"

    base = dict(base_payload or {})
    sends: list[Send] = []

    for item in items:
        if predicate is None or predicate(item):
            payload = payload_fn(item)
            sends.append(Send(target, {**base, **payload}))
        elif fallback_target and fallback_payload_fn:
            payload = fallback_payload_fn(item)
            sends.append(Send(fallback_target, {**base, **payload}))

    if not sends and fallback_target:
        return fallback_target
    return sends


# ---------------------------------------------------------------------------
# Fan-in helpers (collect results from parallel branches)
# ---------------------------------------------------------------------------


def collect_sections(state: dict[str, Any], *,
                     key: str = "sections",
                     sort_key: str = "") -> list[Any]:
    """Extract and optionally sort collected results from fan-in.

    LangGraph's ``operator.add`` reducer automatically merges list-typed
    state keys from parallel branches.  This helper reads the merged result.

    Parameters
    ----------
    state:
        The graph state after all parallel branches have completed.
    key:
        State key holding the merged list.
    sort_key:
        Optional attribute/key name to sort results by.
    """
    items = list(state.get(key, []) or [])
    if sort_key:
        items.sort(key=lambda x: (
            x.get(sort_key, "") if isinstance(x, dict)
            else getattr(x, sort_key, "")
        ))
    return items


def collect_metrics(state: dict[str, Any]) -> dict[str, int]:
    """Aggregate LLM metrics from all parallel branches.

    Returns a dict with ``total_prompt_tokens``, ``total_completion_tokens``,
    ``total_tokens``, and ``call_count``.
    """
    metrics = state.get("llm_metrics", []) or []
    return {
        "total_prompt_tokens": sum(m.get("prompt_tokens", 0) or 0 for m in metrics if isinstance(m, dict)),
        "total_completion_tokens": sum(m.get("completion_tokens", 0) or 0 for m in metrics if isinstance(m, dict)),
        "total_tokens": sum(m.get("total_tokens", 0) or 0 for m in metrics if isinstance(m, dict)),
        "call_count": len(metrics),
    }


# ---------------------------------------------------------------------------
# Fan-out node factory
# ---------------------------------------------------------------------------


class FanOutNode:
    """Reusable callable node that dispatches items to a target sub-graph.

    Use this when your fan-out logic doesn't need custom per-item logic —
    just "for each X in state[key], start subgraph *target*".

    Parameters
    ----------
    items_key:
        State key holding the list to iterate over.
    target:
        Sub-graph node name to dispatch each item to.
    payload_mapping:
        Map of state keys → payload keys to copy into each sub-graph's
        initial state.  e.g. ``{"max_num_turns": "max_num_turns"}``.
    extra_payload:
        Static keys added to every sub-graph's initial state.
    """

    def __init__(
        self,
        items_key: str,
        target: str,
        payload_mapping: dict[str, str] | None = None,
        extra_payload: dict[str, Any] | None = None,
    ):
        self.items_key = items_key
        self.target = target
        self.payload_mapping = payload_mapping or {}
        self.extra_payload = extra_payload or {}

    def __call__(self, state: dict[str, Any]) -> dict[str, Any] | list[Send] | str:
        items = list(state.get(self.items_key, []) or [])
        if not items:
            return {}  # no items → no-op

        base = dict(self.extra_payload)
        for state_key, payload_key in self.payload_mapping.items():
            val = state.get(state_key)
            if val is not None:
                base[payload_key] = val

        return fan_out(
            items=items,
            target=self.target,
            payload_fn=lambda item: item if isinstance(item, dict) else {"item": item},
            base_payload=base,
        )

    def __repr__(self) -> str:
        return f"FanOutNode({self.items_key!r} → {self.target!r})"
