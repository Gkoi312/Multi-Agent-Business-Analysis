# Harness generic data models — memory types, agent types, state helpers.


def keep_latest(_, new):
    """Reducer for scalar state keys updated by parallel branches."""
    return new


from harness.models.agent import (
    Analyst,
    AnalystPlan,
    CoverageGoal,
    DomainMemoryEntry,
    Perspectives,
    ResearchPlan,
    RetrievedSource,
    ReviewFinding,
    ReviewSummary,
    SearchQuery,
)
from harness.models.memory import (
    CompressedTurn,
    MergedMemory,
    MemoryFact,
    MemoryOperation,
    RunningSummary,
    SearchDigest,
    SourceRecord,
    ToolPruneResult,
    FactLedger,
    CoveragePolicy,
    ContextAssemblyResult,
    ContextBudgetExceeded,
    TokenCounter,
)

__all__ = [
    "keep_latest",
    # Agent types
    "Analyst",
    "AnalystPlan",
    "CoverageGoal",
    "DomainMemoryEntry",
    "Perspectives",
    "ResearchPlan",
    "RetrievedSource",
    "ReviewFinding",
    "ReviewSummary",
    "SearchQuery",
    # Memory types
    "CompressedTurn",
    "MergedMemory",
    "MemoryFact",
    "MemoryOperation",
    "RunningSummary",
    "SearchDigest",
    "SourceRecord",
    "ToolPruneResult",
    "FactLedger",
    "CoveragePolicy",
    "ContextAssemblyResult",
    "ContextBudgetExceeded",
    "TokenCounter",
]
