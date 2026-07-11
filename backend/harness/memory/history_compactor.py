"""
History Compactor — triggers and executes history compaction when the
conversation exceeds token pressure thresholds.

Separate from turn fact extraction (which happens every turn).  History
compaction is only triggered when the context window is under pressure.
"""
from __future__ import annotations

from typing import Any

from harness.models.memory import RunningSummary, TokenCounter
from harness.memory.running_summary import RunningSummaryManager
from harness.memory.policies import CompactionPolicy


# ===========================================================================
# HistoryCompactor
# ===========================================================================


class HistoryCompactor:
    """Compacts conversation history by summarizing older messages.

    Responsibilities
    ---------------
    1. Check whether compaction is needed (token threshold).
    2. Determine which messages to compact (OLD) vs keep (RECENT).
    3. Generate an incremental summary via ``RunningSummaryManager``.
    4. Return the projected messages (summary + recent raw messages).

    The canonical message history in the graph state/checkpoint is NEVER
    mutated — this component produces a projection for the LLM call only.

    Parameters
    ----------
    policy : CompactionPolicy
        Configuration for when and how to compact.
    token_counter : TokenCounter
        Type-safe token counter with count_text/count_message/count_messages.
    model : Any
        LLM used for summary generation.
    summary_prompt : str
        Prompt template for summary generation.
    """

    def __init__(
        self,
        policy: CompactionPolicy | None = None,
        token_counter: TokenCounter | None = None,
        model: Any = None,
        summary_prompt: str = "",
    ):
        self.policy = policy or CompactionPolicy()
        self.token_counter = token_counter or TokenCounter()

        # Build a lambda-compatible bridge for RunningSummaryManager
        # (which expects Callable[[Any], int])
        def _count(msg: Any) -> int:
            if isinstance(msg, str):
                return self.token_counter.count_text(msg)
            if isinstance(msg, list):
                return self.token_counter.count_messages(msg)
            return self.token_counter.count_message(msg)

        self.model = model
        self._summary_mgr = RunningSummaryManager(
            token_counter=_count,
            max_summary_tokens=self.policy.max_summary_tokens,
        )
        self.summary_prompt = summary_prompt

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def should_compact(
        self,
        messages: list[Any],
        *,
        turn_count: int = 0,
        extra_tokens: int = 0,
    ) -> bool:
        """Return ``True`` when history compaction should run.

        Checks:
        1. Minimum turns threshold.
        2. Token count exceeds trigger.
        """
        if turn_count < self.policy.min_turns_before_compact:
            return False
        total = self.token_counter.count_messages(messages) + extra_tokens
        return total > self.policy.trigger_tokens

    def compact_history(
        self,
        messages: list[Any],
        *,
        running_summary: RunningSummary | None = None,
    ) -> tuple[list[Any], RunningSummary | None]:
        """Compact older messages into a summary.

        Returns ``(projected_messages, updated_running_summary)``.

        The projected messages consist of:
        1. A single ``HumanMessage`` with the summary (if any summary exists).
        2. Recent raw messages within the keep window.

        IMPORTANT: Only OLD messages are summarized. Recent messages within
        ``keep_recent_tokens`` are kept as raw. This avoids summary/raw duplication.

        Parameters
        ----------
        messages : list[Any]
            Full canonical message list.
        running_summary : RunningSummary | None
            Prior summarization cursor (if any).

        Returns
        -------
        tuple[list[Any], RunningSummary | None]
        """
        if not self.model:
            return list(messages), running_summary

        # Split: old messages (to summarize) vs recent (to keep raw)
        old_messages, recent_messages = self._split_old_recent(messages)

        if not old_messages:
            # Nothing old enough to compact
            if running_summary and running_summary.summary:
                return self._project_with_summary(recent_messages, running_summary), running_summary
            return list(messages), running_summary

        # Only summarize the OLD messages
        new_summary = self._summary_mgr.compute_new_summary(
            old_messages,
            running_summary=running_summary,
            model=self.model,
            summary_prompt=self.summary_prompt,
        )

        effective_summary = new_summary if new_summary is not None else running_summary

        if effective_summary is None or not effective_summary.summary:
            return list(messages), running_summary

        return self._project_with_summary(recent_messages, effective_summary), effective_summary

    async def acompact_history(
        self,
        messages: list[Any],
        *,
        running_summary: RunningSummary | None = None,
    ) -> tuple[list[Any], RunningSummary | None]:
        """Async variant of :meth:`compact_history`."""
        if not self.model:
            return list(messages), running_summary

        old_messages, recent_messages = self._split_old_recent(messages)

        if not old_messages:
            if running_summary and running_summary.summary:
                return self._project_with_summary(recent_messages, running_summary), running_summary
            return list(messages), running_summary

        new_summary = await self._summary_mgr.acompute_new_summary(
            old_messages,
            running_summary=running_summary,
            model=self.model,
            summary_prompt=self.summary_prompt,
        )

        effective_summary = new_summary if new_summary is not None else running_summary

        if effective_summary is None or not effective_summary.summary:
            return list(messages), running_summary

        return self._project_with_summary(recent_messages, effective_summary), effective_summary

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _split_old_recent(self, messages: list[Any]) -> tuple[list[Any], list[Any]]:
        """Split messages into OLD (to summarize) and RECENT (to keep raw).

        The boundary respects:
        - Human/AI Q&A round boundaries
        - AI tool call + parallel ToolMessage groups
        - Tool results followed by AI answer

        Never cuts in the middle of a coherent group.
        """
        max_tokens = self.policy.keep_recent_tokens

        # Walk backwards, accumulating recent messages
        recent: list[Any] = []
        total = 0
        pending_tool_ids: set[str] = set()

        # Phase 1: collect recent messages fit within token budget
        for msg in reversed(messages):
            tokens = self.token_counter.count_message(msg)
            if total + tokens > max_tokens and recent:
                break

            # Check if this is a ToolMessage — we need to include its AI tool_call
            tc_id = getattr(msg, "tool_call_id", None)
            if tc_id:
                pending_tool_ids.add(str(tc_id))

            recent.insert(0, msg)
            total += tokens

        # Phase 2: if we have pending tool IDs, walk further back to include
        # the originating AI message(s)
        if pending_tool_ids:
            cut_idx = messages.index(recent[0]) if recent else len(messages)
            for i in range(cut_idx - 1, -1, -1):
                msg = messages[i]
                tool_calls = getattr(msg, "tool_calls", None)
                if not tool_calls:
                    continue
                ai_ids = {str(tc.get("id", "")) for tc in tool_calls if tc.get("id")}
                if pending_tool_ids & ai_ids:
                    # Include this AI message in recent
                    recent.insert(0, msg)
                    for tc_id in ai_ids:
                        pending_tool_ids.discard(tc_id)
                    # Recurse: this AI message might be a response to a Human question
                    # — include the Human question too for round completeness
                    if i > 0 and getattr(messages[i - 1], "type", None) == "human":
                        recent.insert(0, messages[i - 1])

        # Everything before recent is OLD
        if recent:
            first_recent_idx = messages.index(recent[0])
            old = list(messages[:first_recent_idx])
        else:
            old = list(messages)
            recent = []

        return old, recent

    def _project_with_summary(
        self,
        recent_messages: list[Any],
        running_summary: RunningSummary,
    ) -> list[Any]:
        """Build projected message list: summary + recent raw messages only.

        Does NOT include old messages that were summarized — avoids
        summary/raw duplication.
        """
        if not running_summary.summary:
            return list(recent_messages)

        from langchain_core.messages import HumanMessage

        summary_msg = HumanMessage(
            content=f"[Conversation summary]\n{running_summary.summary}",
        )

        return [summary_msg] + list(recent_messages)

    def _keep_recent(self, messages: list[Any], max_tokens: int) -> list[Any]:
        """Select the most recent messages that fit within ``max_tokens``."""
        kept: list[Any] = []
        total = 0
        for msg in reversed(messages):
            tokens = self.token_counter.count_message(msg)
            if total + tokens > max_tokens and kept:
                break
            kept.insert(0, msg)
            total += tokens
        return kept
