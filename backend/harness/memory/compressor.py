"""
Incremental Compressor — turn-by-turn structured compression for interview loops.

Each interview turn (question + search results + answer) is compressed into a
``CompressedTurn``.  Multiple compressed turns are merged into a ``MergedMemory``
that tracks coverage, gaps, and risk signals — all without replaying raw messages.

Key separation:
- ``compress_completed_turn()`` — runs EVERY turn to extract structured facts.
- ``should_compact_history()`` — checks if context pressure triggers compaction.
- ``compact_history()`` — summarizes older messages into a summary block.
- ``assemble_context()`` — builds the projected context for the next LLM call.
"""
from __future__ import annotations

import logging
from typing import Any

from harness.memory.context_window import ContextWindowManager
from harness.memory.history_compactor import HistoryCompactor
from harness.memory.policies import CompactionPolicy, MemoryDomainConfig
from harness.models.memory import (
    CompressedTurn,
    MemoryFact,
    TokenCounter,
    _extract_json,
    evidence_quality_from_sources,
)

logger = logging.getLogger(__name__)


# ===========================================================================
# Compression prompt — structured facts output
# ===========================================================================

_COMPRESS_TURN_PROMPT = """\
You are a structured data extractor. Compress the following analyst-expert Q&A round
into a concise JSON summary. Extract ONLY facts stated in the answer — do not invent,
infer, or elaborate.

Analyst question:
{question}

Expert answer:
{answer}

IMPORTANT — Source Registry:
The following sources were used for this round. Reference them ONLY by their
short IDs (S1, S2, etc.) in the source_ids field. DO NOT generate URLs.
{source_registry_block}

Return ONLY a JSON object (no markdown fences, no extra text) with these keys:
{{
    "question_intent": "what the analyst was really trying to figure out (one sentence)",
    "facts": [
        {{
            "text": "concrete self-contained fact sentence",
            "primary_category": "{categories}",
            "subject": "what this fact is about",
            "predicate": "the property or attribute being stated",
            "value": "the asserted numeric or string value",
            "unit": "unit of measurement if any (%, USD, etc.)",
            "period": "time period if any (2024, Q1 2025, etc.)",
            "confidence": 0.0,
            "source_ids": ["S1", "S2"]
        }}
    ],
    "numbers_mentioned": [{{"value": "42", "unit": "%", "context": "market share"}}],
    "unanswered": ["what this round did NOT resolve"]
}}

Rules:
- facts: at most 8, each a self-contained sentence with structured SPDV fields.
- primary_category: exactly ONE per fact from: {categories}
- source_ids: ONLY use keys from the source registry above (S1, S2, etc.).
- DO NOT generate URLs, DO NOT generate new source IDs.
- unanswered: list specific questions not resolved, or empty array if fully answered.
- numbers_mentioned: only include if the answer contains explicit numeric data."""


# ===========================================================================
# IncrementalCompressor
# ===========================================================================


