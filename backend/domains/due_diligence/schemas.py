"""
Due Diligence domain schemas — TypedDict state definitions specific to this domain.
"""
import operator
from typing import Annotated, Any

from langgraph.graph import MessagesState
from typing_extensions import TypedDict

from harness.models.agent import (
    Analyst,
    AnalystPlan,
    DomainMemoryEntry,
    ResearchPlan,
    ReviewSummary,
)
from harness.models import keep_latest


class GenerateAnalystsState(TypedDict):
    research_query: str
    company_name: str
    max_analysts: int
    human_analyst_feedback: str
    report_kind: str
    focus: str
    target_role: str
    skill_bundle: list[dict[str, Any]]
    analysts: list[Analyst]


class InterviewState(MessagesState):
    max_num_turns: int
    turn_count: int
    company_name: str
    focus: str
    context: Annotated[list, operator.add]
    analyst: Analyst
    skill_card: dict[str, Any]
    assigned_plan: AnalystPlan
    domain_memory: list[DomainMemoryEntry]
    retrieved_sources: Annotated[list, operator.add]
    router_decisions: Annotated[list, operator.add]
    review_notes: Annotated[list, operator.add]
    workflow_events: Annotated[list, operator.add]
    interview: str
    sections: list
    llm_metrics: Annotated[list, operator.add]
    # Memory & compression (Phase 4.3 + Round 3)
    compressed_turns: list  # list[dict] — serialised CompressedTurn payloads
    working_memory: dict  # serialised WorkingMemory payload (sole truth source, NOT list-reduced)
    memory_snapshot: dict  # read-only MergedMemory snapshot derived from WorkingMemory
    running_summary: dict  # serialised RunningSummary cursor (NOT list-reduced)
    search_digest: dict  # serialised SearchDigest for current turn
    source_registry: dict  # serialised {source_id: SourceRecord} accumulated across turns


class ResearchGraphState(TypedDict):
    research_query: str
    company_name: str
    focus: str
    target_role: str
    report_kind: str
    max_analysts: int
    max_num_turns: Annotated[int, keep_latest]
    company_type: str
    planner_enabled: Annotated[bool, keep_latest]
    review_enabled: Annotated[bool, keep_latest]
    human_analyst_feedback: str
    skill_bundle: Annotated[list, keep_latest]
    research_skills: Annotated[list, keep_latest]
    skill_mapping: Annotated[dict[str, Any], keep_latest]
    source_policy_map: Annotated[dict[str, Any], keep_latest]
    domain_memory: Annotated[list, keep_latest]
    research_plan: Annotated[ResearchPlan, keep_latest]
    analysts: list[Analyst]
    sections: Annotated[list, operator.add]
    introduction: str
    content: str
    conclusion: str
    report_review: Annotated[ReviewSummary, keep_latest]
    review_notes: Annotated[list, operator.add]
    router_decisions: Annotated[list, operator.add]
    workflow_events: Annotated[list, operator.add]
    final_report: str
    llm_metrics: Annotated[list, operator.add]
