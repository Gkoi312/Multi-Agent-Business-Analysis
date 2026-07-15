"""
Context Assembler — builds the projected LLM context from canonical state
without mutating the original checkpoint data.

This is the bridge between "what the agent knows" (checkpoint state) and
"what the model sees" (projected context).  The raw messages, facts, and
search results in the checkpoint are NEVER modified — only the projection
changes.

STRICT BUDGET: ``assemble()`` either returns a result within budget OR
raises ``ContextBudgetExceeded``.  Never returns a known-over-budget result.
"""
from __future__ import annotations

from typing import Any

from harness.models.memory import (
    ContextAssemblyResult,
    ContextBudgetExceeded,
    CompressedTurn,
)
from harness.memory.policies import TokenBudget
from harness.memory.context_editing import ToolContextPruner
from harness.memory.context_window import ContextWindowManager


# ===========================================================================
# ContextAssembler
# ===========================================================================


class ContextAssembler:
    """Assembles the context payload for an LLM call from canonical state.

    Responsibilities
    ---------------
    1. Build a system-prompt block from domain memory, skill cards, etc.
    2. Inject compressed research progress (from ``CompressedTurn`` history).
    3. Inject current ``WorkingMemory`` content.
    4. Include recent raw messages (pruned of old tool results if needed).
    5. Include current search digest (if any).
    6. Include retrieved long-term facts (if any).
    7. Include execution/running summary.
    8. Validate that the assembled context fits within budget.
    9. Shrink by priority if over budget; raise ContextBudgetExceeded if impossible.

    Parameters
    ----------
    token_budget : TokenBudget
        Per-segment token allocation.
    window_mgr : ContextWindowManager
        Token estimation and budget validation.
    tool_pruner : ToolContextPruner | None
        Optional tool result pruning before assembly.
    """

    def __init__(
        self,
        token_budget: TokenBudget | None = None,
        window_mgr: ContextWindowManager | None = None,
        tool_pruner: ToolContextPruner | None = None,
    ):
        self.budget = token_budget or TokenBudget()
        self.window_mgr = window_mgr or ContextWindowManager()
        self.tool_pruner = tool_pruner

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def assemble(
        self,
        *,
        messages: list[Any],
        system_prompt: str = "",
        compressed_turns: list[CompressedTurn] | None = None,
        working_memory_str: str = "",
        search_digest_str: str = "",
        long_term_facts_str: str = "",
        domain_memory_str: str = "",
        skill_card_str: str = "",
        assigned_plan_str: str = "",
        execution_summary: str = "",
        running_summary: str = "",
    ) -> ContextAssemblyResult:
        """Build the full context payload for an LLM call.

        Either returns a result within the safe token limit, or raises
        ``ContextBudgetExceeded``.

        Parameters
        ----------
        messages : list[Any]
            The canonical message list from graph state.
        system_prompt : str
            The base system prompt (analyst/expert role instruction).
        compressed_turns : list[CompressedTurn] | None
            Accumulated compressed turn history.
        working_memory_str : str
            Formatted working memory content.
        search_digest_str : str
            Current search results digest.
        long_term_facts_str : str
            Retrieved long-term fact content.
        domain_memory_str : str
            Domain-specific knowledge / frameworks.
        skill_card_str : str
            Skill card content.
        assigned_plan_str : str
            Assigned research plan content.
        execution_summary : str
            Execution/history compaction summary.
        running_summary : str
            Running summary text.

        Returns
        -------
        ContextAssemblyResult

        Raises
        ------
        ContextBudgetExceeded
            If context cannot fit within budget after all shrink strategies.
        """
        safe_limit = self.window_mgr.safe_limit

        # 1. Build the enriched system prompt
        enriched_sp = self._build_system_prompt(
            base=system_prompt,
            domain_memory=domain_memory_str,
            skill_card=skill_card_str,
            assigned_plan=assigned_plan_str,
        )
        enriched_sp = self._truncate_to_budget(enriched_sp, self.budget.system_prompt)

        # 2. Build research summary from compressed turns
        research_summary = self._build_research_summary(
            compressed_turns=compressed_turns or [],
            budget=self.budget.research_summary,
        )

        # 3. Working memory
        wm = self._truncate_to_budget(working_memory_str, self.budget.working_memory)

        # 4. Recent raw messages (after optional tool pruning)
        recent = self._prepare_recent_messages(
            messages=messages,
            budget=self.budget.recent_messages,
        )

        # 5. Search digest
        sd = self._truncate_to_budget(search_digest_str, self.budget.search_digest)

        # 6. Long-term facts
        ltf = self._truncate_to_budget(long_term_facts_str, self.budget.long_term_facts)

        # 7. Execution/running summary
        exec_summary = execution_summary or running_summary
        exec_summary = self._truncate_to_budget(exec_summary, self.budget.execution_summary)

        # 8. Compute total tokens and validate
        breakdown = {
            "system_prompt": self.window_mgr.estimate_tokens(enriched_sp),
            "research_summary": self.window_mgr.estimate_tokens(research_summary),
            "working_memory": self.window_mgr.estimate_tokens(wm),
            "recent_messages": self.window_mgr.estimate_messages(recent),
            "search_digest": self.window_mgr.estimate_tokens(sd),
            "long_term_facts": self.window_mgr.estimate_tokens(ltf),
            "execution_summary": self.window_mgr.estimate_tokens(exec_summary),
        }
        total = sum(breakdown.values())

        # 9. Shrink by priority if over budget
        if total > safe_limit:
            recent, sd, ltf, wm, exec_summary, research_summary = self._shrink_by_priority(
                recent, sd, ltf, wm, exec_summary, research_summary,
                total, safe_limit,
            )
            # Recompute
            breakdown = {
                "system_prompt": self.window_mgr.estimate_tokens(enriched_sp),
                "research_summary": self.window_mgr.estimate_tokens(research_summary),
                "working_memory": self.window_mgr.estimate_tokens(wm),
                "recent_messages": self.window_mgr.estimate_messages(recent),
                "search_digest": self.window_mgr.estimate_tokens(sd),
                "long_term_facts": self.window_mgr.estimate_tokens(ltf),
                "execution_summary": self.window_mgr.estimate_tokens(exec_summary),
            }
            total = sum(breakdown.values())

        # 10. Final verification — raise if still over budget
        if total > safe_limit:
            raise ContextBudgetExceeded(current_tokens=total, safe_limit=safe_limit)

        return ContextAssemblyResult(
            system_prompt=enriched_sp,
            execution_summary=exec_summary,
            research_summary=research_summary,
            working_memory=wm,
            recent_raw_messages=recent,
            current_search_digest=sd,
            retrieved_long_term_facts=ltf,
            total_tokens=total,
            token_breakdown=breakdown,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_system_prompt(
        self,
        base: str,
        domain_memory: str = "",
        skill_card: str = "",
        assigned_plan: str = "",
    ) -> str:
        """Enrich the base system prompt with domain/skill/plan context."""
        parts = [base]
        if domain_memory:
            parts.append(f"\n## Domain Knowledge\n{domain_memory}")
        if skill_card:
            parts.append(f"\n## Skill Card\n{skill_card}")
        if assigned_plan:
            parts.append(f"\n## Research Plan\n{assigned_plan}")
        return "\n".join(parts)

    def _build_research_summary(
        self,
        compressed_turns: list[CompressedTurn],
        budget: int,
    ) -> str:
        """Format compressed turns into a budget-constrained research summary."""
        if not compressed_turns:
            return ""
        lines = [f"## Research Progress ({len(compressed_turns)} turns)\n"]
        chars = 0
        for i, turn in enumerate(reversed(compressed_turns[-5:])):
            turn_text = f"### Turn {len(compressed_turns) - i}\n{turn.format()}\n"
            if chars + len(turn_text) > budget * 4:
                break
            lines.append(turn_text)
            chars += len(turn_text)
        return "\n".join(lines)

    def _prepare_recent_messages(
        self,
        messages: list[Any],
        budget: int,
    ) -> list[Any]:
        """Prepare recent messages for context injection.

        Applies tool pruning if a pruner is configured.
        Handles single oversized messages.
        """
        recent = self._select_recent(messages, budget)

        # Handle single oversized message: truncate its content
        recent = self._handle_oversized_messages(recent, budget)

        # Prune old tool results if configured
        if self.tool_pruner:
            recent, _ = self.tool_pruner.prune(recent)

        return recent

    def _select_recent(
        self,
        messages: list[Any],
        max_tokens: int,
    ) -> list[Any]:
        """Select the most recent messages fitting within ``max_tokens``.

        Never deletes the current user message (last HumanMessage).
        Always respects the min_current_turn budget.
        """
        if not messages:
            return []

        selected: list[Any] = []
        total = 0
        min_reserve = self.budget.min_current_turn

        # Always keep the last message (current user input)
        last_msg = messages[-1]
        last_tokens = self.window_mgr.estimate_tokens(
            str(getattr(last_msg, "content", str(last_msg)))
        ) + 4

        # Reserve at least min_current_turn for it
        effective_max = max(max_tokens - max(last_tokens, min_reserve), last_tokens)

        for msg in reversed(messages[:-1]):
            content = str(getattr(msg, "content", str(msg)))
            tokens = self.window_mgr.estimate_tokens(content) + 4
            # Include tool_call args in token estimate
            tool_calls = getattr(msg, "tool_calls", None)
            if tool_calls:
                for tc in tool_calls:
                    if isinstance(tc, dict):
                        tokens += self.window_mgr.estimate_tokens(str(tc.get("args", "") or ""))

            if total + tokens > effective_max and selected:
                break
            selected.insert(0, msg)
            total += tokens

        selected.append(last_msg)
        return selected

    def _handle_oversized_messages(
        self,
        messages: list[Any],
        max_tokens: int,
    ) -> list[Any]:
        """Truncate individual messages that are too large."""
        result = []
        for msg in messages:
            content = str(getattr(msg, "content", str(msg)))
            tokens = self.window_mgr.estimate_tokens(content)
            if tokens > max_tokens:
                # Truncate to fit within budget
                max_chars = max_tokens * 4
                truncated_content = content[:max_chars] + "\n... [truncated: oversized message]"
                try:
                    new_msg = msg.model_copy(update={"content": truncated_content})
                    result.append(new_msg)
                except Exception:
                    # Fallback: keep original if model_copy fails
                    result.append(msg)
            else:
                result.append(msg)
        return result

    def _shrink_by_priority(
        self,
        recent: list[Any],
        sd: str,
        ltf: str,
        wm: str,
        exec_summary: str,
        research_summary: str,
        current_total: int,
        target: int,
    ) -> tuple[list[Any], str, str, str, str, str]:
        """Shrink context segments by priority to fit within budget.

        Priority removal order (least important first):
        1. Long-term facts
        2. Search digest
        3. Working memory display
        4. Execution summary
        5. Research summary
        6. Old messages in recent list
        7. Last resort: truncate oversized recent messages (but never delete last)
        """
        over = current_total - target
        if over <= 0:
            return recent, sd, ltf, wm, exec_summary, research_summary

        # 1. Truncate long-term facts
        if ltf and over > 0:
            before = self.window_mgr.estimate_tokens(ltf)
            ltf = self._truncate_to_budget(ltf, max(0, before - over))
            after = self.window_mgr.estimate_tokens(ltf)
            over -= (before - after)

        # 2. Truncate search digest
        if sd and over > 0:
            before = self.window_mgr.estimate_tokens(sd)
            sd = self._truncate_to_budget(sd, max(0, before - over))
            after = self.window_mgr.estimate_tokens(sd)
            over -= (before - after)

        # 3. Truncate working memory
        if wm and over > 0:
            before = self.window_mgr.estimate_tokens(wm)
            wm = self._truncate_to_budget(wm, max(0, before - over))
            after = self.window_mgr.estimate_tokens(wm)
            over -= (before - after)

        # 4. Truncate execution summary
        if exec_summary and over > 0:
            before = self.window_mgr.estimate_tokens(exec_summary)
            exec_summary = self._truncate_to_budget(exec_summary, max(0, before - over))
            after = self.window_mgr.estimate_tokens(exec_summary)
            over -= (before - after)

        # 5. Truncate research summary
        if research_summary and over > 0:
            before = self.window_mgr.estimate_tokens(research_summary)
            research_summary = self._truncate_to_budget(research_summary, max(0, before - over))
            after = self.window_mgr.estimate_tokens(research_summary)
            over -= (before - after)

        # 6. Drop oldest messages (except the last one — current user input)
        if recent and len(recent) > 1 and over > 0:
            while len(recent) > 1 and over > 0:
                dropped = recent.pop(0)
                content = str(getattr(dropped, "content", str(dropped)))
                dropped_tokens = self.window_mgr.estimate_tokens(content) + 4
                tool_calls = getattr(dropped, "tool_calls", None)
                if tool_calls:
                    for tc in tool_calls:
                        if isinstance(tc, dict):
                            dropped_tokens += self.window_mgr.estimate_tokens(
                                str(tc.get("args", "") or "")
                            )
                over -= dropped_tokens

        return recent, sd, ltf, wm, exec_summary, research_summary

    @staticmethod
    def _truncate_to_budget(text: str, max_tokens: int) -> str:
        """Simple character-based truncation (4 chars ≈ 1 token)."""
        if not text:
            return ""
        max_chars = max(0, max_tokens * 4)
        if max_chars == 0:
            return ""
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + "\n... [truncated]"
