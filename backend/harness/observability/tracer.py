"""
Node Tracer — per-node execution traces for LangGraph workflows.

Each trace entry records what happened inside a single graph-node invocation:
timing, token consumption, LLM calls, and any error.  Traces are written as
append-only JSONL to ``.runtime/traces/{task_id}.jsonl`` so they can be tailed
during a run or analysed afterward.

Usage::

    from harness.observability.tracer import NodeTracer

    tracer = NodeTracer(task_id="abc123")

    # As a context manager:
    with tracer.trace("create_analyst") as span:
        result = do_work()
        span.set_output(len(result))
        span.record_llm_call(prompt_tokens=500, completion_tokens=200)

    # As a decorator:
    @tracer.wrap("write_report")
    def write_report(state):
        ...
"""
from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from app.config import RUNTIME_DIR
from app.logger import GLOBAL_LOGGER


# ---------------------------------------------------------------------------
# Trace entry data model
# ---------------------------------------------------------------------------

@dataclass
class TraceEntry:
    """One invocation of a graph node."""

    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    task_id: str = ""
    node_name: str = ""
    span_id: str = ""

    started_at: float = 0.0
    finished_at: float = 0.0

    input_summary: str = ""
    output_summary: str = ""

    duration_ms: int = 0

    llm_calls: list[dict[str, Any]] = field(default_factory=list)
    """Each call: {model, prompt_tokens, completion_tokens, total_tokens, latency_ms}."""

    error: str = ""
    error_type: str = ""

    metadata: dict[str, Any] = field(default_factory=dict)
    """Arbitrary extra tags (e.g. analyst_count, section_count)."""

    @property
    def total_prompt_tokens(self) -> int:
        return sum(
            (c.get("prompt_tokens", 0) or 0) for c in self.llm_calls
        )

    @property
    def total_completion_tokens(self) -> int:
        return sum(
            (c.get("completion_tokens", 0) or 0) for c in self.llm_calls
        )

    @property
    def total_tokens(self) -> int:
        return sum(
            c.get("total_tokens") or (c.get("prompt_tokens", 0) or 0) + (c.get("completion_tokens", 0) or 0)
            for c in self.llm_calls
        )

    @property
    def total_llm_latency_ms(self) -> int:
        return sum(c.get("latency_ms", 0) or 0 for c in self.llm_calls)

    @property
    def ok(self) -> bool:
        return not bool(self.error)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "task_id": self.task_id,
            "node_name": self.node_name,
            "span_id": self.span_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "input_summary": self.input_summary,
            "output_summary": self.output_summary,
            "duration_ms": self.duration_ms,
            "llm_calls": self.llm_calls,
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_tokens": self.total_tokens,
            "total_llm_latency_ms": self.total_llm_latency_ms,
            "error": self.error,
            "error_type": self.error_type,
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# Trace span (context manager)
# ---------------------------------------------------------------------------

class TraceSpan:
    """A live trace span returned by ``NodeTracer.trace()``.

    Call ``set_output``, ``record_llm_call``, and ``set_error`` on the span
    while it's active.  The trace is flushed to disk when the context exits.
    """

    def __init__(self, entry: TraceEntry, tracer: "NodeTracer"):
        self._entry = entry
        self._tracer = tracer
        self._started = time.perf_counter()
        entry.started_at = time.time()

    def set_input(self, summary: str) -> None:
        self._entry.input_summary = summary[:500]

    def set_output(self, summary: str) -> None:
        self._entry.output_summary = str(summary)[:500]

    def record_llm_call(
        self,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        latency_ms: int = 0,
        model: str = "",
    ) -> None:
        """Record one LLM invocation within this node."""
        _prompt = int(prompt_tokens or 0)
        _completion = int(completion_tokens or 0)
        _total = int(total_tokens or 0) or (_prompt + _completion)
        self._entry.llm_calls.append({
            "model": model,
            "prompt_tokens": _prompt,
            "completion_tokens": _completion,
            "total_tokens": _total,
            "latency_ms": int(latency_ms or 0),
        })

    def set_error(self, error: str, error_type: str = "") -> None:
        self._entry.error = error
        self._entry.error_type = error_type

    def tag(self, **kwargs: Any) -> None:
        """Add arbitrary metadata tags."""
        self._entry.metadata.update(kwargs)

    def _close(self) -> TraceEntry:
        elapsed = time.perf_counter() - self._started
        self._entry.duration_ms = int(elapsed * 1000)
        self._entry.finished_at = time.time()
        return self._entry


# ---------------------------------------------------------------------------
# NodeTracer
# ---------------------------------------------------------------------------

