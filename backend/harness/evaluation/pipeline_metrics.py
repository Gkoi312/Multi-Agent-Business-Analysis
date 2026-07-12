"""
Pipeline Trace Aggregator — converts existing StageTrace data into structured metrics.

Reuses data already collected by ``ToolPipeline.run_with_trace()``.
Pure code, no LLM needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StageMetrics:
    """Per-stage aggregated metrics."""
    stage: str
    duration_ms: int
    input_count: int
    output_count: int
    reduction_pct: float
    dropped_count: int
    warning_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "duration_ms": self.duration_ms,
            "input_count": self.input_count,
            "output_count": self.output_count,
            "reduction_pct": self.reduction_pct,
            "dropped_count": self.dropped_count,
            "warning_count": self.warning_count,
        }


@dataclass
class PipelineMetrics:
    """Aggregate metrics from a full pipeline run."""
    pipeline_name: str = ""
    total_duration_ms: int = 0
    total_input_docs: int = 0
    total_output_docs: int = 0
    total_reduction_pct: float = 0.0
    total_dropped: int = 0
    total_warnings: int = 0
    stage_count: int = 0
    per_stage: list[StageMetrics] = field(default_factory=list)

    @property
    def survived_docs(self) -> int:
        """Documents that were NOT dropped by any stage."""
        return self.total_input_docs - self.total_dropped

    def to_dict(self) -> dict[str, Any]:
        return {
            "pipeline_name": self.pipeline_name,
            "total_duration_ms": self.total_duration_ms,
            "total_input_docs": self.total_input_docs,
            "total_output_docs": self.total_output_docs,
            "total_reduction_pct": self.total_reduction_pct,
            "total_dropped": self.total_dropped,
            "total_warnings": self.total_warnings,
            "stage_count": self.stage_count,
            "survived_docs": self.survived_docs,
            "per_stage": [s.to_dict() for s in self.per_stage],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PipelineMetrics":
        return cls(
            pipeline_name=str(d.get("pipeline_name", "")),
            total_duration_ms=int(d.get("total_duration_ms", 0)),
            total_input_docs=int(d.get("total_input_docs", 0)),
            total_output_docs=int(d.get("total_output_docs", 0)),
            total_reduction_pct=float(d.get("total_reduction_pct", 0)),
            total_dropped=int(d.get("total_dropped", 0)),
            total_warnings=int(d.get("total_warnings", 0)),
            stage_count=int(d.get("stage_count", 0)),
            per_stage=[StageMetrics(**s) if isinstance(s, dict) else s
                       for s in (d.get("per_stage", []) or [])],
        )


def aggregate_trace(
    traces: list[Any],
    pipeline_name: str = "",
) -> PipelineMetrics:
    """Aggregate a list of StageTrace objects into PipelineMetrics.

    Args:
        traces: ``list[StageTrace]`` from ``ToolPipeline.run_with_trace()``.
        pipeline_name: Optional label for this pipeline run.

    Returns:
        PipelineMetrics with per-stage breakdown and overall stats.
    """
    if not traces:
        return PipelineMetrics(pipeline_name=pipeline_name)

    per_stage: list[StageMetrics] = []
    total_duration = 0
    total_dropped = 0
    total_warnings = 0

    for t in traces:
        sm = StageMetrics(
            stage=getattr(t, "stage", "unknown"),
            duration_ms=getattr(t, "duration_ms", 0),
            input_count=getattr(t, "input_count", 0),
            output_count=getattr(t, "output_count", 0),
            reduction_pct=getattr(t, "reduction_pct", 0.0),
            dropped_count=getattr(t, "dropped_count", 0),
            warning_count=getattr(t, "warning_count", 0),
        )
        per_stage.append(sm)
        total_duration += sm.duration_ms
        total_dropped += sm.dropped_count
        total_warnings += sm.warning_count

    first = traces[0]
    last = traces[-1]
    total_input = getattr(first, "input_count", 0)
    total_output = getattr(last, "output_count", 0)

    overall_reduction = round(
        (1 - total_output / total_input) * 100, 1
    ) if total_input > 0 else 0.0

    return PipelineMetrics(
        pipeline_name=pipeline_name,
        total_duration_ms=total_duration,
        total_input_docs=total_input,
        total_output_docs=total_output,
        total_reduction_pct=overall_reduction,
        total_dropped=total_dropped,
        total_warnings=total_warnings,
        stage_count=len(traces),
        per_stage=per_stage,
    )


def format_metrics_table(metrics: PipelineMetrics) -> str:
    """Render PipelineMetrics as a Markdown table (for README / 面试).

    Example output::

        | Stage              | 输入 | 输出 | 削减率 | 耗时   | 丢弃 |
        |--------------------|------|------|--------|--------|------|
        | CanonicalizeURL    | 10   | 10   | 0%     | 2ms    | 0    |
        | CleanText          | 10   | 9    | 10%    | 15ms   | 1    |
        | ...                | ...  | ...  | ...    | ...    | ...  |
        | **总计**           | 10   | 4    | 60%    | 288ms  | 6    |
    """
    header = "| Stage | 输入 | 输出 | 削减率 | 耗时 | 丢弃 |"
    sep = "|-------|------|------|--------|------|------|"
    rows: list[str] = [header, sep]

    for s in metrics.per_stage:
        rows.append(
            f"| {s.stage} | {s.input_count} | {s.output_count} "
            f"| {s.reduction_pct}% | {s.duration_ms}ms | {s.dropped_count} |"
        )

    # Total row
    rows.append(
        f"| **总计** | {metrics.total_input_docs} | {metrics.total_output_docs} "
        f"| {metrics.total_reduction_pct}% | {metrics.total_duration_ms}ms "
        f"| {metrics.total_dropped} |"
    )

    return "\n".join(rows)
