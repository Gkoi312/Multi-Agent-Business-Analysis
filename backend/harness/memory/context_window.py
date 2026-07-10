"""
Context Window Manager — token estimation, compression triggers,
context assembly, and token budget validation.

Ensures the total token count stays within the model's context window budget.
Supports model name normalization, Chinese text fallback estimation,
parameter validation, token partitioning, batch flushing, and verification
after assembly.
"""
from __future__ import annotations

import re
import math
from dataclasses import dataclass, field
from typing import Any


# ===========================================================================
# Known model context window sizes (tokens) — normalized keys
# ===========================================================================

_MODEL_LIMITS: dict[str, int] = {
    # OpenAI
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4.1": 1_000_000,
    "gpt-4.1-mini": 1_000_000,
    "gpt-4.1-nano": 1_000_000,
    "gpt-4-turbo": 128_000,
    "gpt-3.5-turbo": 16_384,
    # Anthropic
    "claude-sonnet-5": 200_000,
    "claude-opus-4-8": 200_000,
    "claude-haiku-4-5": 200_000,
    "claude-sonnet-4": 200_000,
    "claude-opus-4": 200_000,
    "claude-haiku-3.5": 200_000,
    # DeepSeek
    "deepseek-v3": 65_536,
    "deepseek-chat": 65_536,
    "deepseek-r1": 65_536,
    "deepseek-v4": 65_536,
    # Google
    "gemini-2.0-flash": 1_048_576,
    "gemini-2.5-flash": 1_048_576,
    "gemini-2.5-pro": 1_048_576,
    "gemini-2.5-flash-lite": 1_048_576,
}


# ===========================================================================
# ContextWindowManager
# ===========================================================================


