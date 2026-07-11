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

    def get_best_search(self, preferred_source_types: list[str] | None = None) -> SearchTool | None:
        """Return the best available search tool for the given source type hints.

        Currently returns the first registered tool.  When multiple backends
        are registered, this method can route by source_type (e.g. news → Brave,
        academic → Tavily).
        """
        if not self._search:
            return None
        # Simple policy: prefer a tool whose name matches a source_type hint
        if preferred_source_types:
            for hint in preferred_source_types:
                for name, tool in self._search.items():
                    if hint.lower() in name.lower():
                        return tool
        # Fallback: return the first registered tool
        return next(iter(self._search.values()))

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
