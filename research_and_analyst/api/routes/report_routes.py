from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from research_and_analyst.database.db_config import SessionLocal, User, hash_password, verify_password
from research_and_analyst.api.services.report_service import ReportService
from research_and_analyst.api.services.task_runtime import TASK_RUNTIME

router = APIRouter()
SESSIONS = {}
DEFAULT_METRICS = {
    "latency": {"generation_ms": 0, "feedback_ms": 0, "created_to_completed_ms": 0},
    "tokens": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "llm_calls": 0, "by_node": {}},
}

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _get_current_user(request: Request) -> str | None:
    session_id = request.cookies.get("session_id")
    if not session_id:
        return None
    return SESSIONS.get(session_id)


def _task_owned_by(task: dict, username: str) -> bool:
    return bool(task) and task.get("owner") == username


def _start_generation_job(task_id: str, research_query: str):
    def _start_generation():
        service = ReportService()
        task = TASK_RUNTIME.get_task(task_id) or {}
        company_name = task.get("company_name", "")
        result = service.start_report_generation(research_query, 3, company_name, max_num_turns=1)
        analysts_preview = result.get("analysts_preview", [])
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


def _start_feedback_job(task_id: str, thread_id: str, feedback: str):
    def _continue_with_feedback():
        service = ReportService()
        current_task = TASK_RUNTIME.get_task(task_id) or {}
        current_analyst_version = int(current_task.get("analyst_version", 0) or 0)
        feedback_result = service.submit_feedback(thread_id, feedback)
        if feedback_result.get("awaiting_feedback"):
            next_analyst_version = current_analyst_version + (1 if feedback.strip() else 0)
            return {
                "next_status": "awaiting_feedback",
                "analysts_preview": feedback_result.get("analysts_preview", []),
                "analyst_version": next_analyst_version,
                "metrics": {
                    **current_task.get("metrics", DEFAULT_METRICS),
                    "latency": {
                        **current_task.get("metrics", DEFAULT_METRICS).get("latency", {}),
                        "feedback_ms": int(feedback_result.get("feedback_elapsed_ms", 0)),
                    },
                },
                "failed_stage": "",
            }
        result = service.get_report_status(thread_id)
        return {
            "docx_path": result.get("docx_path", ""),
            "pdf_path": result.get("pdf_path", ""),
            "risk_summary": result.get("risk_summary", {"high": 0, "medium": 0, "low": 0}),
            "final_recommendation": result.get("final_recommendation", ""),
            "analysts_preview": feedback_result.get("analysts_preview", []),
            "analyst_version": current_analyst_version,
            "metrics": {
                **current_task.get("metrics", DEFAULT_METRICS),
                "tokens": result.get("llm_usage", DEFAULT_METRICS["tokens"]),
                "latency": {
                    **current_task.get("metrics", DEFAULT_METRICS).get("latency", {}),
                    "feedback_ms": int(feedback_result.get("feedback_elapsed_ms", 0)),
                },
            },
            "failed_stage": "",
        }

    TASK_RUNTIME.run_in_background(
        task_id=task_id,
        started_status="running_feedback",
        finished_status="completed",
        work=_continue_with_feedback,
    )

# ------------------ AUTH ROUTES ------------------ #

@router.get("/", response_class=HTMLResponse)
async def show_login(request: Request):
    return request.app.templates.TemplateResponse(request, "login.html", {})

@router.post("/login", response_class=HTMLResponse)
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    db = next(get_db())
    user = db.query(User).filter(User.username == username).first()

    if user and verify_password(password, user.password):
        session_id = f"{username}_session"
        SESSIONS[session_id] = username
        response = RedirectResponse(url="/dashboard", status_code=302)
        response.set_cookie(key="session_id", value=session_id)
        return response

    return request.app.templates.TemplateResponse(
        request,
        "login.html",
        {"error": "Invalid username or password"},
    )

@router.get("/signup", response_class=HTMLResponse)
async def show_signup(request: Request):
    return request.app.templates.TemplateResponse(request, "signup.html", {})

