"""
Domain Adapter base class.

Each domain application (due_diligence, stock_analysis, legal_review, …)
implements this interface so the harness runtime can orchestrate it without
knowing domain-specific details.
"""
from abc import ABC, abstractmethod
from typing import Any, Type

from typing_extensions import TypedDict


class DomainAdapter(ABC):
    """Base class for pluggable domain adapters.

    A domain adapter provides:
    - A state schema (TypedDict) that defines the shape of graph state
    - Node implementations for the standard graph template phases
    - Domain-specific configuration
    """

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

    def build_config(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return the initial state values for a new task.

        Domain-specific adapters should override this to set defaults.
        """
        return dict(params)
