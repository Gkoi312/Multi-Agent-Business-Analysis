"""
Harness Evaluation Framework — three-layer quality measurement.

Layer 1 (Component): compression fidelity, pipeline quality, checkpoint reliability.
Layer 2 (Integration): consistency checks, search→answer traceability.
Layer 3 (End-to-end): report completeness, source traceability, factual accuracy.

Usage:
    from harness.evaluation import Scorer, ScoreResult, SCORER_REGISTRY, register_scorer
    from harness.evaluation.fixtures import load_fixture, load_fixtures
    from harness.evaluation.consistency import run_consistency_checks
"""

from harness.evaluation.scorer import (
    Scorer,
    ScoreResult,
    SCORER_REGISTRY,
    get_scorer,
    list_scorers,
    register_scorer,
)
from harness.evaluation.fixtures import load_fixture, load_fixtures, save_fixture
from harness.evaluation.pipeline_metrics import (
    PipelineMetrics,
    aggregate_trace,
    format_metrics_table,
)
from harness.evaluation.consistency import (
    CONSISTENCY_CHECKS,
    ConsistencyResult,
    run_consistency_checks,
)
from harness.evaluation.reliability import (
    ReliabilityReport,
    DimensionStats,
    CaseStats,
)
from harness.evaluation.runner import EvalRunResult

__all__ = [
    # scorer
    "Scorer",
    "ScoreResult",
    "SCORER_REGISTRY",
    "get_scorer",
    "list_scorers",
    "register_scorer",
    # fixtures
    "load_fixture",
    "load_fixtures",
    "save_fixture",
    # pipeline metrics
    "PipelineMetrics",
    "aggregate_trace",
    "format_metrics_table",
    # consistency
    "CONSISTENCY_CHECKS",
    "ConsistencyResult",
    "run_consistency_checks",
    # reliability
    "ReliabilityReport",
    "DimensionStats",
    "CaseStats",
    # runner
    "EvalRunResult",
]