@router.post("/signup", response_class=HTMLResponse)
async def signup(request: Request, username: str = Form(...), password: str = Form(...)):
    db = next(get_db())
    existing_user = db.query(User).filter(User.username == username).first()
    if existing_user:
        return request.app.templates.TemplateResponse(
            request, "signup.html", {"error": "Username already exists"}
        )

    hashed_pw = hash_password(password)
    new_user = User(username=username, password=hashed_pw)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return RedirectResponse(url="/", status_code=302)

# ------------------ REPORT ROUTES ------------------ #

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    session_id = request.cookies.get("session_id")
    if session_id not in SESSIONS:
        return RedirectResponse(url="/")
    return request.app.templates.TemplateResponse(request, "dashboard.html", {"user": SESSIONS[session_id]})


@router.get("/my_tasks", response_class=HTMLResponse)
async def my_tasks_page(request: Request):
    username = _get_current_user(request)
    if not username:
        return RedirectResponse(url="/", status_code=302)
    tasks = TASK_RUNTIME.list_tasks_by_owner(username)
    return request.app.templates.TemplateResponse(
        request,
        "tasks.html",
        {"user": username, "tasks": tasks},
    )


@router.post("/generate_report", response_class=HTMLResponse)
async def generate_report(
    request: Request,
    company_name: str = Form(...),
    focus: str = Form(""),
    target_role: str = Form(""),
):
    username = _get_current_user(request)
    if not username:
        return RedirectResponse(url="/", status_code=302)

    research_query = f"""Perform due diligence research on the company: {company_name}
Focus areas: {focus or "business model, company scale and development trajectory, and risk factors"}
Target role context: {target_role or "not specified"}
Return a structured analysis with clear, evidence-based sections on business, scale/growth, and risks."""

    task = TASK_RUNTIME.create_task(
        "company_due_diligence",
        company_name=company_name,
        owner=username,
        focus=focus,
        target_role=target_role,
    )

    if not TASK_RUNTIME.is_unblocked(task):
        TASK_RUNTIME.update_task(task["id"], status="blocked")
    else:
        _start_generation_job(task["id"], research_query)

    return request.app.templates.TemplateResponse(
        request,
        "report_progress.html",
        {
            "company_name": company_name,
            "focus": focus,
            "target_role": target_role,
            "feedback": "",
            "doc_path": "",
            "pdf_path": "",
            "thread_id": "",
            "task_id": task["id"],
            "status": "running_generation",
            "risk_summary": {"high": 0, "medium": 0, "low": 0},
            "final_recommendation": "",
            "error": "",
            "failed_stage": "",
            "auto_retry": {"running_generation": {"attempted": 0, "max": 1}, "running_feedback": {"attempted": 0, "max": 1}},
            "metrics": DEFAULT_METRICS,
            "analysts_preview": [],
            "analyst_version": 0,
        },
    )

