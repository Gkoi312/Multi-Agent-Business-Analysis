"""
Tool Pipeline — a composable processing chain for tool results.

Every tool call can (optionally) pass through a pipeline of ``ProcessingStage``
instances.  Each stage receives ``list[SearchDocument]`` and a ``ToolContext``
and returns transformed ``list[SearchDocument]``.  This makes search-result
cleaning, deduplication, and structuring predictable, debuggable, and measurable.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from harness.tools.search.base import SearchDocument


# ---------------------------------------------------------------------------
# Tool context — shared metadata available to every pipeline stage
# ---------------------------------------------------------------------------

@dataclass
class ToolContext:
    """Immutable-ish context passed through the pipeline."""

    target_entity: str = ""  # e.g. company name
    target_focus: str = ""  # e.g. "AI strategy"
    source_type: str = "web"
    max_results: int = 10
    # Optional cheap LLM for stages that need light reasoning (e.g. relevance filter).
    cheap_llm: Any = None

    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Processing stage
# ---------------------------------------------------------------------------

class ProcessingStage(ABC):
    """One step in a tool pipeline.

    Subclasses implement ``__call__`` which receives ``list[SearchDocument]``
    and returns processed ``list[SearchDocument]``.

    Each stage must decide its own error strategy (fail-open or fail-closed)
    and document it in the class docstring.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short kebab-case identifier shown in traces."""
        ...

    @abstractmethod
    def __call__(
        self, data: list[SearchDocument], ctx: ToolContext
    ) -> list[SearchDocument]:
        """Transform *data*.  Must return a list of ``SearchDocument``."""
        ...


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

@dataclass
class StageTrace:
    """Per-stage metrics collected during a pipeline run.

    ``input_count`` / ``output_count`` count only **non-dropped** documents
    entering / leaving the stage.

    ``dropped_count`` and ``warning_count`` are **incremental** — only new
    drops and warnings introduced by this stage, not cumulative totals.
    """

    stage: str
    duration_ms: int
    input_count: int
    """Non-dropped documents entering this stage."""

    output_count: int
    """Non-dropped documents leaving this stage."""

    reduction_pct: float = 0.0
    """Percentage of non-dropped docs removed this stage."""

    warning_count: int = 0
    """New warnings added by this stage (incremental)."""

    dropped_count: int = 0
    """New documents marked as dropped by this stage (incremental)."""


class ToolPipeline:
    """Ordered chain of ``ProcessingStage`` instances.

    Usage::

        pipeline = ToolPipeline([CanonicalizeURLStage(), CleanTextStage(), ...])
        cleaned, trace = pipeline.run_with_trace(raw_docs, ctx)
    """

    def __init__(self, stages: list[ProcessingStage]):
        self.stages = stages

    def run(
        self,
        data: list[SearchDocument],
        ctx: ToolContext | None = None,
    ) -> list[SearchDocument]:
        """Run *data* through every stage sequentially, returning the final result."""
        ctx = ctx or ToolContext()
        for stage in self.stages:
            data = stage(data, ctx)
        return data

    def run_with_trace(
        self,
        data: list[SearchDocument],
        ctx: ToolContext | None = None,
    ) -> tuple[list[SearchDocument], list[StageTrace]]:
        """Run and return (result, per-stage timing/count traces)."""
        ctx = ctx or ToolContext()
        traces: list[StageTrace] = []

        # Snapshot cumulative state before first stage
        prev_warnings = sum(len(d.warnings) for d in data)
        prev_dropped = sum(1 for d in data if d.dropped_reason)

        for stage in self.stages:
            # Count non-dropped docs entering
            input_active = sum(1 for d in data if not d.dropped_reason)

            started = time.perf_counter()
            data = stage(data, ctx)
            elapsed_ms = int((time.perf_counter() - started) * 1000)

            # Count non-dropped docs leaving
            output_active = sum(1 for d in data if not d.dropped_reason)

            # Incremental counts (this stage only, not cumulative)
            curr_warnings = sum(len(d.warnings) for d in data)
            curr_dropped = sum(1 for d in data if d.dropped_reason)
            stage_warnings = curr_warnings - prev_warnings
            stage_dropped = curr_dropped - prev_dropped

            reduction = round((1 - output_active / input_active) * 100, 1) if input_active else 0.0

            traces.append(
                StageTrace(
                    stage=stage.name,
                    duration_ms=elapsed_ms,
                    input_count=input_active,
                    output_count=output_active,
                    reduction_pct=reduction,
                    warning_count=stage_warnings,
                    dropped_count=stage_dropped,
                )
            )

            prev_warnings = curr_warnings
            prev_dropped = curr_dropped

        return data, traces
