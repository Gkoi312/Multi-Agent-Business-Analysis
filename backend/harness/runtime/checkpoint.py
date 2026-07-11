"""
Checkpoint Manager — wraps LangGraph checkpointers and thread management.

Provides a consistent API for:
- Creating / retrieving checkpointer instances (MemorySaver or SqliteSaver)
- Generating thread configs
- Reading / updating graph state at a checkpoint
- Serialising thread history for task-persistence scenarios
"""
from __future__ import annotations

import uuid
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph

from app.logger import GLOBAL_LOGGER


# ---------------------------------------------------------------------------
# Checkpoint manager
# ---------------------------------------------------------------------------

class CheckpointManager:
    """Manages LangGraph checkpointing and thread lifecycle.

    Usage::

        cpm = CheckpointManager(backend="memory")
        thread = cpm.new_thread()

        graph = builder.compile(checkpointer=cpm.checkpointer)
        for event in graph.stream(input, thread):
            ...

        state = cpm.get_state(graph, thread)
        cpm.update_state(graph, thread, {"key": "value"}, as_node="my_node")
    """

    def __init__(self, backend: str = "memory", db_path: str = ""):
        """
        Parameters
        ----------
        backend:
            ``"memory"`` — in-process MemorySaver (default, for dev / testing).
            ``"sqlite"`` — SqliteSaver at *db_path* (for persistence across restarts).
        db_path:
            Path to the SQLite database file (only used when backend="sqlite").
        """
        self.backend = backend
        self.db_path = db_path
        self._logger = GLOBAL_LOGGER.bind(module="CheckpointManager")

        if backend == "sqlite":
            try:
                from langgraph.checkpoint.sqlite import SqliteSaver
                path = db_path or ":memory:"
                self._checkpointer = SqliteSaver.from_conn_string(path)
                self._logger.info("Initialised SqliteSaver", path=path)
            except ImportError:
                self._logger.warning(
                    "SqliteSaver not available — falling back to MemorySaver. "
                    "Install langgraph[checkpoint] or upgrade langgraph."
                )
                self._checkpointer = MemorySaver()
                self.backend = "memory"
        else:
            self._checkpointer = MemorySaver()
            self._logger.info("Initialised MemorySaver")

    # -- checkpointer access ------------------------------------------------

    @property
    def checkpointer(self):
        """The underlying LangGraph checkpointer instance."""
        return self._checkpointer

    # -- thread management --------------------------------------------------

    @staticmethod
    def new_thread(thread_id: str | None = None) -> dict[str, Any]:
        """Create a ``configurable`` dict for a new (or resumed) thread.

        Usage::

            thread = CheckpointManager.new_thread()
            thread = CheckpointManager.new_thread("my-custom-id")
        """
        return {"configurable": {"thread_id": thread_id or str(uuid.uuid4())}}

    @staticmethod
    def thread_id(thread: dict[str, Any]) -> str:
        """Extract the thread_id from a config dict."""
        return str(thread.get("configurable", {}).get("thread_id", ""))

    # -- state helpers ------------------------------------------------------

    @staticmethod
    def get_state(graph, thread: dict[str, Any]) -> Any:
        """Return the current StateSnapshot for *thread*.

        Returns None if the thread has no recorded state yet.
        """
        try:
            return graph.get_state(thread)
        except Exception:
            return None

    @staticmethod
    def get_state_values(graph, thread: dict[str, Any]) -> dict[str, Any]:
        """Return just the ``values`` dict from the current checkpoint."""
        snap = CheckpointManager.get_state(graph, thread)
        if snap is None:
            return {}
        return dict(snap.values or {})

    @staticmethod
    def update_state(
        graph,
        thread: dict[str, Any],
        values: dict[str, Any],
        as_node: str = "",
    ) -> None:
        """Inject *values* into the graph state at the current checkpoint.

        Use this to feed human feedback or external data into a paused graph.
        """
        kwargs: dict[str, Any] = {"config": thread, "values": values}
        if as_node:
            kwargs["as_node"] = as_node
        graph.update_state(**kwargs)

    # -- serialisation ------------------------------------------------------

    def export_thread(
        self, graph, thread: dict[str, Any]
    ) -> dict[str, Any]:
        """Export a thread's full history as a serialisable dict.

        Useful for migrating state between backends or persisting the full
        conversation alongside the task record.
        """
        state = self.get_state(graph, thread)
        if state is None:
            return {"thread_id": self.thread_id(thread), "values": {}, "history": []}

        history: list[dict[str, Any]] = []
        try:
            for checkpoint in graph.get_state_history(thread):
                history.append({
                    "step": getattr(checkpoint, "metadata", {}).get("step", 0),
                    "values": dict(getattr(checkpoint, "values", {}) or {}),
                })
        except Exception:
            pass

        return {
            "thread_id": self.thread_id(thread),
            "values": dict(state.values or {}),
            "next_nodes": list(getattr(state, "next", []) or []),
            "history": history,
        }