@router.post("/submit_feedback", response_class=HTMLResponse)
async def submit_feedback(
    request: Request,
    company_name: str = Form(...),
    focus: str = Form(""),
    target_role: str = Form(""),
    feedback: str = Form(""),
    task_id: str = Form(...),
):
    username = _get_current_user(request)
    if not username:
        return RedirectResponse(url="/", status_code=302)

    task = TASK_RUNTIME.get_task(task_id)
    if not task:
        return request.app.templates.TemplateResponse(
            request,
            "report_progress.html",
            {
                "company_name": company_name,
                "focus": focus,
                "target_role": target_role,
                "feedback": feedback,
                "task_id": task_id,
                "status": "failed",
                "risk_summary": {"high": 0, "medium": 0, "low": 0},
                "final_recommendation": "",
                "error": "Task not found",
                "metrics": DEFAULT_METRICS,
                "failed_stage": "",
                "auto_retry": {"running_generation": {"attempted": 0, "max": 1}, "running_feedback": {"attempted": 0, "max": 1}},
                "analysts_preview": [],
                "analyst_version": 0,
            },
        )
    if not _task_owned_by(task, username):
        return RedirectResponse(url="/dashboard", status_code=302)

    thread_id = task.get("thread_id", "")
    if not thread_id:
        return request.app.templates.TemplateResponse(
            request,
            "report_progress.html",
            {
                "company_name": company_name,
                "focus": focus,
                "target_role": target_role,
                "feedback": feedback,
                "task_id": task_id,
                "status": task.get("status", "pending"),
                "risk_summary": task.get("risk_summary", {"high": 0, "medium": 0, "low": 0}),
                "final_recommendation": task.get("final_recommendation", ""),
                "error": "Generation is still running. Please wait and retry.",
                "metrics": task.get("metrics", DEFAULT_METRICS),
                "failed_stage": task.get("failed_stage", ""),
                "auto_retry": task.get("auto_retry", {}),
                "analysts_preview": task.get("analysts_preview", []),
                "analyst_version": task.get("analyst_version", 0),
            },
        )

    normalized_feedback = feedback.strip()
    TASK_RUNTIME.update_task(task_id, last_feedback=normalized_feedback)
    TASK_RUNTIME.emit_event(task_id, "feedback.submitted", {"feedback_preview": normalized_feedback[:120]})
    if normalized_feedback:
        TASK_RUNTIME.emit_event(task_id, "analyst.regenerated", {"task_id": task_id})
    _start_feedback_job(task_id, thread_id, normalized_feedback)

    return request.app.templates.TemplateResponse(
        request,
        "report_progress.html",
        {
            "company_name": company_name,
            "focus": focus,
            "target_role": target_role,
            "feedback": normalized_feedback,
            "doc_path": "",
            "pdf_path": "",
            "thread_id": thread_id,
            "task_id": task_id,
            "status": "running_feedback",
            "risk_summary": task.get("risk_summary", {"high": 0, "medium": 0, "low": 0}),
            "final_recommendation": task.get("final_recommendation", ""),
            "error": "",
            "metrics": task.get("metrics", DEFAULT_METRICS),
            "failed_stage": task.get("failed_stage", ""),
            "auto_retry": task.get("auto_retry", {}),
            "analysts_preview": task.get("analysts_preview", []),
            "analyst_version": task.get("analyst_version", 0),
        },
    )


@router.get("/report_progress/{task_id}", response_class=HTMLResponse)
async def report_progress(request: Request, task_id: str):
    username = _get_current_user(request)
    if not username:
        return RedirectResponse(url="/", status_code=302)

    task = TASK_RUNTIME.get_task(task_id)
    if not task:
        return request.app.templates.TemplateResponse(
            request,
            "report_progress.html",
            {
            "company_name": "Unknown",
            "focus": "",
            "target_role": "",
                "feedback": "",
                "doc_path": "",
                "pdf_path": "",
                "thread_id": "",
                "task_id": task_id,
                "status": "failed",
                "risk_summary": {"high": 0, "medium": 0, "low": 0},
                "final_recommendation": "",
                "error": "Task not found",
                "metrics": DEFAULT_METRICS,
                "failed_stage": "",
                "auto_retry": {"running_generation": {"attempted": 0, "max": 1}, "running_feedback": {"attempted": 0, "max": 1}},
                "analysts_preview": [],
                "analyst_version": 0,
            },
        )
    if not _task_owned_by(task, username):
        return RedirectResponse(url="/dashboard", status_code=302)

    return request.app.templates.TemplateResponse(
        request,
        "report_progress.html",
        {
            "company_name": task.get("company_name", ""),
            "focus": task.get("focus", ""),
            "target_role": task.get("target_role", ""),
            "feedback": "",
            "doc_path": task.get("docx_path", ""),
            "pdf_path": task.get("pdf_path", ""),
            "thread_id": task.get("thread_id", ""),
            "task_id": task_id,
            "status": task.get("status", "pending"),
            "risk_summary": task.get("risk_summary", {"high": 0, "medium": 0, "low": 0}),
            "final_recommendation": task.get("final_recommendation", ""),
            "error": task.get("error", ""),
            "metrics": task.get("metrics", DEFAULT_METRICS),
            "failed_stage": task.get("failed_stage", ""),
            "auto_retry": task.get("auto_retry", {}),
            "analysts_preview": task.get("analysts_preview", []),
            "analyst_version": task.get("analyst_version", 0),
        },
    )


@router.get("/tasks/{task_id}")
async def get_task_status(request: Request, task_id: str):
    username = _get_current_user(request)
    if not username:
        return {"error": "Unauthorized"}

    task = TASK_RUNTIME.get_task(task_id)
    if not task:
        return {"error": f"Task {task_id} not found"}
    if not _task_owned_by(task, username):
        return {"error": "Forbidden"}
    return task


