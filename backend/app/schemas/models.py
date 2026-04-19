# models.py
import operator
from typing import Annotated, Any, List
from langgraph.graph import MessagesState
from pydantic import BaseModel, Field
from typing_extensions import TypedDict


def keep_latest(_, new):
    """Reducer for scalar state keys updated by parallel branches."""
    return new

# -------------------------------
# Analyst Models
# -------------------------------

class Analyst(BaseModel):
    affiliation: str = Field(description="Primary affiliation or background for the analyst.")
    name: str = Field(description="Analyst display name.")
    role: str = Field(description="Role or mandate for this research task.")
    skill_id: str = Field(default="", description="Bound skill card ID, or empty string.")
    description: str = Field(
        description="Focus areas, concerns, and motivation for this analyst."
    )

    @property
    def persona(self) -> str:
        return (
            f"Name: {self.name}\n"
            f"Role: {self.role}\n"
            f"Affiliation: {self.affiliation}\n"
            f"Description: {self.description}\n"
        )

class Perspectives(BaseModel):
    analysts: List[Analyst] = Field(
        description="Full list of analysts with name, role, affiliation, and description."
    )


# -------------------------------
# Skill / Planning / Review Models
# -------------------------------

class SourcePolicy(BaseModel):
    policy_id: str = Field(description="Search policy ID.")
    label: str = Field(description="Human-readable policy label.")
    preferred_source_types: list[str] = Field(default_factory=list)
    site_hints: list[str] = Field(default_factory=list)
    freshness_hint: str = Field(default="balanced")
    guidance: list[str] = Field(default_factory=list)


class SkillRef(BaseModel):
    skill_id: str = Field(description="Skill card ID.")
    reason: str = Field(default="", description="Why this skill was selected for the task.")


class DomainMemoryEntry(BaseModel):
    memory_id: str = Field(description="Domain memory entry ID.")
    category: str = Field(description="Memory category.")
    title: str = Field(description="Memory title.")
    content: str = Field(description="Memory body text.")
    tags: list[str] = Field(default_factory=list)


class DomainMemoryRef(BaseModel):
    memory_id: str = Field(description="Domain memory entry ID.")
    category: str = Field(description="Memory category.")


class CoverageGoal(BaseModel):
    theme: str = Field(description="Theme to cover.")
    why_it_matters: str = Field(description="Why this theme matters.")


class AnalystPlan(BaseModel):
    analyst_name: str = Field(description="Analyst executing this plan.")
    skill_id: str = Field(default="", description="Bound role skill card ID.")
    research_skill_id: str = Field(default="", description="Bound research skill ID.")
    brief: str = Field(description="Sub-task brief for this analyst.")
    key_questions: list[str] = Field(default_factory=list)
    source_policy: dict[str, Any] = Field(default_factory=dict, description="Search policy used for this analyst.")


class ResearchPlan(BaseModel):
    summary: str = Field(description="Overall research plan summary.")
    coverage_goals: list[CoverageGoal] = Field(default_factory=list)
    analyst_plans: list[AnalystPlan] = Field(default_factory=list)


class RetrievedSource(BaseModel):
    source_id: str = Field(description="Source record ID.")
    title: str = Field(description="Source title.")
    url: str = Field(default="", description="Source URL.")
    snippet: str = Field(default="", description="Source snippet or excerpt.")
    source_type: str = Field(default="web", description="Source type label.")
    credibility_note: str = Field(default="", description="Credibility or quality note.")


class ReviewFinding(BaseModel):
    severity: str = Field(default="medium", description="Severity of the issue.")
    title: str = Field(description="Short title.")
    detail: str = Field(description="Detailed description.")
    suggested_fix: str = Field(default="", description="Suggested fix.")


class ReviewSummary(BaseModel):
    status: str = Field(default="pass", description="Review outcome.")
    summary: str = Field(default="", description="Review summary text.")
    findings: list[ReviewFinding] = Field(default_factory=list)

# -------------------------------
# Search Query Output Parser
# -------------------------------