class NodeTracer:
    """Per-task node execution tracer.

    Writes one JSON line per node invocation to
    ``<RUNTIME_DIR>/traces/<task_id>.jsonl``.
    """

    def __init__(self, task_id: str):
        self.task_id = task_id
        self._traces_dir = os.path.join(os.fspath(RUNTIME_DIR), "traces")
        os.makedirs(self._traces_dir, exist_ok=True)
        self._path = os.path.join(self._traces_dir, f"{task_id}.jsonl")
        self._logger = GLOBAL_LOGGER.bind(module="NodeTracer", task_id=task_id)

    # -- file path ----------------------------------------------------------

    @property
    def path(self) -> str:
        return self._path

    # -- context manager ----------------------------------------------------

    @contextmanager
    def trace(self, node_name: str) -> Iterator[TraceSpan]:
        """Create a trace span for one node invocation.

        Usage::

            with tracer.trace("create_analyst") as span:
                result = do_work()
                span.set_output(f"Created {len(result)} analysts")
                span.record_llm_call(prompt_tokens=500, completion_tokens=200)
        """
        entry = TraceEntry(
            task_id=self.task_id,
            node_name=node_name,
            span_id=str(uuid.uuid4())[:8],
        )
        span = TraceSpan(entry, self)
        try:
            yield span
        except Exception as exc:
            span.set_error(str(exc), type(exc).__name__)
            raise
        finally:
            span._close()
            self._write(entry)

    # -- decorator ----------------------------------------------------------

    def wrap(self, node_name: str) -> Callable:
        """Decorator that wraps a node function with a trace span.

        Usage::

            @tracer.wrap("create_analyst")
            def create_analyst(state):
                ...
        """

        def decorator(fn: Callable) -> Callable:
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                with self.trace(node_name) as span:
                    # Capture input summary from first positional arg
                    if args:
                        first = args[0]
                        if isinstance(first, dict):
                            keys = list(first.keys())[:10]
                            span.set_input(f"state keys: {keys}")
                        elif isinstance(first, str):
                            span.set_input(first[:200])
                    try:
                        result = fn(*args, **kwargs)
                    except Exception as exc:
                        span.set_error(str(exc), type(exc).__name__)
                        raise

                    # Capture output summary
                    if isinstance(result, dict):
                        span.set_output(
                            f"keys: {list(result.keys())[:10]}, "
                            f"len: {sum(len(str(v)) for v in result.values())}"
                        )
                    elif isinstance(result, (str, int, float)):
                        span.set_output(str(result)[:500])
                    else:
                        span.set_output(str(type(result).__name__))

                    # Auto-extract llm_metrics from result dict if present
                    if isinstance(result, dict) and "llm_metrics" in result:
                        for m in result["llm_metrics"]:
                            if isinstance(m, dict):
                                span.record_llm_call(
                                    prompt_tokens=int(m.get("prompt_tokens", 0) or 0),
                                    completion_tokens=int(m.get("completion_tokens", 0) or 0),
                                    total_tokens=int(m.get("total_tokens", 0) or 0),
                                    latency_ms=int(m.get("latency_ms", 0) or 0),
                                    model=str(m.get("model", "")),
                                )

                    return result

            wrapper.__name__ = fn.__name__
            wrapper.__doc__ = fn.__doc__
            return wrapper

        return decorator

    # -- persistence --------------------------------------------------------

    def _write(self, entry: TraceEntry) -> None:
        """Append one trace entry to the JSONL file."""
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry.to_dict(), ensure_ascii=True) + "\n")
        except Exception:
            self._logger.warning(
                "Failed to write trace entry",
                node=entry.node_name,
                span=entry.span_id,
                exc_info=True,
            )

    # -- query helpers ------------------------------------------------------

    def read_all(self) -> list[TraceEntry]:
        """Read all trace entries for this task from disk."""
        entries: list[TraceEntry] = []
        if not os.path.exists(self._path):
            return entries
        with open(self._path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                entries.append(self._dict_to_entry(d))
        return entries

    def summary(self) -> dict[str, Any]:
        """Aggregate summary of all traces for this task."""
        entries = self.read_all()
        if not entries:
            return {"total_nodes": 0}

        total_duration = sum(e.duration_ms for e in entries)
        total_prompt = sum(e.total_prompt_tokens for e in entries)
        total_completion = sum(e.total_completion_tokens for e in entries)
        errors = [e for e in entries if not e.ok]

        node_stats: dict[str, dict[str, Any]] = {}
        for e in entries:
            stats = node_stats.setdefault(e.node_name, {
                "count": 0,
                "total_duration_ms": 0,
                "total_prompt_tokens": 0,
                "total_completion_tokens": 0,
                "errors": 0,
            })
            stats["count"] += 1
            stats["total_duration_ms"] += e.duration_ms
            stats["total_prompt_tokens"] += e.total_prompt_tokens
            stats["total_completion_tokens"] += e.total_completion_tokens
            if not e.ok:
                stats["errors"] += 1

        return {
            "total_nodes": len(entries),
            "total_duration_ms": total_duration,
            "total_prompt_tokens": total_prompt,
            "total_completion_tokens": total_completion,
            "total_tokens": total_prompt + total_completion,
            "error_count": len(errors),
            "by_node": node_stats,
        }

    @staticmethod
    def _dict_to_entry(d: dict[str, Any]) -> TraceEntry:
        e = TraceEntry(
            trace_id=str(d.get("trace_id", "")),
            task_id=str(d.get("task_id", "")),
            node_name=str(d.get("node_name", "")),
            span_id=str(d.get("span_id", "")),
            started_at=float(d.get("started_at", 0) or 0),
            finished_at=float(d.get("finished_at", 0) or 0),
            input_summary=str(d.get("input_summary", "")),
            output_summary=str(d.get("output_summary", "")),
            duration_ms=int(d.get("duration_ms", 0) or 0),
            llm_calls=list(d.get("llm_calls", []) or []),
            error=str(d.get("error", "")),
            error_type=str(d.get("error_type", "")),
            metadata=dict(d.get("metadata", {}) or {}),
        )
        return e


# ---------------------------------------------------------------------------
# Global trace registry (one tracer per active task)
# ---------------------------------------------------------------------------

_tracers: dict[str, NodeTracer] = {}


def get_tracer(task_id: str) -> NodeTracer:
    """Get or create a ``NodeTracer`` for *task_id*.

    This is a convenience singleton registry so domain code can access
    the tracer without threading it through every constructor.
    """
    if task_id not in _tracers:
        _tracers[task_id] = NodeTracer(task_id)
    return _tracers[task_id]


def remove_tracer(task_id: str) -> None:
    """Remove a tracer from the registry (e.g. on task completion)."""
    _tracers.pop(task_id, None)
