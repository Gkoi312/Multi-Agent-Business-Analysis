# Memory & Context — compressor, working memory, context window, editing,
# assembling, fact reconciliation, and history compaction.
#
# Public API — import from here for all memory layer components.

from harness.memory.compressor import IncrementalCompressor
from harness.memory.context_window import ContextWindowManager
from harness.memory.working_memory import WorkingMemory
from harness.memory.policies import (
    DEFAULT_COVERAGE_THRESHOLDS,
    DEFAULT_SUFFICIENT_THRESHOLDS,
    VALID_PRIMARY_CATEGORIES,
    TokenBudget,
    CompactionPolicy,
    ToolPruneConfig,
    ContextWindowConfig,
    MemoryDomainConfig,
)
from harness.memory.running_summary import RunningSummaryManager
from harness.memory.history_compactor import HistoryCompactor
from harness.memory.context_editing import ToolContextPruner
from harness.memory.context_assembler import ContextAssembler
from harness.memory.fact_reconciler import FactReconciler
from harness.memory.search_digest import SearchDigestBuilder

__all__ = [
    # Core
    "ContextWindowManager",
    "IncrementalCompressor",
    "WorkingMemory",
    # New components
    "RunningSummaryManager",
    "HistoryCompactor",
    "ToolContextPruner",
    "ContextAssembler",
    "FactReconciler",
    "SearchDigestBuilder",
    # Policies & config
    "DEFAULT_COVERAGE_THRESHOLDS",
    "DEFAULT_SUFFICIENT_THRESHOLDS",
    "VALID_PRIMARY_CATEGORIES",
    "TokenBudget",
    "CompactionPolicy",
    "ToolPruneConfig",
    "ContextWindowConfig",
    "MemoryDomainConfig",
]
