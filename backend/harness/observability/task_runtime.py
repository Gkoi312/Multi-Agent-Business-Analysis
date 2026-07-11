"""
Harness Task Runtime — generic async job persistence and lifecycle management.

Migrated from ``app.api.services.task_runtime``.  All domain-specific fields
have been moved into a ``params`` dict so the runtime can serve any task type.
"""
import json
import os
import threading
import time
import uuid
from typing import Any, Callable

from app.logger import GLOBAL_LOGGER
from app.config import RUNTIME_DIR


class TaskRuntime:
    """Persistent runtime for async agent-task jobs (harness level).

    Stores tasks as JSON on disk and emits an append-only event log so the
    front-end (or any observer) can render progress in near-real-time.
    """

    def __init__(self):
        self.runtime_dir = os.fspath(RUNTIME_DIR)
        os.makedirs(self.runtime_dir, exist_ok=True)
        self.tasks_path = os.path.join(self.runtime_dir, "tasks.json")
        self.events_path = os.path.join(self.runtime_dir, "task_events.jsonl")
        self._lock = threading.Lock()
        self.logger = GLOBAL_LOGGER.bind(module="TaskRuntime")

        if not os.path.exists(self.tasks_path):
            self._write_tasks({})

    # ------------------------------------------------------------------
    # Internal persistence helpers
    # ------------------------------------------------------------------

    def _read_tasks(self) -> dict[str, dict[str, Any]]:
        with open(self.tasks_path, "r", encoding="utf-8") as f:
            data = f.read().strip()
        if not data:
            return {}
        return json.loads(data)

    def _write_tasks(self, tasks: dict[str, dict[str, Any]]) -> None:
        with open(self.tasks_path, "w", encoding="utf-8") as f:
            json.dump(tasks, f, ensure_ascii=True, indent=2)

    # ------------------------------------------------------------------
    # Events (append-only JSONL)
    # ------------------------------------------------------------------

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
        """Public helper so route-layer code can emit domain events."""
        self._emit_event(task_id, event, payload)

    # ------------------------------------------------------------------
    # Task CRUD
    # ------------------------------------------------------------------

    def create_task(self, task_type: str, owner: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Create a new task record.

        *task_type*  –  arbitrary label (``"due_diligence"``, ``"stock_analysis"``, …).
        *owner*      –  username / principal id.
        *params*     –  free-form dict with domain-specific configuration.

        The returned ``dict`` is the stored task.  It always contains the
        generic keys listed below; *params* is flattened into the top level
        so existing front-ends can read known keys without changes.
        """
        task_id = str(uuid.uuid4())
        now = time.time()
        _params = dict(params or {})

        task: dict[str, Any] = {
            # -- harness-level fields --
            "id": task_id,
            "task_type": task_type,
            "owner": owner,
            "status": "pending",
            "thread_id": "",
            "error": "",
            "failed_stage": "",
            "created_at": now,
            "updated_at": now,
            # -- convenience fields (kept for front-end compatibility) --
            "company_name": _params.get("company_name", ""),
            "focus": _params.get("focus", ""),
            "target_role": _params.get("target_role", ""),
            "max_analysts": int(_params.get("max_analysts", 3)),
            # -- domain-populated fields (initialised empty) --
            "analysts_preview": [],
            "analyst_version": 0,
            "docx_path": "",
            "pdf_path": "",
            "last_feedback": "",
            "risk_summary": {"high": 0, "medium": 0, "low": 0},
            "final_recommendation": "",
            "report_review_status": "",
            "report_review_summary": "",
            # -- carry full params for domain code --
            "params": _params,
        }

        with self._lock:
            tasks = self._read_tasks()
            tasks[task_id] = task
            self._write_tasks(tasks)

        self._emit_event(task_id, "task.created", {
            "task_type": task_type,
            "owner": owner,
            "params": _params,
        })
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
        """Mark stale running tasks as *failed* after a process restart."""
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
                task["error"] = "Task interrupted by service reload or restart. Checkpoints are preserved — retry will resume from the last successful step."
                task["updated_at"] = now
                tasks[task_id] = task
                updated_count += 1
                recovered_ids.append(task_id)
            if updated_count:
                self._write_tasks(tasks)

        if updated_count:
            self.logger.warning("Recovered interrupted tasks", count=updated_count)
            for task_id in recovered_ids:
                self._emit_event(task_id, "task.interrupted", {
                    "message": "Task record recovered after service reload or restart",
                })
        return updated_count

    # ------------------------------------------------------------------
    # Metrics snapshot
    # ------------------------------------------------------------------

    def snapshot_metrics(self, task_id: str) -> dict[str, Any] | None:
        """Capture current ledger + tracer metrics into the task record.

        Called when a task reaches terminal state so metrics survive restarts.
        """
        try:
            from harness.observability.metrics import get_ledger
            from harness.observability.tracer import get_tracer

            ledger = get_ledger(task_id)
            tracer = get_tracer(task_id)
            ledger_summary = ledger.summary()
            tracer_summary = tracer.summary()

            # Merge ledger (token/cost) + tracer (timing) into one snapshot
            tracer_by_node: dict[str, dict] = {}
            for node_name, stats in (tracer_summary.get("by_node") or {}).items():
                tracer_by_node[node_name] = {
                    "count": stats.get("count", 0),
                    "total_duration_ms": stats.get("total_duration_ms", 0),
                    "errors": stats.get("errors", 0),
                }

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
                    "errors": tn.get("errors", 0),
                }

            total_duration_ms = tracer_summary.get("total_duration_ms", 0)

            snapshot = {
                "call_count": ledger_summary.get("call_count", 0),
                "total_prompt_tokens": ledger_summary.get("total_prompt_tokens", 0),
                "total_completion_tokens": ledger_summary.get("total_completion_tokens", 0),
                "total_tokens": ledger_summary.get("total_tokens", 0),
                "total_latency_ms": total_duration_ms or ledger_summary.get("total_latency_ms", 0),
                "estimated_cost_usd": ledger_summary.get("estimated_cost_usd", 0.0),
                "over_budget": ledger_summary.get("over_budget", False),
                "by_node": merged_by_node,
                "by_model": ledger_summary.get("by_model", {}),
            }

            self.update_task(task_id, metrics_snapshot=snapshot)
            return snapshot
        except Exception:
            self.logger.warning("Failed to snapshot metrics", task_id=task_id, exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Background runner
    # ------------------------------------------------------------------

    def run_in_background(
        self,
        task_id: str,
        started_status: str,
        finished_status: str,
        work: Callable[[], dict[str, Any]],
    ) -> None:
        """Execute *work* on a daemon thread, updating task status bookends."""

        def _runner():
            self.update_task(task_id, status=started_status, error="")
            self._emit_event(task_id, "task.started", {"status": started_status})
            try:
                result = work() or {}
                resolved_status = str(result.pop("next_status", finished_status) or finished_status)
                result["error"] = ""
                result["failed_stage"] = ""
                self.update_task(task_id, status=resolved_status, **result)
                # Snapshot metrics for completed tasks so they survive restarts
                if resolved_status == "completed":
                    self.snapshot_metrics(task_id)
                self._emit_event(task_id, "task.completed", {"status": resolved_status})
            except Exception as exc:
                self.logger.error("Background task failed", task_id=task_id, error=str(exc))
                self.update_task(
                    task_id,
                    status="failed",
                    error=str(exc),
                    failed_stage=started_status,
                )
                self._emit_event(task_id, "task.failed", {
                    "error": str(exc),
                    "failed_stage": started_status,
                })

        threading.Thread(target=_runner, daemon=True).start()


# ------------------------------------------------------------------
# Module-level singleton (replaces the old app.api.services singleton)
# ------------------------------------------------------------------
TASK_RUNTIME = TaskRuntime()
