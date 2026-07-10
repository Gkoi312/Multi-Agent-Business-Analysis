"""
Feedback Tracker — records, queries, and summarises human feedback across versions.

Every time a human submits feedback through an approval gate, a ``FeedbackRecord``
is created.  The tracker persists these records alongside the task so the system
(and downstream LLM nodes) can understand **what changed and why**.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Feedback record
# ---------------------------------------------------------------------------

@dataclass
class FeedbackRecord:
    """A single feedback submission from a human reviewer."""

    record_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    """Unique ID for this feedback entry."""

    review_target: str = ""
    """What was being reviewed (e.g. ``"analysts"``, ``"plan"``, ``"draft"``)."""

    version: int = 0
    """Version number of the reviewed artifact at the time of feedback."""

    feedback: str = ""
    """The feedback text submitted by the reviewer."""

    submitted_by: str = ""
    """Username or principal who submitted the feedback."""

    submitted_at: float = field(default_factory=time.time)
    """Unix timestamp when feedback was submitted."""

    accepted: bool = True
    """Whether this feedback was acted upon (vs. overridden by a later revision)."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "review_target": self.review_target,
            "version": self.version,
            "feedback": self.feedback,
            "submitted_by": self.submitted_by,
            "submitted_at": self.submitted_at,
            "accepted": self.accepted,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "FeedbackRecord":
        return cls(
            record_id=str(d.get("record_id", "")),
            review_target=str(d.get("review_target", "")),
            version=int(d.get("version", 0) or 0),
            feedback=str(d.get("feedback", "")),
            submitted_by=str(d.get("submitted_by", "")),
            submitted_at=float(d.get("submitted_at", 0) or 0),
            accepted=bool(d.get("accepted", True)),
        )


# ---------------------------------------------------------------------------
# Feedback tracker
# ---------------------------------------------------------------------------

class FeedbackTracker:
    """Tracks human feedback across review cycles within a single task.

    Each task gets its own ``FeedbackTracker`` instance (not a singleton).
    The tracker is **not** persisted to disk itself — it lives in the graph
    state under a key like ``"feedback_history"``.  The route layer serialises
    it to JSON via ``to_dict()`` / ``from_dict()``.

    Usage::

        tracker = FeedbackTracker(task_id="abc123")

        # When feedback is submitted:
        tracker.record(
            review_target="analysts",
            version=2,
            feedback="Please add a legal analyst.",
            submitted_by="alice",
        )

        # In a downstream LLM node:
        summary = tracker.format_for_llm()
    """

    def __init__(self, task_id: str = ""):
        self.task_id = task_id
        self._records: list[FeedbackRecord] = []

    # -- CRUD ---------------------------------------------------------------

    def record(
        self,
        review_target: str,
        version: int,
        feedback: str,
        submitted_by: str = "",
    ) -> FeedbackRecord:
        """Create and store a new feedback record."""
        rec = FeedbackRecord(
            review_target=review_target,
            version=version,
            feedback=feedback,
            submitted_by=submitted_by,
        )
        self._records.append(rec)
        return rec

    @property
    def records(self) -> list[FeedbackRecord]:
        """All feedback records, oldest first."""
        return list(self._records)

    @property
    def latest(self) -> FeedbackRecord | None:
        """The most recent feedback record, if any."""
        return self._records[-1] if self._records else None

    def for_target(self, review_target: str) -> list[FeedbackRecord]:
        """All feedback records for a specific review target."""
        return [r for r in self._records if r.review_target == review_target]

    @property
    def total_rounds(self) -> int:
        """How many distinct review rounds have occurred."""
        return len(self._records)

    @property
    def latest_version(self) -> int:
        """The highest version number seen so far."""
        if not self._records:
            return 0
        return max(r.version for r in self._records)

    # -- Serialisation ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "records": [r.to_dict() for r in self._records],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "FeedbackTracker":
        tracker = cls(task_id=str(d.get("task_id", "")))
        for r in d.get("records", []) or []:
            tracker._records.append(FeedbackRecord.from_dict(r))
        return tracker

    # -- Formatting for LLM context -----------------------------------------

    def format_for_llm(self, max_records: int = 5) -> str:
        """Format recent feedback as a concise LLM-readable summary.

        Use this in ``SystemMessage`` content when a node needs to understand
        what the human reviewer changed and why.
        """
        if not self._records:
            return ""

        recent = self._records[-max_records:]
        lines = ["## Reviewer Feedback History"]

        for i, rec in enumerate(recent, 1):
            lines.append(
                f"\n### Round {i} — {rec.review_target!r} (v{rec.version})"
            )
            if rec.submitted_by:
                lines.append(f"*By: {rec.submitted_by}*")
            lines.append(rec.feedback)

        lines.append(f"\n*{self.total_rounds} total review round(s)*")
        return "\n".join(lines)

    def commit_all(self) -> None:
        """Mark all pending feedback records as accepted.

        Call this after a revision cycle completes successfully to indicate
        that the feedback was acted upon.
        """
        for rec in self._records:
            rec.accepted = True

    def pending_feedback(self) -> list[FeedbackRecord]:
        """Feedback records that have not yet been marked accepted."""
        return [r for r in self._records if not r.accepted]


# ---------------------------------------------------------------------------
# Feedback summary helpers (pure functions, stateless)
# ---------------------------------------------------------------------------

def summarise_feedback_for_prompt(
    records: list[FeedbackRecord],
    max_chars: int = 800,
) -> str:
    """Build a condensed feedback summary suitable for injection into a prompt.

    Unlike ``FeedbackTracker.format_for_llm()``, this is a pure function that
    works with any list of records — useful when you don't have a tracker
    instance, e.g. in a stateless node.
    """
    if not records:
        return ""

    parts: list[str] = []
    total_chars = 0

    for rec in reversed(records):  # newest first
        snippet = rec.feedback[:max_chars - total_chars]
        if not snippet:
            break
        parts.append(f"[v{rec.version}] {snippet}")
        total_chars += len(snippet) + 20  # overhead for prefix
        if total_chars >= max_chars:
            break

    return " | ".join(reversed(parts))  # chronological order
