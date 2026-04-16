from typing import Any, Literal

from pydantic import BaseModel, Field

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

class FeedbackRequest(BaseModel):
    feedback: str | None = Field(None, description="Optional feedback from analyst")


class DependencyUpdateRequest(BaseModel):
    blocked_by: list[str] = Field(default_factory=list, description="Dependent task IDs")


class UserResponse(BaseModel):
    username: str


class MessageResponse(BaseModel):
    message: str


class ErrorResponse(BaseModel):
    detail: str


class AnalystPreview(BaseModel):
    name: str = ""
    role: str = ""
    affiliation: str = ""
    description: str = ""


class TokenMetrics(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    llm_calls: int = 0
    by_node: dict[str, Any] = Field(default_factory=dict)
    latency_ms: int = 0
    usage_available: bool = False


class LatencyMetrics(BaseModel):
    generation_ms: int = 0
    feedback_ms: int = 0
    created_to_completed_ms: int = 0


class TaskMetrics(BaseModel):
    latency: LatencyMetrics = Field(default_factory=LatencyMetrics)
    tokens: TokenMetrics = Field(default_factory=TokenMetrics)


class RetryStage(BaseModel):
    attempted: int = 0
    max: int = 1


class AutoRetryState(BaseModel):
    running_generation: RetryStage = Field(default_factory=RetryStage)
    running_feedback: RetryStage = Field(default_factory=RetryStage)


class RiskSummary(BaseModel):
    high: int = 0
    medium: int = 0
    low: int = 0


class TaskResponse(BaseModel):
    id: str
    type: str
    company_name: str
    focus: str = ""
    target_role: str = ""
    report_kind: str = "due_diligence"
    owner: str
    assignee: str
    blocked_by: list[str] = Field(default_factory=list)
    status: str
    thread_id: str = ""
    analysts_preview: list[AnalystPreview] = Field(default_factory=list)
    analyst_version: int = 0
    docx_path: str = ""
    pdf_path: str = ""
    error: str = ""
    failed_stage: str = ""
    retry_count: int = 0
    auto_retry: AutoRetryState = Field(default_factory=AutoRetryState)
    last_feedback: str = ""
    risk_summary: RiskSummary = Field(default_factory=RiskSummary)
    final_recommendation: str = ""
    metrics: TaskMetrics = Field(default_factory=TaskMetrics)
    created_at: float = 0
    updated_at: float = 0


class TaskListResponse(BaseModel):
    tasks: list[TaskResponse]


class TaskCreatedResponse(BaseModel):
    task: TaskResponse


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