@dataclass
class ContextWindowManager:
    """Token-aware context window manager.

    Responsibilities
    ----------------
    1. Normalize model names (handle version suffixes like ``-2024-11-20``).
    2. Validate parameters (reserved_tokens < max_tokens, 0 < safe_ratio ≤ 1).
    3. Estimate token counts for text and messages (with Chinese fallback).
    4. Decide when compression/pruning is needed.
    5. Estimate tokens for ToolMessage and tool-call arguments.
    6. Support token partition budgets.
    7. Batch token release via ``token_flush_size``.
    8. Verify context after assembly; shrinkage by priority if over budget.

    Parameters
    ----------
    model_name : str
        Model identifier, e.g. ``"gpt-4o"`` or ``"gpt-4o-2024-11-20"``.
    max_tokens : int | None
        Explicit override for context window size. Falls back to lookup table,
        then to 8192.
    reserved_tokens : int
        Tokens reserved for model output (default 2000).
    safe_ratio : float
        Fraction of the window at which to trigger compression (default 0.7).
    token_flush_size : int
        Minimum number of tokens to release when flushing old context.
    """

    model_name: str = "gpt-4o"
    max_tokens: int | None = None
    reserved_tokens: int = 2000
    safe_ratio: float = 0.7
    token_flush_size: int = 2000

    # -- internal -----------------------------------------------------------

    _tiktoken_enc: Any = field(default=None, repr=False, compare=False)
    _normalized_model: str = ""

    def __post_init__(self) -> None:
        # ---- Normalize model name ----
        self._normalized_model = self._normalize_model_name(self.model_name)

        # ---- Resolve max_tokens ----
        if self.max_tokens is None:
            self.max_tokens = _MODEL_LIMITS.get(self._normalized_model, 8192)

        # ---- Parameter validation ----
        if self.reserved_tokens >= self.max_tokens:
            raise ValueError(
                f"reserved_tokens ({self.reserved_tokens}) must be less than "
                f"max_tokens ({self.max_tokens}). Reserved tokens are for "
                "model output and must leave room for input."
            )
        if not 0 < self.safe_ratio <= 1:
            raise ValueError(
                f"safe_ratio ({self.safe_ratio}) must be in range (0, 1]."
            )
        if self.token_flush_size <= 0:
            raise ValueError(
                f"token_flush_size ({self.token_flush_size}) must be positive."
            )

    # ------------------------------------------------------------------
    # Model name normalization
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_model_name(name: str) -> str:
        """Strip version suffixes like ``-2024-11-20``, ``-preview``,
        ``@latest``, ``:preview``, and minor variants.

        Examples
        --------
        - ``gpt-4o-2024-11-20`` → ``gpt-4o``
        - ``gpt-4o-mini`` → ``gpt-4o-mini``
        - ``claude-sonnet-5-20251010`` → ``claude-sonnet-5``
        - ``deepseek-chat-v3`` → ``deepseek-chat``
        """
        # Strip date suffixes: -YYYY-MM-DD, -YYYYMMDD, -YYMMDD
        name = re.sub(r"-\d{4}(?:-\d{2}){0,2}$", "", name)
        name = re.sub(r"-\d{8}$", "", name)  # YYYYMMDD
        name = re.sub(r"-\d{6}$", "", name)  # YYMMDD
        # Strip known qualifiers
        name = re.sub(r"-(?:preview|beta|alpha|rc\d+)$", "", name)
        name = re.sub(r"[@:](?:preview|beta|alpha|rc\d+)$", "", name)
        # Strip @latest
        name = re.sub(r"@latest$", "", name)
        return name

    # ------------------------------------------------------------------
    # Token estimation
    # ------------------------------------------------------------------

    def estimate_tokens(self, text: str) -> int:
        """Return an estimated token count for *text*.

        Strategy:
        1. Try ``tiktoken`` (model-aware encoding).
        2. For Chinese (CJK) text: use character count / 2 (each CJK char ≈ 1 token, but 2 chars ≈ 1 English word ≈ 1 token).
           Actually, CJK is approx 1.5 chars per token. We use len(text)//1.5.
        3. Fallback: ``len(text)//4`` for mostly-ASCII text.
        """
        if not text:
            return 0

        try:
            import tiktoken

            if self._tiktoken_enc is None:
                try:
                    self._tiktoken_enc = tiktoken.encoding_for_model(self._normalized_model)
                except Exception:
                    try:
                        self._tiktoken_enc = tiktoken.get_encoding("cl100k_base")
                    except Exception:
                        pass

            if self._tiktoken_enc is not None:
                return len(self._tiktoken_enc.encode(text))
        except Exception:
            pass

        # Fallback: detect language mix
        cjk_ratio = self._cjk_character_ratio(text)
        if cjk_ratio > 0.3:
            # CJK-dominant: ~1.5 chars per token
            return max(1, int(len(text) / 1.5))
        # Mostly ASCII/English: ~4 chars per token
        return max(1, len(text) // 4)

    def estimate_messages(self, messages: list[Any]) -> int:
        """Estimate total tokens for a list of messages.

        Includes message content, tool_call arguments, and per-message overhead.
        """
        total = 0
        for msg in messages:
            content = getattr(msg, "content", "") if hasattr(msg, "content") else str(msg)
            total += self.estimate_tokens(str(content))

            # Tool calls: estimate token cost for arguments
            tool_calls = getattr(msg, "tool_calls", None)
            if tool_calls:
                for tc in tool_calls:
                    if isinstance(tc, dict):
                        args_str = str(tc.get("args", "") or "")
                        total += self.estimate_tokens(args_str)

            # Tool message: estimate name overhead
            tool_name = getattr(msg, "name", None)
            if tool_name:
                total += self.estimate_tokens(str(tool_name))

            # Per-message overhead (~4 tokens for role markers)
            total += 4
        return total

    # ------------------------------------------------------------------
    # Language detection
    # ------------------------------------------------------------------

    @staticmethod
    def _cjk_character_ratio(text: str) -> float:
        """Return the fraction of characters that are CJK."""
        if not text:
            return 0.0
        cjk_count = 0
        for ch in text:
            cp = ord(ch)
            if (
                (0x4E00 <= cp <= 0x9FFF)   # CJK Unified Ideographs
                or (0x3400 <= cp <= 0x4DBF)  # CJK Ext-A
                or (0x20000 <= cp <= 0x2A6DF)  # CJK Ext-B
                or (0xF900 <= cp <= 0xFAFF)  # CJK Compat
                or (0x3040 <= cp <= 0x309F)  # Hiragana
                or (0x30A0 <= cp <= 0x30FF)  # Katakana
                or (0xAC00 <= cp <= 0xD7AF)  # Hangul
            ):
                cjk_count += 1
        return cjk_count / max(len(text), 1)

    # ------------------------------------------------------------------
    # Compression / pruning trigger
    # ------------------------------------------------------------------

    @property
    def safe_limit(self) -> int:
        """The token count at which we should trigger compression (public)."""
        return int((self.max_tokens - self.reserved_tokens) * self.safe_ratio)

    def _safe_limit(self) -> int:
        """Deprecated: use ``safe_limit`` property instead."""
        return self.safe_limit

    def should_compress(
        self,
        messages: list[Any],
        system_prompt: str = "",
        working_memory_str: str = "",
        compressed_turns_str: str = "",
    ) -> bool:
        """Return ``True`` when the current context exceeds the safe threshold."""
        total = self.estimate_tokens(system_prompt)
        total += self.estimate_tokens(working_memory_str)
        total += self.estimate_tokens(compressed_turns_str)
        total += self.estimate_messages(messages)
        return total > self._safe_limit()

    # ------------------------------------------------------------------
    # Context assembly verification & shrinkage
    # ------------------------------------------------------------------

    def current_usage_estimate(
        self,
        messages: list[Any],
        system_prompt: str = "",
        working_memory_str: str = "",
        compressed_turns_str: str = "",
    ) -> dict[str, int]:
        """Return a breakdown of estimated token usage."""
        sp = self.estimate_tokens(system_prompt)
        wm = self.estimate_tokens(working_memory_str)
        ct = self.estimate_tokens(compressed_turns_str)
        msgs = self.estimate_messages(messages)
        return {
            "system_prompt": sp,
            "working_memory": wm,
            "compressed_turns": ct,
            "messages": msgs,
            "total": sp + wm + ct + msgs,
            "safe_limit": self._safe_limit(),
            "max_tokens": self.max_tokens,
        }

    def verify_assembly(
        self,
        system_prompt: str,
        research_summary: str,
        working_memory_str: str,
        recent_messages: list[Any],
        search_digest_str: str = "",
        long_term_facts_str: str = "",
    ) -> bool:
        """Verify that assembled context fits within the safe limit."""
        total = (
            self.estimate_tokens(system_prompt)
            + self.estimate_tokens(research_summary)
            + self.estimate_tokens(working_memory_str)
            + self.estimate_messages(recent_messages)
            + self.estimate_tokens(search_digest_str)
            + self.estimate_tokens(long_term_facts_str)
        )
        return total <= self._safe_limit()

    # ------------------------------------------------------------------
    # Batch flush helpers
    # ------------------------------------------------------------------

    def find_flush_cutoff(
        self,
        messages: list[Any],
        target_tokens: int | None = None,
    ) -> int:
        """Find a safe cutoff index to release at least ``target_tokens``
        worth of messages (defaults to ``token_flush_size``).

        Returns the index after which messages should be kept.
        Never splits AI tool-call / ToolMessage pairs.

        Parameters
        ----------
        messages : list[Any]
            Full message list.
        target_tokens : int | None
            Tokens to release; defaults to ``self.token_flush_size``.

        Returns
        -------
        int
            Index of the first message to KEEP (all before this are flushed).
        """
        if target_tokens is None:
            target_tokens = self.token_flush_size

        accumulated = 0
        cutoff = 0

        for i, msg in enumerate(messages):
            if accumulated >= target_tokens:
                # Check safety: don't split tool call boundaries
                safe = self._find_safe_cutoff(messages, i)
                return safe
            content = str(getattr(msg, "content", str(msg)))
            accumulated += self.estimate_tokens(content) + 4
            cutoff = i + 1

        return cutoff

    @staticmethod
    def _find_safe_cutoff(messages: list[Any], candidate: int) -> int:
        """Advance cutoff past ToolMessage blocks to avoid splitting pairs.

        If cutting at ``candidate`` would split an AI tool_call from its
        ToolMessage responses, advance until all tool responses are included.
        """
        if candidate >= len(messages):
            return candidate

        try:
            from langchain_core.messages import ToolMessage, AIMessage
        except ImportError:
            return candidate

        idx = candidate
        # Collect tool_call_ids from the messages being cut
        tool_call_ids = set()
        for msg in messages[candidate:]:
            if isinstance(msg, ToolMessage) and msg.tool_call_id:
                tool_call_ids.add(msg.tool_call_id)

        if not tool_call_ids:
            return candidate

        # Search backward for AI messages with these tool calls
        for i in range(candidate - 1, -1, -1):
            msg = messages[i]
            if isinstance(msg, AIMessage) and msg.tool_calls:
                ai_ids = {tc.get("id") for tc in msg.tool_calls if tc.get("id")}
                if tool_call_ids & ai_ids:
                    return i  # Cut before the AI message that spawned these tools

        return candidate