class IncrementalCompressor:
    """Turn-by-turn incremental compressor for interview/research loops.

    Provides a clear API boundary between:

    - **Turn Fact Extraction** (``compress_completed_turn``) — runs every turn.
    - **History Compaction** (``should_compact_history``, ``compact_history``) —
      only when the context window is under pressure.

    Parameters
    ----------
    llm :
        The LLM to use for compression.  Should be a **cheap, fast model**
        (e.g. ``gpt-4o-mini``, ``deepseek-chat``) — compression is extraction,
        not reasoning.  Must support ``.invoke(messages)`` and optionally
        ``.ainvoke(messages)``.
    window_manager : ContextWindowManager | None
        Token-aware window manager for compression/compaction triggers.
    max_retries : int
        How many times to retry on JSON parse failure (default 2).
    history_compactor : HistoryCompactor | None
        Optional dedicated history compactor. Created lazily if not provided.
    compaction_policy : CompactionPolicy | None
        Policy for history compaction triggers and thresholds.
    domain_config : MemoryDomainConfig | None
        Domain-specific config for categories and aliases.
    """

    def __init__(
        self,
        llm: Any,
        window_manager: ContextWindowManager | None = None,
        max_retries: int = 2,
        history_compactor: HistoryCompactor | None = None,
        compaction_policy: CompactionPolicy | None = None,
        domain_config: MemoryDomainConfig | None = None,
    ):
        self.llm = llm
        self.window_manager = window_manager or ContextWindowManager()
        self.max_retries = max_retries
        self._token_counter = TokenCounter(self.window_manager)
        self._history_compactor = history_compactor
        self._compaction_policy = compaction_policy or CompactionPolicy()
        self._domain_config = domain_config

    @property
    def categories_str(self) -> str:
        """Comma-separated category list for prompt injection."""
        if self._domain_config:
            cats = list(self._domain_config.categories) + [self._domain_config.fallback_category]
            return "|".join(cats)
        return "business_model|growth|risk|competition|financials|other"

    # ==================================================================
    # A. Turn Fact Extraction (runs every turn)
    # ==================================================================

    def compress_completed_turn(
        self,
        question: str,
        answer: str,
        *,
        source_registry: dict[str, Any] | None = None,
    ) -> CompressedTurn:
        """Compress one Q&A round into a ``CompressedTurn``.

        Runs EVERY turn after the expert answer is generated.  This is the
        structured fact extraction path — NOT history compaction.

        Parameters
        ----------
        question : str
            The analyst's question text.
        answer : str
            The expert's full answer text.
        source_registry : dict | None
            Mapping of source_id (S1, S2) → SourceRecord. Models see only IDs.

        Returns
        -------
        CompressedTurn
        """
        # Build source registry block for prompt
        registry = source_registry or {}

        registry_lines = []
        for sid, rec in registry.items():
            if hasattr(rec, "title"):
                registry_lines.append(f"  {sid}: {rec.title}")
            elif isinstance(rec, dict):
                registry_lines.append(f"  {sid}: {rec.get('title', '')}")
            else:
                registry_lines.append(f"  {sid}: {str(rec)}")

        source_registry_block = "\n".join(registry_lines) if registry_lines else "[No sources registered]"

        prompt = _COMPRESS_TURN_PROMPT.format(
            question=question[:2000],
            answer=answer[:4000],
            categories=self.categories_str,
            source_registry_block=source_registry_block,
        )

        for attempt in range(self.max_retries + 1):
            try:
                from langchain_core.messages import HumanMessage

                response = self.llm.invoke([HumanMessage(content=prompt)])
                data = _extract_json(response.content)

                if data:
                    return self._parse_compressed_turn(data, registry)

                if attempt < self.max_retries:
                    continue

            except Exception as exc:
                logger.warning(
                    f"Compression attempt {attempt + 1}/{self.max_retries + 1} failed: {exc}"
                )
                if attempt < self.max_retries:
                    continue

                return CompressedTurn(
                    question_intent=question[:200],
                    key_findings=[answer[:500]] if answer else [],
                    evidence_quality="low",
                    unanswered=[],
                    compression_error=f"Compression failed after {self.max_retries + 1} attempts: {type(exc).__name__}",
                )

        return CompressedTurn(
            question_intent=question[:200],
            key_findings=[answer[:500]] if answer else [],
            evidence_quality="low",
            unanswered=[],
            compression_error="LLM returned empty JSON after all retries",
        )

    # ==================================================================
    # B. History Compaction (only under pressure)
    # ==================================================================

    def should_compact_history(
        self,
        messages: list[Any],
        *,
        turn_count: int = 0,
        working_memory_str: str = "",
        compressed_turns_str: str = "",
    ) -> bool:
        """Check whether history compaction should run.

        No ``system_prompt`` budget is added here: this check runs between
        rounds, before the router picks the next node, so which system
        prompt (and its length) will actually be used next isn't known yet.
        """
        # Fast path: context window check
        if self.window_manager is not None:
            token_pressure = self.window_manager.should_compress(
                messages=messages,
                working_memory_str=working_memory_str,
                compressed_turns_str=compressed_turns_str,
            )
            if token_pressure:
                return True

        # Secondary check: history compactor thresholds
        if self._get_history_compactor() is not None:
            extra = (
                self.window_manager.estimate_tokens(working_memory_str)
                + self.window_manager.estimate_tokens(compressed_turns_str)
            )
            return self._get_history_compactor().should_compact(
                messages, turn_count=turn_count, extra_tokens=extra
            )

        return False

    def compact_history(
        self,
        messages: list[Any],
        *,
        running_summary=None,
    ):
        """Compact older messages into a summary block."""
        compactor = self._get_history_compactor()
        if compactor and compactor.model is not None:
            return compactor.compact_history(
                messages, running_summary=running_summary
            )
        return list(messages), running_summary

    # ==================================================================
    # Helpers for the interview graph
    # ==================================================================

    @staticmethod
    def extract_last_question_and_answer(
        messages: list[Any],
    ) -> tuple[str, str]:
        """Return (question, answer) strings from the last two messages."""
        question = ""
        answer = ""
        if len(messages) >= 2:
            q_msg = messages[-2]
            a_msg = messages[-1]
            question = getattr(q_msg, "content", str(q_msg))
            answer = getattr(a_msg, "content", str(a_msg))
        elif len(messages) == 1:
            answer = getattr(messages[-1], "content", str(messages[-1]))
        return question, answer

    @staticmethod
    def format_compressed_turns(turns: list[CompressedTurn]) -> str:
        """Format a list of compressed turns as a compact context block."""
        if not turns:
            return ""
        lines = [f"## Previous {len(turns)} research round(s) (compressed)\n"]
        for i, turn in enumerate(turns, 1):
            lines.append(f"### Round {i}")
            lines.append(turn.format())
            lines.append("")
        return "\n".join(lines)

    # ==================================================================
    # Internal
    # ==================================================================

    def _get_history_compactor(self) -> HistoryCompactor | None:
        """Lazily create the history compactor if a model is available."""
        if self._history_compactor is None:
            self._history_compactor = HistoryCompactor(
                policy=self._compaction_policy,
                token_counter=self._token_counter,
                model=getattr(self, "llm", None),
            )
        return self._history_compactor

    def _parse_compressed_turn(
        self,
        data: dict[str, Any],
        source_registry: dict[str, Any] | None = None,
    ) -> CompressedTurn:
        """Parse model output into a CompressedTurn with structured facts.

        Uses ONLY the source registry keys (S1, S2, etc.) for source_ids.
        Model-generated URLs are rejected. Unknown source IDs are dropped
        with a validation warning.
        """
        registry = source_registry or {}
        valid_sids = set(registry.keys()) if registry else set()

        # Parse facts
        facts_raw = data.get("facts") or []
        facts: list[MemoryFact] = []
        for fdata in facts_raw:
            if not isinstance(fdata, dict):
                continue
            text = str(fdata.get("text", "") or "")
            if not text:
                continue

            category = str(fdata.get("primary_category", "other") or "other")
            if self._domain_config:
                category = self._domain_config.fallback_category if category not in self._domain_config.all_categories else category
            elif category not in {"business_model", "growth", "risk", "competition", "financials", "other"}:
                category = "other"

            # Map source IDs: ONLY accept registry keys (S1, S2, ...)
            source_ids: list[str] = []
            for sid_raw in (fdata.get("source_ids") or []):
                sid_str = str(sid_raw).strip()
                if not sid_str:
                    continue
                if sid_str.startswith("http://") or sid_str.startswith("https://"):
                    # Model returned URL — reject, log warning
                    logger.warning(
                        f"Model returned URL in source_ids: '{sid_str[:80]}'. "
                        f"Only registry keys (S1, S2, ...) are accepted. Skipping."
                    )
                    continue
                if sid_str in valid_sids:
                    source_ids.append(sid_str)
                else:
                    logger.warning(
                        f"Unknown source ID '{sid_str}' not in registry. "
                        f"Valid IDs: {sorted(valid_sids)}. Skipping."
                    )

            # Evidence quality is derived mechanically from independent
            # source count, not judged by the LLM — it's arithmetic on data
            # the compressor already extracts, not a separate reasoning task.
            evidence_quality = evidence_quality_from_sources(source_ids)

            facts.append(MemoryFact(
                text=text,
                primary_category=category,
                subject=str(fdata.get("subject", "") or ""),
                predicate=str(fdata.get("predicate", "") or ""),
                value=fdata.get("value"),
                unit=str(fdata.get("unit")) if fdata.get("unit") else None,
                period=str(fdata.get("period")) if fdata.get("period") else None,
                evidence_quality=evidence_quality,
                confidence=float(fdata["confidence"]) if fdata.get("confidence") is not None else None,
                source_ids=source_ids,
            ))

        # Parse numbers
        numbers = []
        for n in (data.get("numbers_mentioned") or []):
            if isinstance(n, dict):
                numbers.append(n)

        # Parse unanswered
        unanswered_raw = data.get("unanswered") or []
        if isinstance(unanswered_raw, list):
            unanswered = [str(u) for u in unanswered_raw if u]
        elif isinstance(unanswered_raw, str) and unanswered_raw:
            unanswered = [str(unanswered_raw)]
        else:
            unanswered = []

        return CompressedTurn(
            question_intent=str(data.get("question_intent", "") or ""),
            facts=facts,
            numbers_mentioned=numbers,
            unanswered=unanswered,
            compression_error="",
        )
