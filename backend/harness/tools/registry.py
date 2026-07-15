"""
Tool Registry — centralised registration and discovery of tools.

Every tool (search, browse, data, …) registers here so domain code
and pipelines can resolve tool instances by name.
"""
from threading import Lock
from typing import Any

from harness.tools.search.base import SearchTool


class ToolRegistry:
    """Thread-safe registry for tool instances."""

    def __init__(self):
        self._search: dict[str, SearchTool] = {}
        self._browse: dict[str, Any] = {}
        self._lock = Lock()

    # ------------------------------------------------------------------
    # Search tools
    # ------------------------------------------------------------------

    def register_search(self, tool: SearchTool) -> None:
        with self._lock:
            self._search[tool.name] = tool

    def get_search(self, name: str) -> SearchTool | None:
        with self._lock:
            return self._search.get(name)

    def list_search(self) -> list[str]:
        with self._lock:
            return sorted(self._search.keys())

    # ------------------------------------------------------------------
    # Browse tools
    # ------------------------------------------------------------------

    def register_browse(self, name: str, tool: Any) -> None:
        with self._lock:
            self._browse[name] = tool

    def get_browse(self, name: str) -> Any:
        with self._lock:
            return self._browse.get(name)

    def list_browse(self) -> list[str]:
        with self._lock:
            return sorted(self._browse.keys())


# Module-level singleton
TOOL_REGISTRY = ToolRegistry()
