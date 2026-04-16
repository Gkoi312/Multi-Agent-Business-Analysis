from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse

from app.api.models.request_models import (
    DependencyUpdateRequest,
    DueDiligenceRequest,
    FeedbackRequest,
    MessageResponse,
    ReportCreateResponse,
    RetryResponse,
    TaskActionResponse,
    TaskCreatedResponse,
    TaskEventsResponse,
    TaskListResponse,
    TaskResponse,
    UserResponse,
    LoginRequest,
    SignupRequest,
)
from app.api.services.report_service import ReportService
from app.api.services.session_store import SESSION_STORE
from app.api.services.task_runtime import TASK_RUNTIME
from app.config import (
    SESSION_COOKIE_MAX_AGE,
    SESSION_COOKIE_NAME,
    SESSION_COOKIE_SAMESITE,
    SESSION_COOKIE_SECURE,
)
from app.database.db_config import (
    SessionLocal,
    User,
    hash_password,
    verify_password,
)


router = APIRouter(prefix="/api", tags=["api"])
DEFAULT_METRICS = {
    "latency": {"generation_ms": 0, "feedback_ms": 0, "created_to_completed_ms": 0},
    "tokens": {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "llm_calls": 0,
        "by_node": {},
    },
}


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
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录或会话已失效")
    return username


def _task_owned_by(task: dict, username: str) -> bool:
    return bool(task) and task.get("owner") == username


def _require_owned_task(task_id: str, username: str) -> dict:
    task = TASK_RUNTIME.get_task(task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    if not _task_owned_by(task, username):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该任务")
    return task


def _build_research_query(company_name: str, focus: str, target_role: str) -> str:
    return f"""请对这家公司进行尽职调查研究：{company_name}
重点关注：{focus or "商业模式、公司规模与发展轨迹，以及核心风险因素"}
目标岗位语境：{target_role or "未指定"}
请输出一份结构化分析，围绕业务、规模/增长和风险给出清晰且基于证据的结论。"""


def _task_response(task: dict) -> TaskResponse:
    return TaskResponse(**task)


def _start_generation_job(task_id: str, research_query: str, max_analysts: int):
    def _start_generation():
        service = ReportService()
        task = TASK_RUNTIME.get_task(task_id) or {}
        company_name = task.get("company_name", "")
        result = service.start_report_generation(
            research_query,
            max_analysts,
            company_name,
            max_num_turns=1,
        )
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
            "risk_summary": result.get(
                "risk_summary", {"high": 0, "medium": 0, "low": 0}
            ),
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


@router.post("/auth/signup", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def signup(payload: SignupRequest):
    db = next(get_db())
    existing_user = db.query(User).filter(User.username == payload.username).first()
    if existing_user:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已存在")

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
            detail="用户名或密码错误",
        )

    session_id = SESSION_STORE.create(user.username)
    response = JSONResponse(content=UserResponse(username=user.username).model_dump())
    _set_session_cookie(response, session_id)
    return response


@router.post("/auth/logout", response_model=MessageResponse)
async def logout(request: Request):
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    SESSION_STORE.delete(session_id)
    response = JSONResponse(content=MessageResponse(message="已退出登录").model_dump())
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
        "company_due_diligence",
        company_name=payload.company_name,
        owner=username,
        focus=payload.focus,
        target_role=payload.target_role,
    )

    if not TASK_RUNTIME.is_unblocked(task):
        task = TASK_RUNTIME.update_task(task["id"], status="blocked")
    else:
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


@router.post("/tasks/{task_id}/feedback", response_model=TaskActionResponse)
async def submit_feedback(request: Request, task_id: str, payload: FeedbackRequest):
    username = _require_current_user(request)
    task = _require_owned_task(task_id, username)

    thread_id = task.get("thread_id", "")
    if not thread_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="报告仍在生成中，请稍后再试。",
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


@router.post("/tasks/{task_id}/claim", response_model=TaskActionResponse)
async def claim_task(request: Request, task_id: str):
    username = _require_current_user(request)
    _require_owned_task(task_id, username)
    updated = TASK_RUNTIME.claim_task(task_id, username)
    return TaskActionResponse(task=_task_response(updated))


@router.post("/tasks/{task_id}/dependencies", response_model=TaskActionResponse)
async def set_task_dependencies(
    request: Request,
    task_id: str,
    payload: DependencyUpdateRequest,
):
    username = _require_current_user(request)
    _require_owned_task(task_id, username)
    updated = TASK_RUNTIME.set_blocked_by(task_id, payload.blocked_by)
    return TaskActionResponse(task=_task_response(updated))


@router.post("/tasks/{task_id}/retry", response_model=RetryResponse)
async def retry_task(request: Request, task_id: str):
    username = _require_current_user(request)
    task = _require_owned_task(task_id, username)
    if task.get("status") != "failed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="只有失败任务才可以重试",
        )
    if not TASK_RUNTIME.is_unblocked(task):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="任务被依赖项阻塞，暂时无法重试",
        )

    failed_stage = task.get("failed_stage", "")
    if failed_stage == "running_generation":
        research_query = _build_research_query(
            task.get("company_name", ""),
            task.get("focus", ""),
            task.get("target_role", ""),
        )
        TASK_RUNTIME.update_task(task_id, status="running_generation", error="")
        _start_generation_job(task_id, research_query, 3)
        return RetryResponse(
            message="已开始重试生成阶段",
            task_id=task_id,
        )
    if failed_stage == "running_feedback":
        thread_id = task.get("thread_id", "")
        feedback = task.get("last_feedback", "")
        if not thread_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="缺少 thread_id，无法重试反馈阶段",
            )
        TASK_RUNTIME.update_task(task_id, status="running_feedback", error="")
        _start_feedback_job(task_id, thread_id, feedback)
        return RetryResponse(
            message="已开始重试反馈阶段",
            task_id=task_id,
        )
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="未找到可重试的失败阶段",
    )


def _download_report_for_task(task: dict, file_name: str):
    allowed_file_names = {
        task.get("docx_path", "").split("\\")[-1].split("/")[-1],
        task.get("pdf_path", "").split("\\")[-1].split("/")[-1],
    }
    if file_name not in allowed_file_names:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="该文件不属于当前任务")

    service = ReportService()
    file_response = service.download_file(file_name)
    if not hasattr(file_response, "path"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"未找到文件：{file_name}")
    return file_response


@router.get("/files/{file_name}")
async def download_report(request: Request, file_name: str, task_id: str = Query(...)):
    username = _require_current_user(request)
    task = _require_owned_task(task_id, username)
    return _download_report_for_task(task, file_name)


@router.get("/tasks/{task_id}/files/{file_name}")
async def download_report_for_task(request: Request, task_id: str, file_name: str):
    username = _require_current_user(request)
    task = _require_owned_task(task_id, username)
    return _download_report_for_task(task, file_name)
