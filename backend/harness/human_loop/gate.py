"""
Human Review Gate — a universal pause/approval mechanism for LangGraph workflows.

Any graph can include a ``HumanReviewGate`` node.  When execution reaches this node
the graph **interrupts** (via LangGraph's ``interrupt_before``) and waits for an
external caller to inject review feedback through ``graph.update_state()``.

This module is **domain-agnostic** — it knows nothing about due diligence, analysts,
or report sections.  It only deals with abstract *review targets* and *feedback*.

Usage sketch::

    from harness.human_loop.gate import HumanReviewGate

    gate = HumanReviewGate(
        review_target=ReviewTarget.ANALYSTS,
        version_key="analyst_version",
    )

    builder.add_node("review_gate", gate)
    builder.add_conditional_edges(
        "review_gate",
        gate.build_router(
            feedback_key="human_analyst_feedback",
            approved_next="continue_work",
            revise_next="regenerate",
        ),
        ["continue_work", "regenerate"],
    )
"""
from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from app.logger import GLOBAL_LOGGER


# ---------------------------------------------------------------------------
# Review target — what is being reviewed
# ---------------------------------------------------------------------------

class ReviewTarget(str, enum.Enum):
    """Standard review targets that a gate can guard.

    Domain code can also pass a plain string for custom targets.
    """
    ANALYSTS = "analysts"
    PLAN = "plan"
    DRAFT = "draft"
    FINAL_REPORT = "final_report"


# ---------------------------------------------------------------------------
# Gate metadata (auto-populated by the node)
# ---------------------------------------------------------------------------

@dataclass
class GateMetadata:
    """Metadata recorded when a gate is reached.

    Domain nodes can read ``state["_gate_meta"]`` to surface review context
    in the UI (e.g. "Review the 3 analyst personas below").
    """

    review_target: str
    """What the human is being asked to review."""

    version: int = 0
    """Monotonic version counter for this review target within the task."""

    reached_at: float = field(default_factory=time.time)
    """Unix timestamp when the gate was reached."""

    summary: str = ""
    """Optional human-readable summary of what's presented for review."""

    hint: str = ""
    """Optional guidance shown to the reviewer (e.g. "Leave empty to approve")."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_target": self.review_target,
            "version": self.version,
            "reached_at": self.reached_at,
            "summary": self.summary,
            "hint": self.hint,
        }


# ---------------------------------------------------------------------------
# HumanReviewGate
# ---------------------------------------------------------------------------

class HumanReviewGate:
    """A universal human-in-the-loop pause node.

    Insert this node into **any** ``StateGraph`` to create a review checkpoint.
    The node itself is a no-op — its only job is to mark the state so downstream
    routers and the front-end know what is being reviewed.

    Parameters
    ----------
    review_target:
        What is being reviewed.  Use ``ReviewTarget`` members or a custom string.
    version_key:
        State key that holds the version number for this review target.
        The gate auto-increments it each time it is reached *after a revision*.
    summary_key:
        Optional state key whose value is cat as the gate summary.
    hint:
        Optional guidance text shown to the reviewer.
    """

    def __init__(
        self,
        review_target: str = "",
        version_key: str = "review_version",
        summary_key: str = "",
        hint: str = "",
    ):
        self.review_target = review_target
        self.version_key = version_key
        self.summary_key = summary_key
        self.hint = hint
        self._logger = GLOBAL_LOGGER.bind(
            module="HumanReviewGate", target=review_target
        )

    # -- node callable (for add_node) ---------------------------------------

    def __call__(self, state: dict[str, Any]) -> dict[str, Any]:
        """LangGraph node — no-op that records review metadata.

        The graph **must** be compiled with this node in ``interrupt_before``,
        otherwise execution will sail right past without pausing.
        """
        current_version = int(state.get(self.version_key, 0) or 0)
        # Bump version if this is a re-review (previous feedback was non-empty)
        last_feedback = str(state.get("_last_review_feedback", "") or "").strip()
        if last_feedback:
            current_version += 1

        summary = ""
        if self.summary_key:
            raw = state.get(self.summary_key, "")
            if isinstance(raw, str):
                summary = raw[:200]
            else:
                summary = str(raw)[:200]

        meta = GateMetadata(
            review_target=self.review_target,
            version=current_version,
            summary=summary,
            hint=self.hint or "Submit feedback to revise, or leave empty to approve and continue.",
        )

        self._logger.info(
            "Review gate reached",
            target=self.review_target,
            version=current_version,
        )

        return {
            self.version_key: current_version,
            "_review_target": self.review_target,
            "_gate_meta": meta.to_dict(),
        }

    # -- router builder ------------------------------------------------------

    @staticmethod
    def build_router(
        feedback_key: str,
        approved_next: str,
        revise_next: str,
    ) -> Callable[[dict[str, Any]], str]:
        """Build a conditional-edge router function.

        Parameters
        ----------
        feedback_key:
            State key that holds the human's feedback string.
            Non-empty → *revise*; empty → *approved*.
        approved_next:
            Node to route to when feedback is empty (approved).
        revise_next:
            Node to route to when feedback is non-empty (needs revision).

        Returns
        -------
        Callable
            A router function suitable for ``add_conditional_edges``.
        """

        def _router(state: dict[str, Any]) -> str:
            feedback = str(state.get(feedback_key, "") or "").strip()
            if feedback:
                GLOBAL_LOGGER.info(
                    "Review: routing to revise",
                    feedback_key=feedback_key,
                    feedback_len=len(feedback),
                )
                return revise_next
            GLOBAL_LOGGER.info(
                "Review: approved, routing onward",
                feedback_key=feedback_key,
            )
            return approved_next

        # Store metadata on the function so callers can inspect it
        _router._feedback_key = feedback_key  # type: ignore[attr-defined]
        _router._approved_next = approved_next  # type: ignore[attr-defined]
        _router._revise_next = revise_next  # type: ignore[attr-defined]
        return _router

    # -- state-update helper ------------------------------------------------

    @staticmethod
    def inject_feedback(
        state: dict[str, Any],
        feedback: str,
        feedback_key: str = "human_analyst_feedback",
    ) -> dict[str, Any]:
        """Return a state update dict with the human's feedback recorded.

        This is a convenience for route-layer code that calls
        ``graph.update_state(thread, HumanReviewGate.inject_feedback(...), as_node="...")``.
        """
        return {
            feedback_key: feedback,
            "_last_review_feedback": feedback,
        }


# ---------------------------------------------------------------------------
# Composite gate — multiple review targets in one workflow
# ---------------------------------------------------------------------------

class MultiGate:
    """A registry of named gates for workflows that need several review points.

    Usage::

        gates = MultiGate()
        gates.register("analysts", HumanReviewGate(review_target=ReviewTarget.ANALYSTS))
        gates.register("plan", HumanReviewGate(review_target=ReviewTarget.PLAN))

        # In graph builder:
        builder.add_node("review_analysts", gates["analysts"])
        builder.add_node("review_plan", gates["plan"])
        builder.compile(interrupt_before=["review_analysts", "review_plan"])
    """

    def __init__(self):
        self._gates: dict[str, HumanReviewGate] = {}

    def register(self, name: str, gate: HumanReviewGate) -> None:
        """Register a named gate."""
        self._gates[name] = gate

    def __getitem__(self, name: str) -> HumanReviewGate:
        if name not in self._gates:
            raise KeyError(f"Gate {name!r} not registered. Available: {list(self._gates)}")
        return self._gates[name]

    def __contains__(self, name: str) -> bool:
        return name in self._gates

    @property
    def names(self) -> list[str]:
        return list(self._gates.keys())
