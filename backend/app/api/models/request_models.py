from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

class LoginRequest(BaseModel):
    username: str = Field(..., description="Username for login")
    password: str = Field(..., description="Password for login")

class SignupRequest(BaseModel):
    username: str = Field(..., description="New username for signup")
    password: str = Field(..., description="Password for signup")

class DueDiligenceRequest(BaseModel):
    company_name: str = Field(..., description="Company to analyze")
    focus: str = Field("", description="Optional focus areas")
    target_role: str = Field("", description="Optional role context")
    max_analysts: int = Field(3, description="Number of analyst personas to create")
    task_type: str = Field(
        default="due_diligence",
        description="Task type slug — maps to a domain adapter",
    )


class SkillPackListResponse(BaseModel):
    items: list[str]


class FeedbackRequest(BaseModel):
    feedback: str | None = Field(None, description="Optional feedback from analyst")


class UserResponse(BaseModel):
    username: str


class MessageResponse(BaseModel):
    message: str


class AnalystPreview(BaseModel):
    name: str = ""
    role: str = ""
    affiliation: str = ""
    description: str = ""


class RiskSummary(BaseModel):
    high: int = 0
    medium: int = 0
    low: int = 0


class TaskResponse(BaseModel):
    model_config = ConfigDict(extra="allow")  # forward-compat with harness-level fields

    id: str
    task_type: str = "due_diligence"
    owner: str = ""
    company_name: str
    focus: str = ""
    target_role: str = ""
    max_analysts: int = 3
    status: str
    thread_id: str = ""
    analysts_preview: list[AnalystPreview] = Field(default_factory=list)
    analyst_version: int = 0
    docx_path: str = ""
    pdf_path: str = ""
    error: str = ""
    failed_stage: str = ""
    last_feedback: str = ""
    risk_summary: RiskSummary = Field(default_factory=RiskSummary)
    final_recommendation: str = ""
    report_review_status: str = ""
    report_review_summary: str = ""
    created_at: float = 0
    updated_at: float = 0


class TaskListResponse(BaseModel):
    tasks: list[TaskResponse]


class EventResponse(BaseModel):
    ts: float
    task_id: str
    event: str
    payload: dict[str, Any] = Field(default_factory=dict)


class TaskEventsResponse(BaseModel):
    task_id: str
    events: list[EventResponse]


class ReportCreateResponse(BaseModel):
    task: TaskResponse


class RetryResponse(BaseModel):
    message: str
    task_id: str


class TaskActionResponse(BaseModel):
    task: TaskResponse


class HealthResponse(BaseModel):
    status: Literal["healthy"]
    service: str
    timestamp: str


class MetricsResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    call_count: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    total_latency_ms: int = 0
    estimated_cost_usd: float = 0.0
    budget_cap_usd: float | None = None
    budget_remaining_usd: float | None = None
    over_budget: bool = False
    by_node: dict = Field(default_factory=dict)
    by_model: dict = Field(default_factory=dict)
