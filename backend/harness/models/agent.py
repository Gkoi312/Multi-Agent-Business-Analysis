"""
Harness generic agent models — Analyst, skill refs, planning, review.

These types are domain-agnostic and can be reused across any domain app.
"""
from pydantic import BaseModel, Field
from typing import Any


# ---------------------------------------------------------------------------
# Analyst
# ---------------------------------------------------------------------------

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
    analysts: list[Analyst] = Field(
        description="Full list of analysts with name, role, affiliation, and description."
    )


# ---------------------------------------------------------------------------
# Skill / Planning / Review
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Search query
# ---------------------------------------------------------------------------

class SearchQuery(BaseModel):
    search_query: str = Field(None, description="Query string for retrieval or web search.")
    source_type: str = Field(default="web", description="Preferred source type.")
    site_hints: list[str] = Field(default_factory=list, description="Preferred sites or domains.")
    freshness_hint: str = Field(default="balanced", description="Recency preference.")
    reasoning: str = Field(default="", description="Routing rationale.")
