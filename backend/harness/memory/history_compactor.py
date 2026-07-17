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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _split_old_recent(self, messages: list[Any]) -> tuple[list[Any], list[Any]]:
        """Split messages into OLD (to summarize) and RECENT (to keep raw).

        Every round in this harness is a fixed 2-message unit — a question
        appended by the ask node, then its answer appended by the answer
        node (see domains/due_diligence/interview.py: ask_question and
        generate_answer each append exactly one message, with no
        HumanMessage in between). The boundary is aligned to an even index
        so a round is never split between its question and its answer.

        NOTE: no domain currently does LangChain-style tool-calling inside
        state["messages"] (search runs as a plain function call, not a tool
        call — see domains/due_diligence/interview.py's _search_web). If a
        future domain adds a ReAct-style tool-calling agent, this method
        will need boundary protection for tool_call_id <-> ToolMessage
        pairs before being reused there.
        """
        max_tokens = self.policy.keep_recent_tokens

        # Walk backwards, accumulating recent messages within budget
        recent: list[Any] = []
        total = 0
        for msg in reversed(messages):
            tokens = self.token_counter.count_message(msg)
            if total + tokens > max_tokens and recent:
                break
            recent.insert(0, msg)
            total += tokens

        if not recent:
            return list(messages), []

        first_recent_idx = messages.index(recent[0])

        # Align to a round boundary: an odd cut index means we've split a
        # question from its answer, so pull the question in too.
        if first_recent_idx % 2 == 1:
            first_recent_idx -= 1
            recent.insert(0, messages[first_recent_idx])

        old = list(messages[:first_recent_idx])
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
