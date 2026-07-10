"""
Domain Adapter base class.

Each domain application (due_diligence, stock_analysis, legal_review, …)
implements this interface so the harness runtime can orchestrate it without
knowing domain-specific details.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Type

from typing_extensions import TypedDict


class DomainAdapter(ABC):
    """Base class for pluggable domain adapters.

    A domain adapter provides:
    - A state schema (TypedDict) that defines the shape of graph state
    - Node implementations for the standard graph template phases
    - Domain-specific configuration
    - (optional) A NodeRegistry for the runtime graph builder

    Subclasses should:
    1. Implement ``domain_name``, ``state_schema``
    2. Override ``get_nodes()`` to expose their node callables
    3. Optionally override ``get_graph_mode()`` to pick a template
    """

    # ------------------------------------------------------------------
    # Required
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def domain_name(self) -> str:
        """Short kebab-case identifier, e.g. ``"due_diligence"``."""
        ...

    @property
    @abstractmethod
    def state_schema(self) -> Type[TypedDict]:
        """The TypedDict used as the graph state for this domain."""
        ...

    # ------------------------------------------------------------------
    # Optional — node registry (for runtime graph builder)
    # ------------------------------------------------------------------

    def get_nodes(self) -> "NodeRegistry":  # noqa: F821
        """Return a ``NodeRegistry`` mapping standard node names → callables.

        Override this in domain subclasses.  The runtime graph builder calls
        this to discover what nodes the domain provides.

        Standard names (plan_execute mode):
        - ``"plan"``, ``"revise"``, ``"dispatch"``, ``"execute"``
        - ``"write_report"``, ``"write_introduction"``, ``"write_conclusion"``
        - ``"review_report"``, ``"finalize_report"``
        - Any custom prepare-chain nodes
        """
        from harness.runtime.graph_builder import NodeRegistry
        return NodeRegistry()

    def get_graph_mode(self) -> str:
        """Return the preferred graph mode for this domain.

        Override to return ``"plan_execute"`` (default), ``"debate"``,
        or ``"research"``.
        """
        return "plan_execute"

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def build_config(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return the initial state values for a new task.

        Domain-specific adapters should override this to set defaults.
        """
        return dict(params)

    # ------------------------------------------------------------------
    # Convenience — build the graph through the harness runtime
    # ------------------------------------------------------------------

    def build_graph(
        self,
        checkpointer: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Build a compiled LangGraph graph using the runtime template.

        Uses ``AgentGraphTemplate`` internally.  Extra *kwargs* are forwarded
        to the template's mode-specific builder.

        This is a convenience — domain code can also call
        ``AgentGraphTemplate(mode=..., domain=self).build(...)`` directly.
        """
        from harness.runtime.graph_builder import AgentGraphTemplate

        template = AgentGraphTemplate(
            mode=self.get_graph_mode(),
            domain=self,
        )
        registry = self.get_nodes()
        return template.build(
            checkpointer=checkpointer,
            registry=registry,
            **kwargs,
        )