@router.get("/tasks")
async def list_my_tasks(request: Request):
    username = _get_current_user(request)
    if not username:
        return {"error": "Unauthorized"}
    return {"tasks": TASK_RUNTIME.list_tasks_by_owner(username)}


@router.get("/tasks/{task_id}/events")
async def get_task_events(request: Request, task_id: str, limit: int = 50):
    username = _get_current_user(request)
    if not username:
        return {"error": "Unauthorized"}
    task = TASK_RUNTIME.get_task(task_id)
    if not task:
        return {"error": f"Task {task_id} not found"}
    if not _task_owned_by(task, username):
        return {"error": "Forbidden"}
    return {"task_id": task_id, "events": TASK_RUNTIME.list_events(task_id, limit)}


@router.post("/tasks/{task_id}/claim")
async def claim_task(request: Request, task_id: str):
    username = _get_current_user(request)
    if not username:
        return {"error": "Unauthorized"}
    task = TASK_RUNTIME.get_task(task_id)
    if not task:
        return {"error": f"Task {task_id} not found"}
    if not _task_owned_by(task, username):
        return {"error": "Forbidden"}
    updated = TASK_RUNTIME.claim_task(task_id, username)
    return {"task": updated}


@router.post("/tasks/{task_id}/dependencies")
async def set_task_dependencies(request: Request, task_id: str, blocked_by: str = Form("")):
    username = _get_current_user(request)
    if not username:
        return {"error": "Unauthorized"}
    task = TASK_RUNTIME.get_task(task_id)
    if not task:
        return {"error": f"Task {task_id} not found"}
    if not _task_owned_by(task, username):
        return {"error": "Forbidden"}
    dep_ids = [x.strip() for x in blocked_by.split(",") if x.strip()]
    updated = TASK_RUNTIME.set_blocked_by(task_id, dep_ids)
    return {"task": updated}


@router.post("/tasks/{task_id}/retry")
async def retry_task(request: Request, task_id: str):
    username = _get_current_user(request)
    if not username:
        return {"error": "Unauthorized"}
    task = TASK_RUNTIME.get_task(task_id)
    if not task:
        return {"error": f"Task {task_id} not found"}
    if not _task_owned_by(task, username):
        return {"error": "Forbidden"}
    if task.get("status") != "failed":
        return {"error": "Only failed tasks can be retried"}
    if not TASK_RUNTIME.is_unblocked(task):
        return {"error": "Task is blocked by dependencies"}

    failed_stage = task.get("failed_stage", "")
    if failed_stage == "running_generation":
        company_name = task.get("company_name", "")
        focus = task.get("focus", "")
        target_role = task.get("target_role", "")
        research_query = f"""Perform due diligence research on the company: {company_name}
Focus areas: {focus or "business model, company scale and development trajectory, and risk factors"}
Target role context: {target_role or "not specified"}
Return a structured analysis with clear, evidence-based sections on business, scale/growth, and risks."""
        _start_generation_job(task_id, research_query)
        return {"message": "Retry started for generation stage", "task_id": task_id}
    if failed_stage == "running_feedback":
        thread_id = task.get("thread_id", "")
        feedback = task.get("last_feedback", "")
        if not thread_id:
            return {"error": "Cannot retry feedback stage without thread_id"}
        _start_feedback_job(task_id, thread_id, feedback)
        return {"message": "Retry started for feedback stage", "task_id": task_id}
    return {"error": "No retryable failed stage found"}

@router.get("/download/{file_name}", response_class=HTMLResponse)
async def download_report(request: Request, file_name: str, task_id: str = ""):
    username = _get_current_user(request)
    if not username:
        return RedirectResponse(url="/", status_code=302)

    task = TASK_RUNTIME.get_task(task_id) if task_id else None
    if not task or not _task_owned_by(task, username):
        return RedirectResponse(url="/dashboard", status_code=302)

    allowed_file_names = {
        task.get("docx_path", "").split("\\")[-1].split("/")[-1],
        task.get("pdf_path", "").split("\\")[-1].split("/")[-1],
    }
    if file_name not in allowed_file_names:
        return {"error": "File not allowed for this task"}

    service = ReportService()
    file_response = service.download_file(file_name)
    if file_response:
        return file_response
    return {"error": f"File {file_name} not found"}
