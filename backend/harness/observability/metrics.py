"""
Metrics Collector — token counting, cost estimation, and budget tracking.

Tracks token consumption across LLM calls and estimates cost based on
per-model pricing.  Can be used both as a per-task accumulator and as a
global ledger for cross-task reporting.

Model pricing is loaded from a built-in table (USD per 1M tokens, input / output).
Pricing data is updated periodically — the table below reflects July 2026
approximate pricing.

Usage::

    from harness.observability.metrics import MetricsCollector, MODEL_PRICING

    ledger = MetricsCollector(task_id="abc123")

    # Record a call
    ledger.record(
        node="create_analyst",
        model="gpt-4o-mini",
        prompt_tokens=500,
        completion_tokens=200,
        latency_ms=1200,
    )

    # Check totals
    print(ledger.total_tokens)      # 700
    print(ledger.estimated_cost)    # e.g. 0.00028

    # Per-node breakdown
    for node, stats in ledger.by_node().items():
        print(node, stats["total_tokens"], stats["estimated_cost"])
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Model pricing table (USD per 1M tokens)
# ---------------------------------------------------------------------------

@dataclass
class ModelPrice:
    """Pricing for one model in USD per 1M tokens."""

    input_per_mtok: float
    output_per_mtok: float

    def cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        """Cost in USD for a given token count."""
        return (
            prompt_tokens / 1_000_000 * self.input_per_mtok
            + completion_tokens / 1_000_000 * self.output_per_mtok
        )


# Approximate pricing as of July 2026.  Update these as providers change rates.
MODEL_PRICING: dict[str, ModelPrice] = {
    # OpenAI
    "gpt-4o": ModelPrice(input_per_mtok=2.50, output_per_mtok=10.00),
    "gpt-4o-mini": ModelPrice(input_per_mtok=0.15, output_per_mtok=0.60),
    "gpt-4.1": ModelPrice(input_per_mtok=2.00, output_per_mtok=8.00),
    "gpt-4.1-mini": ModelPrice(input_per_mtok=0.40, output_per_mtok=1.60),
    "gpt-4.1-nano": ModelPrice(input_per_mtok=0.10, output_per_mtok=0.40),
    "gpt-4-turbo": ModelPrice(input_per_mtok=10.00, output_per_mtok=30.00),
    "o3": ModelPrice(input_per_mtok=10.00, output_per_mtok=40.00),
    "o4-mini": ModelPrice(input_per_mtok=1.10, output_per_mtok=4.40),
    # Anthropic
    "claude-sonnet-5": ModelPrice(input_per_mtok=3.00, output_per_mtok=15.00),
    "claude-opus-4-8": ModelPrice(input_per_mtok=15.00, output_per_mtok=75.00),
    "claude-haiku-4-5-20251001": ModelPrice(input_per_mtok=0.80, output_per_mtok=4.00),
    "claude-fable-5": ModelPrice(input_per_mtok=3.00, output_per_mtok=15.00),
    # Google
    "gemini-2.0-flash": ModelPrice(input_per_mtok=0.10, output_per_mtok=0.40),
    "gemini-2.5-pro": ModelPrice(input_per_mtok=1.25, output_per_mtok=10.00),
    "gemini-2.5-flash": ModelPrice(input_per_mtok=0.15, output_per_mtok=0.60),
    # DeepSeek
    "deepseek-chat": ModelPrice(input_per_mtok=0.27, output_per_mtok=1.10),
    "deepseek-reasoner": ModelPrice(input_per_mtok=0.55, output_per_mtok=2.19),
    "deepseek-v3": ModelPrice(input_per_mtok=0.27, output_per_mtok=1.10),
    "deepseek-v4-pro": ModelPrice(input_per_mtok=0.27, output_per_mtok=1.10),
    # Groq (often free tier or low-cost)
    "llama-3.3-70b": ModelPrice(input_per_mtok=0.59, output_per_mtok=0.79),
    "mixtral-8x7b": ModelPrice(input_per_mtok=0.24, output_per_mtok=0.24),
}


def get_price(model_name: str) -> ModelPrice:
    """Look up pricing for *model_name*.

    Falls back to a cheap default if the model is not in the table.
    """
    # Try exact match
    if model_name in MODEL_PRICING:
        return MODEL_PRICING[model_name]
    # Try prefix match (e.g. "gpt-4o-2024-08-06" → "gpt-4o")
    for key, price in MODEL_PRICING.items():
        if model_name.startswith(key):
            return price
    # Fallback: assume ~cheap pricing
    return ModelPrice(input_per_mtok=0.50, output_per_mtok=2.00)


# ---------------------------------------------------------------------------
# Per-call record
# ---------------------------------------------------------------------------

@dataclass
class LLMCallRecord:
    """One LLM call recorded by the ledger."""

    node: str = ""
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    note: str = ""

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def cost(self) -> float:
        return get_price(self.model).cost(self.prompt_tokens, self.completion_tokens)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node": self.node,
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "latency_ms": self.latency_ms,
            "cost": round(self.cost, 6),
            "note": self.note,
        }


# ---------------------------------------------------------------------------
# Metrics collector
# ---------------------------------------------------------------------------

class MetricsCollector:
    """Per-task token and cost ledger.

    Accumulates ``LLMCallRecord`` entries and provides aggregation helpers.
    Use one instance per task.
    """

    def __init__(self, task_id: str = ""):
        self.task_id = task_id
        self._calls: list[LLMCallRecord] = []
        self._budget_cap: float | None = None  # USD
        self._budget_warning_pct: float = 0.80  # warn at 80% of cap

    # -- record -------------------------------------------------------------

    def record(
        self,
        node: str = "",
        model: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        latency_ms: int = 0,
        note: str = "",
    ) -> LLMCallRecord:
        """Record one LLM call and return the record."""
        rec = LLMCallRecord(
            node=node,
            model=model,
            prompt_tokens=int(prompt_tokens or 0),
            completion_tokens=int(completion_tokens or 0),
            latency_ms=int(latency_ms or 0),
            note=note,
        )
        self._calls.append(rec)
        return rec

    # -- budget -------------------------------------------------------------

    @property
    def budget_cap(self) -> float | None:
        return self._budget_cap

    @property
    def budget_remaining(self) -> float | None:
        if self._budget_cap is None:
            return None
        return max(0.0, self._budget_cap - self.estimated_cost)

    @property
    def over_budget(self) -> bool:
        if self._budget_cap is None:
            return False
        return self.estimated_cost > self._budget_cap

    @property
    def nearing_budget(self) -> bool:
        if self._budget_cap is None or self._budget_cap == 0:
            return False
        return self.estimated_cost >= self._budget_cap * self._budget_warning_pct

    # -- aggregation --------------------------------------------------------

    @property
    def calls(self) -> list[LLMCallRecord]:
        return list(self._calls)

    @property
    def call_count(self) -> int:
        return len(self._calls)

    @property
    def total_prompt_tokens(self) -> int:
        return sum(c.prompt_tokens for c in self._calls)

    @property
    def total_completion_tokens(self) -> int:
        return sum(c.completion_tokens for c in self._calls)

    @property
    def total_tokens(self) -> int:
        return self.total_prompt_tokens + self.total_completion_tokens

    @property
    def total_latency_ms(self) -> int:
        return sum(c.latency_ms for c in self._calls)

    @property
    def estimated_cost(self) -> float:
        return sum(c.cost for c in self._calls)

    def by_node(self) -> dict[str, dict[str, Any]]:
        """Per-node aggregation.

        Returns a dict keyed by node name, each value is a stats dict.
        """
        nodes: dict[str, dict[str, Any]] = {}
        for c in self._calls:
            stats = nodes.setdefault(
                c.node or "(unknown)",
                {
                    "calls": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "total_latency_ms": 0,
                    "estimated_cost": 0.0,
                },
            )
            stats["calls"] += 1
            stats["prompt_tokens"] += c.prompt_tokens
            stats["completion_tokens"] += c.completion_tokens
            stats["total_tokens"] += c.total_tokens
            stats["total_latency_ms"] += c.latency_ms
            stats["estimated_cost"] += c.cost
        # Round cost for readability
        for stats in nodes.values():
            stats["estimated_cost"] = round(stats["estimated_cost"], 6)
        return nodes

    def by_model(self) -> dict[str, dict[str, Any]]:
        """Per-model aggregation."""
        models: dict[str, dict[str, Any]] = {}
        for c in self._calls:
            model = c.model or "(unknown)"
            stats = models.setdefault(
                model,
                {
                    "calls": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "estimated_cost": 0.0,
                },
            )
            stats["calls"] += 1
            stats["prompt_tokens"] += c.prompt_tokens
            stats["completion_tokens"] += c.completion_tokens
            stats["total_tokens"] += c.total_tokens
            stats["estimated_cost"] += c.cost
        for stats in models.values():
            stats["estimated_cost"] = round(stats["estimated_cost"], 6)
        return models

    def summary(self) -> dict[str, Any]:
        """One-shot aggregate summary suitable for a task-status response."""
        return {
            "call_count": self.call_count,
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_tokens": self.total_tokens,
            "total_latency_ms": self.total_latency_ms,
            "estimated_cost_usd": round(self.estimated_cost, 6),
            "budget_cap_usd": self._budget_cap,
            "budget_remaining_usd": (
                round(self.budget_remaining, 6)
                if self.budget_remaining is not None
                else None
            ),
            "over_budget": self.over_budget,
            "by_node": self.by_node(),
            "by_model": self.by_model(),
        }

    # -- serialisation ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "calls": [c.to_dict() for c in self._calls],
            "budget_cap": self._budget_cap,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MetricsCollector":
        m = cls(task_id=str(d.get("task_id", "")))
        m._budget_cap = d.get("budget_cap")
        for c in d.get("calls", []) or []:
            m._calls.append(
                LLMCallRecord(
                    node=str(c.get("node", "")),
                    model=str(c.get("model", "")),
                    prompt_tokens=int(c.get("prompt_tokens", 0) or 0),
                    completion_tokens=int(c.get("completion_tokens", 0) or 0),
                    latency_ms=int(c.get("latency_ms", 0) or 0),
                    note=str(c.get("note", "")),
                )
            )
        return m


# ---------------------------------------------------------------------------
# Global ledger (one per active task, keyed by task_id)
# ---------------------------------------------------------------------------

_ledgers: dict[str, MetricsCollector] = {}


def get_ledger(task_id: str) -> MetricsCollector:
    """Get or create a ``MetricsCollector`` for *task_id*."""
    if task_id not in _ledgers:
        _ledgers[task_id] = MetricsCollector(task_id)
    return _ledgers[task_id]


def remove_ledger(task_id: str) -> None:
    """Remove a ledger from the registry."""
    _ledgers.pop(task_id, None)
