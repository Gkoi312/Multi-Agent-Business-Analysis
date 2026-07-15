from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
import uuid

from server.api.models.request_models import (
    DueDiligenceRequest,
    FeedbackRequest,
    MessageResponse,
    ReportCreateResponse,
    RetryResponse,
    TaskActionResponse,
    TaskEventsResponse,
    TaskListResponse,
    TaskResponse,
    UserResponse,
    LoginRequest,
    SignupRequest,
)
from server.api.services.report_service import ReportService
from server.api.services.session_store import SESSION_STORE
from harness.observability.task_runtime import TASK_RUNTIME
from server.config import (
    SESSION_COOKIE_MAX_AGE,
    SESSION_COOKIE_NAME,
    SESSION_COOKIE_SAMESITE,
    SESSION_COOKIE_SECURE,
)
from server.database.db_config import (
    SessionLocal,
    User,
    hash_password,
    verify_password,
)


router = APIRouter(prefix="/api", tags=["api"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _set_session_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_id,
        httponly=True,
        secure=SESSION_COOKIE_SECURE,
        samesite=SESSION_COOKIE_SAMESITE,
        max_age=SESSION_COOKIE_MAX_AGE,
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        httponly=True,
        secure=SESSION_COOKIE_SECURE,
        samesite=SESSION_COOKIE_SAMESITE,
    )


def _get_current_user(request: Request) -> str | None:
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    return SESSION_STORE.get_username(session_id)


def _require_current_user(request: Request) -> str:
    username = _get_current_user(request)
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not signed in or session expired")
    return username


def _task_owned_by(task: dict, username: str) -> bool:
    return bool(task) and task.get("owner") == username


def _require_owned_task(task_id: str, username: str) -> dict:
    task = TASK_RUNTIME.get_task(task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    if not _task_owned_by(task, username):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed to access this task")
    return task


def _build_research_query(company_name: str, focus: str, target_role: str) -> str:
    return f"""Conduct due diligence research on this company: {company_name}
Focus areas: {focus or "business model, company scale and trajectory, and key risk factors"}
Target role context: {target_role or "not specified"}
Produce a structured analysis with clear, evidence-backed conclusions on business, scale/growth, and risk."""


def _task_response(task: dict) -> TaskResponse:
    return TaskResponse(**task)


def _start_generation_job(task_id: str, research_query: str, max_analysts: int, *, use_thread_id: str = ""):
    def _start_generation():
        service = ReportService()
        task = TASK_RUNTIME.get_task(task_id) or {}
        company_name = task.get("company_name", "")
        focus = task.get("focus", "")
        target_role = task.get("target_role", "")

        # Persist thread_id BEFORE streaming so retry can find it even on immediate failure
        thread_id = use_thread_id or str(uuid.uuid4())
        TASK_RUNTIME.update_task(task_id, thread_id=thread_id)

        result = service.start_report_generation(
            research_query,
            max_analysts,
            company_name,
            focus=focus,
            target_role=target_role,
            task_id=task_id,
            thread_id=thread_id,
        )
        analysts_preview = result.get("analysts_preview", [])
        TASK_RUNTIME.emit_event(
            task_id,
            "workflow.skills.assembled",
            {"analyst_count": len(analysts_preview)},
        )
        return {
            "thread_id": result["thread_id"],
            "analysts_preview": analysts_preview,
            "analyst_version": 1 if analysts_preview else 0,
            "failed_stage": "",
        }

    TASK_RUNTIME.run_in_background(
        task_id=task_id,
        started_status="running_generation",
        finished_status="awaiting_feedback",
        work=_start_generation,
    )


def _resume_generation_job(task_id: str, thread_id: str):
    """Resume a generation-stage graph from its last checkpoint (no wasted tokens)."""

    def _resume():
        service = ReportService()
        thread = {"configurable": {"thread_id": thread_id}}

        # Check current state — graph may already be at human_feedback (success)
        state = service.graph.get_state(thread)
        pending = list(getattr(state, "next", []) or [])
        if "human_feedback" in pending:
            analysts_preview = service._extract_analysts_preview(state.values)
            return {
                "next_status": "awaiting_feedback",
                "thread_id": thread_id,
                "analysts_preview": analysts_preview,
                "analyst_version": 1 if analysts_preview else 0,
                "failed_stage": "",
            }

        # Resume from last checkpoint — re-runs the failed node and continues
        for _ in service.graph.stream(None, thread, stream_mode="values"):
            pass

        state = service.graph.get_state(thread)
        pending = list(getattr(state, "next", []) or [])
        if "human_feedback" in pending:
            analysts_preview = service._extract_analysts_preview(state.values)
            return {
                "next_status": "awaiting_feedback",
                "thread_id": thread_id,
                "analysts_preview": analysts_preview,
                "analyst_version": 1 if analysts_preview else 0,
                "failed_stage": "",
            }

        # Graph completed without reaching human_feedback (no interrupt triggered)
        return {
            "next_status": "completed",
            "thread_id": thread_id,
            "failed_stage": "",
        }

    TASK_RUNTIME.run_in_background(
        task_id=task_id,
        started_status="running_generation",
        finished_status="awaiting_feedback",
        work=_resume,
    )


def _resume_feedback_job(task_id: str, thread_id: str, feedback: str):
    """Resume a feedback-stage graph from its exact last checkpoint.

    Unlike ``_start_feedback_job`` (which always forks from ``human_feedback``),
    this resumes from wherever the graph actually stopped — preserving every
    token spent on already-completed research nodes.
    """

    def _resume():
        service = ReportService()
        thread = {"configurable": {"thread_id": thread_id}}

        # If the graph is still waiting at human_feedback, inject feedback first
        state = service.graph.get_state(thread)
        pending = list(getattr(state, "next", []) or [])
        if "human_feedback" in pending and feedback:
            service.graph.update_state(
                thread,
                {"human_analyst_feedback": feedback},
                as_node="human_feedback",
            )

        # Resume from the last checkpoint (could be right after the failed node)
        for _ in service.graph.stream(None, thread, stream_mode="values"):
            pass

        # Check where we landed
        state = service.graph.get_state(thread)
        pending = list(getattr(state, "next", []) or [])
        if "human_feedback" in pending:
            analysts_preview = service._extract_analysts_preview(state.values)
            return {
                "next_status": "awaiting_feedback",
                "analysts_preview": analysts_preview,
                "analyst_version": 1 if analysts_preview else 0,
                "failed_stage": "",
            }

        result = service.get_report_status(thread_id, task_id=task_id)
        TASK_RUNTIME.emit_event(
            task_id,
            "workflow.report.status",
            {"review_status": result.get("report_review_status", "")},
        )
        return {
            "docx_path": result.get("docx_path", ""),
            "pdf_path": result.get("pdf_path", ""),
            "risk_summary": result.get("risk_summary", {"high": 0, "medium": 0, "low": 0}),
            "final_recommendation": result.get("final_recommendation", ""),
            "report_review_status": result.get("report_review_status", ""),
            "report_review_summary": result.get("report_review_summary", ""),
            "failed_stage": "",
        }

    TASK_RUNTIME.run_in_background(
        task_id=task_id,
        started_status="running_feedback",
        finished_status="completed",
        work=_resume,
    )


def _start_feedback_job(task_id: str, thread_id: str, feedback: str):
    def _continue_with_feedback():
        service = ReportService()
        current_task = TASK_RUNTIME.get_task(task_id) or {}
        current_analyst_version = int(current_task.get("analyst_version", 0) or 0)
        feedback_result = service.submit_feedback(thread_id, feedback, task_id=task_id)
        if feedback_result.get("awaiting_feedback"):
            next_analyst_version = current_analyst_version + (1 if feedback.strip() else 0)
            return {
                "next_status": "awaiting_feedback",
                "analysts_preview": feedback_result.get("analysts_preview", []),
                "analyst_version": next_analyst_version,
                "failed_stage": "",
            }
        result = service.get_report_status(thread_id, task_id=task_id)
        TASK_RUNTIME.emit_event(
            task_id,
            "workflow.report.status",
            {
                "review_status": result.get("report_review_status", ""),
            },
        )
        return {
            "docx_path": result.get("docx_path", ""),
            "pdf_path": result.get("pdf_path", ""),
            "risk_summary": result.get(
                "risk_summary", {"high": 0, "medium": 0, "low": 0}
            ),
            "final_recommendation": result.get("final_recommendation", ""),
            "report_review_status": result.get("report_review_status", ""),
            "report_review_summary": result.get("report_review_summary", ""),
            "analysts_preview": feedback_result.get("analysts_preview", []),
            "analyst_version": current_analyst_version,
            "failed_stage": "",
        }

    TASK_RUNTIME.run_in_background(
        task_id=task_id,
        started_status="running_feedback",
        finished_status="completed",
        work=_continue_with_feedback,
    )


@router.post("/auth/signup", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def signup(payload: SignupRequest):
    db = next(get_db())
    existing_user = db.query(User).filter(User.username == payload.username).first()
    if existing_user:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already exists")

    hashed_pw = hash_password(payload.password)
    new_user = User(username=payload.username, password=hashed_pw)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    session_id = SESSION_STORE.create(new_user.username)
    response = JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=UserResponse(username=new_user.username).model_dump(),
    )
    _set_session_cookie(response, session_id)
    return response


@router.post("/auth/login", response_model=UserResponse)
async def login(payload: LoginRequest):
    db = next(get_db())
    user = db.query(User).filter(User.username == payload.username).first()
    if not user or not verify_password(payload.password, user.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    session_id = SESSION_STORE.create(user.username)
    response = JSONResponse(content=UserResponse(username=user.username).model_dump())
    _set_session_cookie(response, session_id)
    return response


@router.post("/auth/logout", response_model=MessageResponse)
async def logout(request: Request):
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    SESSION_STORE.delete(session_id)
    response = JSONResponse(content=MessageResponse(message="Signed out").model_dump())
    _clear_session_cookie(response)
    return response


@router.get("/auth/me", response_model=UserResponse)
async def current_user(request: Request):
    username = _require_current_user(request)
    return UserResponse(username=username)


@router.post("/reports", response_model=ReportCreateResponse)
async def create_report(request: Request, payload: DueDiligenceRequest):
    username = _require_current_user(request)
    research_query = _build_research_query(
        payload.company_name,
        payload.focus,
        payload.target_role,
    )

    task = TASK_RUNTIME.create_task(
        task_type=payload.task_type or "due_diligence",
        owner=username,
        params={
            "company_name": payload.company_name,
            "focus": payload.focus,
            "target_role": payload.target_role,
            "max_analysts": payload.max_analysts,
        },
    )
    TASK_RUNTIME.emit_event(
        task["id"],
        "workflow.configured",
        {
            "max_analysts": payload.max_analysts,
        },
    )

    task = TASK_RUNTIME.update_task(task["id"], status="running_generation", error="")
    _start_generation_job(task["id"], research_query, payload.max_analysts)

    return ReportCreateResponse(task=_task_response(task))


@router.get("/tasks", response_model=TaskListResponse)
async def list_my_tasks(request: Request):
    username = _require_current_user(request)
    tasks = [_task_response(task) for task in TASK_RUNTIME.list_tasks_by_owner(username)]
    return TaskListResponse(tasks=tasks)


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task_status(request: Request, task_id: str):
    username = _require_current_user(request)
    task = _require_owned_task(task_id, username)
    return _task_response(task)


@router.get("/tasks/{task_id}/events", response_model=TaskEventsResponse)
async def get_task_events(request: Request, task_id: str, limit: int = 50):
    username = _require_current_user(request)
    _require_owned_task(task_id, username)
    return TaskEventsResponse(
        task_id=task_id,
        events=TASK_RUNTIME.list_events(task_id, limit),
    )


@router.get("/tasks/{task_id}/metrics")
async def get_task_metrics(request: Request, task_id: str):
    username = _require_current_user(request)
    task = _require_owned_task(task_id, username)
    from harness.observability.metrics import get_ledger
    from harness.observability.tracer import get_tracer
    from server.api.models.request_models import MetricsResponse

    # Serve stored snapshot for completed tasks (survives server restarts)
    snapshot = task.get("metrics_snapshot")
    if snapshot and isinstance(snapshot, dict) and snapshot.get("call_count", 0) > 0:
        return MetricsResponse(**{k: v for k, v in snapshot.items() if k != "budget_cap_usd"})

    ledger = get_ledger(task_id)
    tracer = get_tracer(task_id)

    # Merge ledger (token/cost) + tracer (per-node timing) into one response
    ledger_summary = ledger.summary()

    tracer_summary = tracer.summary()
    tracer_by_node: dict[str, dict] = {}
    for node_name, stats in (tracer_summary.get("by_node") or {}).items():
        tracer_by_node[node_name] = {
            "count": stats.get("count", 0),
            "total_duration_ms": stats.get("total_duration_ms", 0),
            "avg_duration_ms": (
                round(stats["total_duration_ms"] / stats["count"])
                if stats.get("count") else 0
            ),
            "errors": stats.get("errors", 0),
        }

    # Merge per-node data: ledger has token/cost, tracer has timing
    merged_by_node: dict[str, dict] = {}
    ledger_nodes = ledger_summary.get("by_node") or {}
    for node_name in set(list(ledger_nodes.keys()) + list(tracer_by_node.keys())):
        ln = ledger_nodes.get(node_name) or {}
        tn = tracer_by_node.get(node_name) or {}
        merged_by_node[node_name] = {
            "calls": ln.get("calls", 0) or tn.get("count", 0),
            "prompt_tokens": ln.get("prompt_tokens", 0),
            "completion_tokens": ln.get("completion_tokens", 0),
            "total_tokens": ln.get("total_tokens", 0),
            "estimated_cost": ln.get("estimated_cost", 0.0),
            "total_duration_ms": tn.get("total_duration_ms", 0),
            "avg_duration_ms": tn.get("avg_duration_ms", 0),
            "errors": tn.get("errors", 0),
        }

    # Compute total duration from tracer
    total_duration_ms = tracer_summary.get("total_duration_ms", 0)

    return MetricsResponse(
        call_count=ledger_summary.get("call_count", 0),
        total_prompt_tokens=ledger_summary.get("total_prompt_tokens", 0),
        total_completion_tokens=ledger_summary.get("total_completion_tokens", 0),
        total_tokens=ledger_summary.get("total_tokens", 0),
        total_latency_ms=total_duration_ms or ledger_summary.get("total_latency_ms", 0),
        estimated_cost_usd=ledger_summary.get("estimated_cost_usd", 0.0),
        budget_cap_usd=ledger_summary.get("budget_cap_usd"),
        budget_remaining_usd=ledger_summary.get("budget_remaining_usd"),
        over_budget=ledger_summary.get("over_budget", False),
        by_node=merged_by_node,
        by_model=ledger_summary.get("by_model", {}),
    )


@router.post("/tasks/{task_id}/feedback", response_model=TaskActionResponse)
async def submit_feedback(request: Request, task_id: str, payload: FeedbackRequest):
    username = _require_current_user(request)
    task = _require_owned_task(task_id, username)

    thread_id = task.get("thread_id", "")
    if not thread_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Report generation is still in progress; try again shortly.",
        )

    normalized_feedback = (payload.feedback or "").strip()
    task = TASK_RUNTIME.update_task(
        task_id,
        last_feedback=normalized_feedback,
        status="running_feedback",
        error="",
    )
    TASK_RUNTIME.emit_event(
        task_id,
        "feedback.submitted",
        {"feedback_preview": normalized_feedback[:120]},
    )
    if normalized_feedback:
        TASK_RUNTIME.emit_event(task_id, "analyst.regenerated", {"task_id": task_id})
    _start_feedback_job(task_id, thread_id, normalized_feedback)
    return TaskActionResponse(task=_task_response(task))


@router.post("/tasks/{task_id}/retry", response_model=RetryResponse)
async def retry_task(request: Request, task_id: str):
    username = _require_current_user(request)
    task = _require_owned_task(task_id, username)
    if task.get("status") != "failed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only failed tasks can be retried",
        )

    failed_stage = task.get("failed_stage", "")
    if failed_stage == "running_generation":
        thread_id = task.get("thread_id", "")
        if thread_id:
            # Resume from the last checkpoint (SqliteSaver persists it across restarts)
            TASK_RUNTIME.update_task(task_id, status="running_generation", error="")
            _resume_generation_job(task_id, thread_id)
            return RetryResponse(
                message="Retry: resuming from last checkpoint",
                task_id=task_id,
            )
        # No thread_id — start fresh (pre-existing task from before this fix)
        research_query = _build_research_query(
            task.get("company_name", ""),
            task.get("focus", ""),
            task.get("target_role", ""),
        )
        TASK_RUNTIME.update_task(task_id, status="running_generation", error="")
        _start_generation_job(task_id, research_query, int(task.get("max_analysts", 3) or 3))
        return RetryResponse(
            message="Retry: starting fresh (no checkpoint available)",
            task_id=task_id,
        )
    if failed_stage == "running_feedback":
        thread_id = task.get("thread_id", "")
        feedback = task.get("last_feedback", "")
        if not thread_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Missing thread_id; cannot retry feedback stage",
            )
        TASK_RUNTIME.update_task(task_id, status="running_feedback", error="")
        _resume_feedback_job(task_id, thread_id, feedback)
        return RetryResponse(
            message="Retry: resuming from last checkpoint",
            task_id=task_id,
        )
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="No retriable failed stage found",
    )


def _download_report_for_task(task: dict, file_name: str):
    allowed_file_names = {
        task.get("docx_path", "").split("\\")[-1].split("/")[-1],
        task.get("pdf_path", "").split("\\")[-1].split("/")[-1],
    }
    if file_name not in allowed_file_names:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="File does not belong to this task")

    service = ReportService()
    file_response = service.download_file(file_name)
    if not hasattr(file_response, "path"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"File not found: {file_name}")
    return file_response


@router.get("/tasks/{task_id}/files/{file_name}")
async def download_report_for_task(request: Request, task_id: str, file_name: str):
    username = _require_current_user(request)
    task = _require_owned_task(task_id, username)
    return _download_report_for_task(task, file_name)
