"""
Tool Pipeline — a composable processing chain for tool results.

Every tool call can (optionally) pass through a pipeline of ``ProcessingStage``
instances.  Each stage receives the data and a ``ToolContext`` and returns
transformed data.  This makes search-result cleaning, deduplication, and
structuring predictable, debuggable, and measurable.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


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

    Subclasses implement ``__call__`` which receives the data (list of SearchResult
    or list of dict) and returns processed data of the same shape.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short kebab-case identifier shown in traces."""
        ...

    @abstractmethod
    def __call__(self, data: list[Any], ctx: ToolContext) -> list[Any]:
        """Transform *data*.  Must return a list of the same element type."""
        ...


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

@dataclass
class StageTrace:
    stage: str
    duration_ms: int
    input_count: int
    output_count: int
    reduction_pct: float = 0.0


class ToolPipeline:
    """Ordered chain of ``ProcessingStage`` instances.

    Usage::

        pipeline = ToolPipeline([DeduplicateStage(), CleanTextStage(), ...])
        cleaned = pipeline.run(raw_results, ctx)
    """

    def __init__(self, stages: list[ProcessingStage]):
        self.stages = stages

    def run(self, data: list[Any], ctx: ToolContext | None = None) -> list[Any]:
        """Run *data* through every stage sequentially, returning the final result."""
        ctx = ctx or ToolContext()
        for stage in self.stages:
            data = stage(data, ctx)
        return data

    def run_with_trace(
        self, data: list[Any], ctx: ToolContext | None = None
    ) -> tuple[list[Any], list[StageTrace]]:
        """Run and return (result, per-stage timing/count traces)."""
        ctx = ctx or ToolContext()
        traces: list[StageTrace] = []
        for stage in self.stages:
            started = time.perf_counter()
            before = len(data)
            data = stage(data, ctx)
            after = len(data)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            reduction = round((1 - after / before) * 100, 1) if before else 0.0
            traces.append(
                StageTrace(
                    stage=stage.name,
                    duration_ms=elapsed_ms,
                    input_count=before,
                    output_count=after,
                    reduction_pct=reduction,
                )
            )
        return data, traces