class SearchQuery(BaseModel):
    search_query: str = Field(None, description="Query string for retrieval or web search.")
    source_type: str = Field(default="web", description="Preferred source type.")
    site_hints: list[str] = Field(default_factory=list, description="Preferred sites or domains.")
    freshness_hint: str = Field(default="balanced", description="Recency preference.")
    reasoning: str = Field(default="", description="Routing rationale.")

# -------------------------------
# State Classes for Graphs
# -------------------------------

class GenerateAnalystsState(TypedDict):
    research_query: str  # Research brief/query for due diligence
    company_name: str  # Target company name for due diligence
    max_analysts: int  # Number of analysts to generate
    human_analyst_feedback: str  # Feedback from human
    report_kind: str  # Report kind or task family
    focus: str  # Focus areas
    target_role: str  # Target role context
    company_type: str  # Classified company type
    company_type_confidence: float  # Classification confidence
    company_type_source: str  # manual / fallback
    skill_bundle: list[dict[str, Any]]  # Role skills chosen for this task
    analysts: List[Analyst]  # List of analysts generated

class InterviewState(MessagesState):
    max_num_turns: int  # Max interview turns allowed
    turn_count: int  # Current interview turns completed
    context: Annotated[list, operator.add]  # Retrieved or searched context
    analyst: Analyst  # Analyst conducting interview
    skill_card: dict[str, Any]  # Role skill used by the analyst
    assigned_plan: AnalystPlan  # Research assignment for the analyst
    domain_memory: list[DomainMemoryEntry]  # Readonly domain memory injected into the run
    retrieved_sources: Annotated[list, operator.add]  # Structured sources retrieved by router
    router_decisions: Annotated[list, operator.add]  # Search router decisions
    review_notes: Annotated[list, operator.add]  # Section-level review results
    workflow_events: Annotated[list, operator.add]  # Structured workflow events
    interview: str  # Full interview transcript
    sections: list  # Generated section from interview
    llm_metrics: Annotated[list, operator.add]  # Per-node llm usage metrics

class ResearchGraphState(TypedDict):
    research_query: str  # Research brief/query for due diligence
    company_name: str  # Target company name for due diligence
    focus: str  # Task focus areas
    target_role: str  # Role context
    report_kind: str  # Report type
    max_analysts: int  # Number of analysts
    max_num_turns: Annotated[int, keep_latest]  # Interview turns per analyst
    industry_pack: str  # Skill pack folder key from API (same as task.industry_pack)
    company_type: str  # After classify: matches industry_pack when valid, else unknown
    company_type_confidence: Annotated[float, keep_latest]  # Company type confidence
    company_type_source: str  # manual / fallback
    planner_enabled: Annotated[bool, keep_latest]  # Whether planner is enabled
    review_enabled: Annotated[bool, keep_latest]  # Whether review is enabled
    human_analyst_feedback: str  # Optional human feedback
    skill_bundle: Annotated[list, keep_latest]  # Role skills selected for the task
    research_skills: Annotated[list, keep_latest]  # Research skills selected for the task
    skill_mapping: Annotated[dict[str, Any], keep_latest]  # Mapping between role and research skills
    source_policy_map: Annotated[dict[str, Any], keep_latest]  # Source policies by id
    domain_memory: Annotated[list, keep_latest]  # Readonly domain memory injected in the run
    research_plan: Annotated[ResearchPlan, keep_latest]  # Generated research plan
    analysts: List[Analyst]  # All analysts involved
    sections: Annotated[list, operator.add]  # All interview-generated sections
    introduction: str  # Introduction of final report
    content: str  # Main content of report
    conclusion: str  # Conclusion of final report
    report_review: Annotated[ReviewSummary, keep_latest]  # Review summary for full report
    review_notes: Annotated[list, operator.add]  # Review notes across the workflow
    router_decisions: Annotated[list, operator.add]  # Aggregated router decisions
    workflow_events: Annotated[list, operator.add]  # Structured workflow events
    final_report: str  # Compiled report string
    llm_metrics: Annotated[list, operator.add]  # All LLM call metrics across graph