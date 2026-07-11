"""
Running Summary — incremental, idempotent summarization cursor.

Based on LangMem's ``RunningSummary`` pattern:
- Already-summarized messages are never re-summarized.
- Checkpoint retries produce idempotent results.
- Parallel tool-call groups are summarized atomically.
- Messages without IDs get stable fallback IDs (NO list-index dependency).
"""
from __future__ import annotations

import hashlib
from typing import Any, Callable

from harness.models.memory import RunningSummary, _stable_message_id


# ===========================================================================
# RunningSummaryManager
# ===========================================================================


class RunningSummaryManager:
    """Manages incremental summarization with idempotent checkpoint safety.

    Parameters
    ----------
    token_counter : Callable
        Function that estimates tokens for a message or text.
    max_summary_tokens : int
        Max tokens to budget for the summary text itself.
    """

    def __init__(
        self,
        token_counter: Callable[[Any], int],
        max_summary_tokens: int = 256,
    ):
        self.token_counter = token_counter
        self.max_summary_tokens = max_summary_tokens

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute_new_summary(
        self,
        messages: list[Any],
        *,
        running_summary: RunningSummary | None = None,
        model: Any,
        summary_prompt: str = "",
    ) -> RunningSummary | None:
        """Compute an incremental summary for messages, updating a running cursor.

        Returns ``None`` if no new messages need summarization, or a new
        ``RunningSummary`` with updated cursor tracking.

        Parameters
        ----------
        messages : list[Any]
            All messages (including previously summarized ones).
        running_summary : RunningSummary | None
            The running summary cursor from the prior checkpoint iteration.
        model : Any
            LLM to use for summary generation. Must support ``.invoke()``.
        summary_prompt : str
            Prompt template for summary generation. Use ``{existing_summary}``
            and ``{new_messages}`` placeholders.
        """
        if running_summary is None:
            running_summary = RunningSummary()

        # Find unsummarized messages
        new_messages = self._find_new_messages(messages, running_summary)

        if not new_messages:
            return None

        # Ensure last message isn't an orphaned AI tool-call without responses
        new_messages = self._complete_tool_call_boundary(new_messages, messages)

        # Don't summarize too few messages
        if len(new_messages) < 2:
            return None

        # Build new summary
        new_text = self._build_summary_block(new_messages)
        summary_content = self._generate_summary(
            model=model,
            existing_summary=running_summary.summary,
            new_text=new_text,
            summary_prompt=summary_prompt,
        )

        # Update cursor — use stable IDs without index dependency
        new_ids = set(self._get_id(msg) for msg in new_messages)

        return RunningSummary(
            summary=summary_content,
            summarized_message_ids=running_summary.summarized_message_ids | new_ids,
            last_summarized_message_id=self._get_id(new_messages[-1]),
            version=running_summary.version + 1,
        )

    async def acompute_new_summary(
        self,
        messages: list[Any],
        *,
        running_summary: RunningSummary | None = None,
        model: Any,
        summary_prompt: str = "",
    ) -> RunningSummary | None:
        """Async variant of :meth:`compute_new_summary`."""
        if running_summary is None:
            running_summary = RunningSummary()

        new_messages = self._find_new_messages(messages, running_summary)
        if not new_messages:
            return None

        new_messages = self._complete_tool_call_boundary(new_messages, messages)
        if len(new_messages) < 2:
            return None

        new_text = self._build_summary_block(new_messages)
        summary_content = await self._agenerate_summary(
            model=model,
            existing_summary=running_summary.summary,
            new_text=new_text,
            summary_prompt=summary_prompt,
        )

        new_ids = set(self._get_id(msg) for msg in new_messages)

        return RunningSummary(
            summary=summary_content,
            summarized_message_ids=running_summary.summarized_message_ids | new_ids,
            last_summarized_message_id=self._get_id(new_messages[-1]),
            version=running_summary.version + 1,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_new_messages(
        self,
        messages: list[Any],
        running_summary: RunningSummary,
    ) -> list[Any]:
        """Find messages that have NOT yet been summarized.

        Uses ``last_summarized_message_id`` as a fast-path lookup.
        Falls back to ID-set membership check if the cursor can't be found.
        """
        if not running_summary.last_summarized_message_id or not running_summary.summarized_message_ids:
            return list(messages)

        # Find the last summarized position
        resume_idx = -1
        for i, msg in enumerate(messages):
            if self._get_id(msg) == running_summary.last_summarized_message_id:
                resume_idx = i
                break

        # If we can't find the last summarized message, fall back to ID-set check
        if resume_idx < 0:
            new = []
            for msg in messages:
                if self._get_id(msg) not in running_summary.summarized_message_ids:
                    new.append(msg)
            return new

        # Everything AFTER the last summarized message is new
        return list(messages[resume_idx + 1:])

    def _complete_tool_call_boundary(
        self,
        new_messages: list[Any],
        all_messages: list[Any],
    ) -> list[Any]:
        """Ensure we don't cut between an AI tool-call and its ToolMessage responses.

        If the last new message is an AI message with tool calls, find all
        corresponding ToolMessages that appear later in ``all_messages`` and
        include them.
        """
        if not new_messages:
            return new_messages

        last_new = new_messages[-1]
        tool_calls = getattr(last_new, "tool_calls", None)
        if not tool_calls:
            return new_messages

        # Find the position of last_new in all_messages
        last_new_id = getattr(last_new, "id", None)
        new_start = 0
        if last_new_id:
            for i, msg in enumerate(all_messages):
                if getattr(msg, "id", None) == last_new_id:
                    new_start = i + 1
                    break

        # Collect ToolMessage responses for the pending tool calls
        tool_call_ids = {tc.get("id") for tc in tool_calls if tc.get("id")}
        for msg in all_messages[new_start:]:
            if hasattr(msg, "tool_call_id") and msg.tool_call_id in tool_call_ids:
                new_messages.append(msg)
                tool_call_ids.discard(msg.tool_call_id)

        return new_messages

    def _build_summary_block(self, messages: list[Any]) -> str:
        """Format messages as a text block for the summary prompt."""
        lines = []
        for msg in messages:
            role = getattr(msg, "type", "unknown")
            content = getattr(msg, "content", str(msg))
            if len(content) > 2000:
                content = content[:2000] + "..."
            lines.append(f"[{role}] {content}")
        return "\n\n".join(lines)

    def _generate_summary(
        self,
        model: Any,
        existing_summary: str,
        new_text: str,
        summary_prompt: str,
    ) -> str:
        """Call the LLM to generate or extend a summary.

        Enforces max_summary_tokens: if the model produces a summary that exceeds
        the budget, attempts to re-compress or falls back to truncation.
        """
        if not summary_prompt:
            summary_prompt = (
                "You are a context compressor. Summarize the key facts, decisions, "
                "and findings from the following conversation. Be concise but complete.\n\n"
            )

        if existing_summary:
            prompt = (
                f"{summary_prompt}\n\n"
                f"## Existing Summary\n{existing_summary}\n\n"
                f"## New Messages to Incorporate\n{new_text}\n\n"
                "Extend the existing summary by incorporating the new messages above. "
                "Return ONLY the updated summary text (no JSON, no markdown fences)."
            )
        else:
            prompt = (
                f"{summary_prompt}\n\n"
                f"## Messages to Summarize\n{new_text}\n\n"
                "Return ONLY the summary text (no JSON, no markdown fences)."
            )

        from langchain_core.messages import HumanMessage
        response = model.invoke([HumanMessage(content=prompt)])
        content = getattr(response, "content", str(response))
        content = content.strip()

        # Enforce max_summary_tokens
        content = self._enforce_token_budget(content, model, summary_prompt, existing_summary)

        return content

    def _enforce_token_budget(
        self,
        content: str,
        model: Any,
        summary_prompt: str,
        existing_summary: str,
    ) -> str:
        """Ensure the summary does not exceed max_summary_tokens.

        First tries asking the model to compress further, then falls back
        to hard truncation at token boundary.
        """
        actual_tokens = self.token_counter(content)
        if actual_tokens <= self.max_summary_tokens:
            return content

        import logging
        logger = logging.getLogger(__name__)
        logger.warning(
            f"Summary exceeds token budget: {actual_tokens} > {self.max_summary_tokens}. "
            "Attempting re-compression."
        )

        # Try re-compression via model
        try:
            from langchain_core.messages import HumanMessage
            compress_prompt = (
                f"{summary_prompt}\n\n"
                f"The following summary is {actual_tokens} tokens but must fit in "
                f"{self.max_summary_tokens} tokens. Compress it further while "
                f"preserving the key facts:\n\n{content}\n\n"
                "Return ONLY the compressed summary text."
            )
            response = model.invoke([HumanMessage(content=compress_prompt)])
            compressed = getattr(response, "content", str(response)).strip()
            compressed_tokens = self.token_counter(compressed)

            if compressed_tokens <= self.max_summary_tokens:
                return compressed

            logger.warning(
                f"Re-compression still over budget: {compressed_tokens} > {self.max_summary_tokens}. "
                "Falling back to truncation."
            )
            # Fallback: truncate to token boundary
            return self._truncate_to_token_boundary(compressed)

        except Exception:
            logger.warning("Re-compression failed; truncating summary.")
            return self._truncate_to_token_boundary(content)

    def _truncate_to_token_boundary(self, text: str) -> str:
        """Truncate text to fit within max_summary_tokens at a word boundary."""
        # Conservative estimate: 4 chars per token, back off by 10% for safety
        max_chars = int(self.max_summary_tokens * 4 * 0.9)
        if len(text) <= max_chars:
            return text
        truncated = text[:max_chars]
        # Try to break at last sentence boundary
        for sep in ["\n", ". ", "。", " "]:
            last = truncated.rfind(sep)
            if last > max_chars // 2:
                return truncated[:last + len(sep)].rstrip() + "…"
        return truncated.rstrip() + "…"

    async def _agenerate_summary(
        self,
        model: Any,
        existing_summary: str,
        new_text: str,
        summary_prompt: str,
    ) -> str:
        """Async variant of :meth:`_generate_summary`."""
        if not summary_prompt:
            summary_prompt = (
                "You are a context compressor. Summarize the key facts, decisions, "
                "and findings from the following conversation. Be concise but complete.\n\n"
            )

        if existing_summary:
            prompt = (
                f"{summary_prompt}\n\n"
                f"## Existing Summary\n{existing_summary}\n\n"
                f"## New Messages to Incorporate\n{new_text}\n\n"
                "Extend the existing summary by incorporating the new messages above. "
                "Return ONLY the updated summary text (no JSON, no markdown fences)."
            )
        else:
            prompt = (
                f"{summary_prompt}\n\n"
                f"## Messages to Summarize\n{new_text}\n\n"
                "Return ONLY the summary text (no JSON, no markdown fences)."
            )

        from langchain_core.messages import HumanMessage
        response = await model.ainvoke([HumanMessage(content=prompt)])
        content = getattr(response, "content", str(response)).strip()

        # Enforce max_summary_tokens (sync fallback — model.ainvoke already resolved)
        actual_tokens = self.token_counter(content)
        if actual_tokens > self.max_summary_tokens:
            content = self._truncate_to_token_boundary(content)

        return content

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_id(msg: Any) -> str:
        """Get stable ID for a message WITHOUT list-index dependency.

        Uses message's own .id if available, otherwise role + content +
        tool_call_id + tool name for a content-stable hash.
        """
        return _stable_message_id(msg, occurrence_key="")
