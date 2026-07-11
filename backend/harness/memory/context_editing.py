"""
Context Editing — model-agnostic tool result pruning.

Based on LangChain's ``ClearToolUsesEdit`` pattern:
- Triggered when total token count exceeds a threshold.
- Replaces old ToolMessage content with a placeholder (never deletes).
- Preserves tool_call_id and metadata.
- Marks cleared messages to avoid re-processing.
- Does NOT modify checkpoint state — operates on a copy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from harness.memory.policies import ToolPruneConfig
from harness.models.memory import ToolPruneResult


# ===========================================================================
# ToolContextPruner
# ===========================================================================


class ToolContextPruner:
    """Prunes old tool results from a message list to free context.

    Operates on a **copy** of the message list — never mutates the
    canonical state.

    Parameters
    ----------
    config : ToolPruneConfig
        Configuration controlling when and how to prune.
    token_counter : Callable
        Function that estimates token counts for messages.
    """

    def __init__(
        self,
        config: ToolPruneConfig | None = None,
        token_counter: Callable[[Any], int] | None = None,
    ):
        self.config = config or ToolPruneConfig()
        self.token_counter = token_counter or (lambda x: max(1, len(str(x)) // 4))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def prune(self, messages: list[Any]) -> tuple[list[Any], ToolPruneResult]:
        """Apply pruning to a copy of ``messages``.

        Returns ``(pruned_messages, result)``.

        The original ``messages`` list is NOT mutated.
        """
        import copy
        edited = copy.deepcopy(list(messages))

        tokens_before = sum(self.token_counter(m) for m in edited)
        result = ToolPruneResult(
            messages_before=len(edited),
            tokens_before=tokens_before,
        )

        if tokens_before <= self.config.trigger_tokens:
            result.messages_after = len(edited)
            result.tokens_after = tokens_before
            return edited, result

        # Find candidate ToolMessages (oldest first)
        candidates = self._find_candidates(edited)

        if not candidates:
            result.messages_after = len(edited)
            result.tokens_after = tokens_before
            return edited, result

        # Track cleared tool_call_ids for clear_tool_inputs
        cleared_tool_call_ids: set[str] = set()

        # Prune from oldest to newest
        for idx, tool_msg in candidates:
            if self.config.reclaim_at_least_tokens > 0:
                current = sum(self.token_counter(m) for m in edited)
                if (tokens_before - current) >= self.config.reclaim_at_least_tokens:
                    break

            # Skip already cleared
            resp_meta = getattr(tool_msg, "response_metadata", {}) or {}
            if resp_meta.get("context_editing", {}).get("cleared"):
                continue

            # Skip excluded tools
            tool_name = getattr(tool_msg, "name", None)
            if tool_name and tool_name in self.config.excluded_tools:
                continue

            # Record tool_call_id before clearing
            tc_id = getattr(tool_msg, "tool_call_id", None)
            if tc_id:
                cleared_tool_call_ids.add(str(tc_id))

            # Replace with placeholder
            edited[idx] = self._build_pruned_message(tool_msg)
            result.tools_cleared += 1

        # If clear_tool_inputs, also clear the originating AI tool-call args
        if self.config.clear_tool_inputs and cleared_tool_call_ids:
            edited = self._clear_tool_inputs(edited, cleared_tool_call_ids)

        result.messages_after = len(edited)
        result.tokens_after = sum(self.token_counter(m) for m in edited)
        result.tokens_reclaimed = max(0, tokens_before - result.tokens_after)
        return edited, result

    def _find_candidates(self, messages: list[Any]) -> list[tuple[int, Any]]:
        """Find prune-eligible ToolMessages, oldest first.

        FIXED: when len(candidates) <= keep_recent_tool_results, return empty
        list (no clearing). Only clear the excess when there are more
        candidates than the keep threshold.
        """
        candidates: list[tuple[int, Any]] = []
        tool_class = self._get_tool_message_class()

        for idx, msg in enumerate(messages):
            if isinstance(msg, tool_class):
                candidates.append((idx, msg))

        keep = self.config.keep_recent_tool_results

        # FIXED: if we have <= keep candidates, clear NONE of them
        if len(candidates) <= keep:
            return []

        # Keep the most recent `keep` results, clear the older ones
        return candidates[:len(candidates) - keep]

    def _build_pruned_message(self, tool_msg: Any) -> Any:
        """Replace a ToolMessage's content with placeholder, preserving metadata."""
        tool_class = type(tool_msg)

        resp_meta = dict(getattr(tool_msg, "response_metadata", {}) or {})
        resp_meta["context_editing"] = {
            "cleared": True,
            "strategy": "tool_pruner",
            "original_tool_call_id": getattr(tool_msg, "tool_call_id", None),
        }

        try:
            return tool_class(
                content=self.config.placeholder,
                tool_call_id=getattr(tool_msg, "tool_call_id", ""),
                name=getattr(tool_msg, "name", None),
                response_metadata=resp_meta,
            )
        except Exception:
            # Fallback: model_copy
            return tool_msg.model_copy(
                update={
                    "content": self.config.placeholder,
                    "artifact": None,
                    "response_metadata": resp_meta,
                }
            )

    def _clear_tool_inputs(
        self,
        messages: list[Any],
        cleared_tool_call_ids: set[str],
    ) -> list[Any]:
        """Clear tool-call args on AI messages for pruned tool results.

        Preserves tool_call ID, name, and call relationship;
        replaces args with minimal placeholder.
        """
        ai_class = self._get_ai_message_class()

        for idx, msg in enumerate(messages):
            if not isinstance(msg, ai_class):
                continue
            tool_calls = getattr(msg, "tool_calls", None)
            if not tool_calls:
                continue

            modified = False
            new_tool_calls = []
            for tc in tool_calls:
                if isinstance(tc, dict):
                    tc_id = str(tc.get("id", ""))
                    if tc_id in cleared_tool_call_ids:
                        new_tc = dict(tc)
                        new_tc["args"] = {"_cleared": True}
                        modified = True
                        new_tool_calls.append(new_tc)
                    else:
                        new_tool_calls.append(tc)
                else:
                    new_tool_calls.append(tc)

            if modified:
                try:
                    messages[idx] = msg.model_copy(update={"tool_calls": new_tool_calls})
                except Exception:
                    pass

        return messages

    @staticmethod
    def _get_tool_message_class() -> type:
        """Import ToolMessage lazily to avoid hard dependency."""
        from langchain_core.messages import ToolMessage
        return ToolMessage

    @staticmethod
    def _get_ai_message_class() -> type:
        """Import AIMessage lazily."""
        from langchain_core.messages import AIMessage
        return AIMessage
