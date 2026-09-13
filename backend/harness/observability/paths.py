"""
Default filesystem locations for harness-level persistence (tasks, traces, ...).

harness must not import app.* (see README's noted boundary rules). Callers
that want a custom location — e.g. the FastAPI app choosing a different
runtime directory — should pass it explicitly to ``TaskRuntime``/``NodeTracer``
rather than harness reaching into app config. The ``RUNTIME_DIR`` env var is
still respected as the default, since reading an env var directly is just
external configuration, not a dependency on another package.
"""
import os
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent  # backend/


def default_runtime_dir() -> str:
    return os.getenv("RUNTIME_DIR", str(_BACKEND_ROOT / ".runtime"))
