from datetime import datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.models.request_models import HealthResponse
from app.api.routes import report_routes
from app.api.services.task_runtime import TASK_RUNTIME
from app.config import FRONTEND_ORIGINS

app = FastAPI(title="Autonomous Report Generator API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def recover_runtime_tasks():
    TASK_RUNTIME.recover_interrupted_tasks()

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint for container orchestration"""
    return {
        "status": "healthy",
        "service": "research-report-generation",
        "timestamp": datetime.now().isoformat(),
    }


app.include_router(report_routes.router)
