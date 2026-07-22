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
from harness.utils.llm_json import _extract_usage


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
    ) -> tuple[RunningSummary | None, dict[str, int]]:
        """Compute an incremental summary for messages, updating a running cursor.

        Returns ``(None, zero_usage)`` if no new messages need summarization,
        or ``(new_running_summary, usage)`` — ``usage`` is the real token
        cost of the LLM call(s) this invocation made (zeroed fields if none
        were made), so callers can account for compaction's own cost instead
        of it silently vanishing from token totals.

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
            return None, self._zero_usage()

        # NOTE: no domain currently does LangChain-style tool-calling inside
        # state["messages"] (search runs as a plain function call — see
        # domains/due_diligence/interview.py's _search_web), so there's no
        # AIMessage(tool_calls=...) / ToolMessage boundary to protect here.
        # If a future domain adds a ReAct-style tool-calling agent, this
        # method will need to ensure new_messages doesn't end on an AI
        # tool-call whose ToolMessage responses land just outside the slice.

        # Don't summarize too few messages
        if len(new_messages) < 2:
            return None, self._zero_usage()

        # Build new summary
        new_text = self._build_summary_block(new_messages)
        summary_content, usage = self._generate_summary(
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
        ), usage

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
    ) -> tuple[str, dict[str, int]]:
        """Call the LLM to generate or extend a summary.

        Enforces max_summary_tokens: if the model produces a summary that exceeds
        the budget, attempts to re-compress or falls back to truncation.

        Returns ``(content, usage)`` — ``usage`` sums every ``model.invoke()``
        call made in service of this one summary (the initial generation plus
        any re-compression retry).
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
        usage = _extract_usage(response)

        # Enforce max_summary_tokens
        content, extra_usage = self._enforce_token_budget(content, model, summary_prompt, existing_summary)
        usage = self._sum_usage(usage, extra_usage)

        return content, usage

    def _enforce_token_budget(
        self,
        content: str,
        model: Any,
        summary_prompt: str,
        existing_summary: str,
    ) -> tuple[str, dict[str, int]]:
        """Ensure the summary does not exceed max_summary_tokens.

        First tries asking the model to compress further, then falls back
        to hard truncation at token boundary. Returns ``(content, usage)`` —
        ``usage`` is zeroed unless a re-compression call was actually made.
        """
        actual_tokens = self.token_counter(content)
        if actual_tokens <= self.max_summary_tokens:
            return content, self._zero_usage()

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
            usage = _extract_usage(response)
            compressed = getattr(response, "content", str(response)).strip()
            compressed_tokens = self.token_counter(compressed)

            if compressed_tokens <= self.max_summary_tokens:
                return compressed, usage

            logger.warning(
                f"Re-compression still over budget: {compressed_tokens} > {self.max_summary_tokens}. "
                "Falling back to truncation."
            )
            # Fallback: truncate to token boundary
            return self._truncate_to_token_boundary(compressed), usage

        except Exception:
            logger.warning("Re-compression failed; truncating summary.")
            return self._truncate_to_token_boundary(content), self._zero_usage()

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

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _zero_usage() -> dict[str, int]:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    @staticmethod
    def _sum_usage(a: dict[str, int], b: dict[str, int]) -> dict[str, int]:
        return {k: a.get(k, 0) + b.get(k, 0) for k in ("prompt_tokens", "completion_tokens", "total_tokens")}

    @staticmethod
    def _get_id(msg: Any) -> str:
        """Get stable ID for a message WITHOUT list-index dependency.

        Uses message's own .id if available, otherwise role + content +
        tool_call_id + tool name for a content-stable hash.
        """
        return _stable_message_id(msg, occurrence_key="")
