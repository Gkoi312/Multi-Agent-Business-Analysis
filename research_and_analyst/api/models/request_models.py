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
    thread_id: str
    feedback: str | None = Field(None, description="Optional feedback from analyst")
