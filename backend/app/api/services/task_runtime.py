import json
import os
import threading
import time
import uuid
from typing import Any, Callable

from app.logger import GLOBAL_LOGGER
from app.config import RUNTIME_DIR


class TaskRuntime:
    """Minimal persistent task runtime for async report jobs."""

    def __init__(self):
        self.runtime_dir = os.fspath(RUNTIME_DIR)
        os.makedirs(self.runtime_dir, exist_ok=True)
        self.tasks_path = os.path.join(self.runtime_dir, "tasks.json")
        self.events_path = os.path.join(self.runtime_dir, "task_events.jsonl")
        self._lock = threading.Lock()
        self.logger = GLOBAL_LOGGER.bind(module="TaskRuntime")

        if not os.path.exists(self.tasks_path):
            self._write_tasks({})

    def _read_tasks(self) -> dict[str, dict[str, Any]]:
        with open(self.tasks_path, "r", encoding="utf-8") as f:
            data = f.read().strip()
        if not data:
            return {}
        return json.loads(data)

    def _write_tasks(self, tasks: dict[str, dict[str, Any]]) -> None:
        with open(self.tasks_path, "w", encoding="utf-8") as f:
            json.dump(tasks, f, ensure_ascii=True, indent=2)

    def _emit_event(self, task_id: str, event: str, payload: dict[str, Any] | None = None) -> None:
        event_row = {
            "ts": time.time(),
            "task_id": task_id,
            "event": event,
            "payload": payload or {},
        }
        with open(self.events_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event_row, ensure_ascii=True) + "\n")

    def emit_event(self, task_id: str, event: str, payload: dict[str, Any] | None = None) -> None:
        self._emit_event(task_id, event, payload)

    def create_task(
        self,
        company_name: str,
        owner: str,
        focus: str = "",
        target_role: str = "",
        max_analysts: int = 3,
        industry_pack: str = "",
    ) -> dict[str, Any]:
        task_id = str(uuid.uuid4())
        now = time.time()
        task = {
            "id": task_id,
            "company_name": company_name,
            "focus": focus,
            "target_role": target_role,
            "max_analysts": int(max_analysts or 3),
            "industry_pack": (industry_pack or "").strip(),
            "owner": owner,
            "status": "pending",
            "thread_id": "",
            "analysts_preview": [],
            "analyst_version": 0,
            "docx_path": "",
            "pdf_path": "",
            "error": "",
            "failed_stage": "",
            "last_feedback": "",
            "risk_summary": {"high": 0, "medium": 0, "low": 0},
            "final_recommendation": "",
            "report_review_status": "",
            "report_review_summary": "",
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            tasks = self._read_tasks()
            tasks[task_id] = task
            self._write_tasks(tasks)
        self._emit_event(
            task_id,
            "task.created",
            {
                "company_name": company_name,
                "focus": focus,
                "target_role": target_role,
                "max_analysts": int(max_analysts or 3),
                "industry_pack": (industry_pack or "").strip(),
                "owner": owner,
            },
        )
        return task

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            tasks = self._read_tasks()
            return tasks.get(task_id)

    def list_tasks_by_owner(self, owner: str) -> list[dict[str, Any]]:
        with self._lock:
            tasks = self._read_tasks()
            items = [t for t in tasks.values() if t.get("owner") == owner]
        return sorted(items, key=lambda t: t.get("updated_at", 0), reverse=True)

    def update_task(self, task_id: str, **updates: Any) -> dict[str, Any]:
        with self._lock:
            tasks = self._read_tasks()
            task = tasks.get(task_id)
            if task is None:
                raise ValueError(f"Task {task_id} not found")
            task.update(updates)
            task["updated_at"] = time.time()
            tasks[task_id] = task
            self._write_tasks(tasks)
            return task

    def list_events(self, task_id: str, limit: int = 50) -> list[dict[str, Any]]:
        if not os.path.exists(self.events_path):
            return []
        limit = max(1, min(int(limit or 50), 500))
        rows: list[dict[str, Any]] = []
        with open(self.events_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("task_id") == task_id:
                    rows.append(row)
        return rows[-limit:]

    def recover_interrupted_tasks(self) -> int:
        """
        Mark stale running tasks as failed after process restart.
        This prevents UI from being stuck on running states forever.
        """
        updated_count = 0
        recovered_ids: list[str] = []
        with self._lock:
            tasks = self._read_tasks()
            now = time.time()
            for task_id, task in tasks.items():
                status = str(task.get("status", ""))
                if status not in {"running_generation", "running_feedback"}:
                    continue
                task["status"] = "failed"
                task["failed_stage"] = status
                task["error"] = "Task interrupted by service reload or restart; please retry."
                task["updated_at"] = now
                tasks[task_id] = task
                updated_count += 1
                recovered_ids.append(task_id)
            if updated_count:
                self._write_tasks(tasks)

        if updated_count:
            self.logger.warning("Recovered interrupted tasks", count=updated_count)
            for task_id in recovered_ids:
                self._emit_event(
                    task_id,
                    "task.interrupted",
                    {"message": "Task record recovered after service reload or restart"},
                )
        return updated_count

    def run_in_background(
        self,
        task_id: str,
        started_status: str,
        finished_status: str,
        work: Callable[[], dict[str, Any]],
    ) -> None:
        def _runner():
            self.update_task(task_id, status=started_status, error="")
            self._emit_event(task_id, "task.started", {"status": started_status})
            try:
                result = work() or {}
                resolved_status = str(result.pop("next_status", finished_status) or finished_status)
                result["error"] = ""
                result["failed_stage"] = ""
                self.update_task(task_id, status=resolved_status, **result)
                self._emit_event(
                    task_id,
                    "task.completed",
                    {"status": resolved_status},
                )
            except Exception as exc:
                self.logger.error("Background task failed", task_id=task_id, error=str(exc))
                self.update_task(
                    task_id,
                    status="failed",
                    error=str(exc),
                    failed_stage=started_status,
                )
                self._emit_event(
                    task_id,
                    "task.failed",
                    {"error": str(exc), "failed_stage": started_status},
                )

        threading.Thread(target=_runner, daemon=True).start()


TASK_RUNTIME = TaskRuntime()
