"""
Agent Graph Template — domain-agnostic graph builders for standard Agent patterns.

Provides three canonical graph modes:

- **plan_execute**:  prepare → plan → human review → fan-out execute → assemble → review → finalize
- **debate**:         present positions → cross-examine → judge ruling
- **research**:       investigate → evaluate sufficiency → (loop | produce output)

Each mode is a ``build_*`` method that takes a ``DomainAdapter`` and returns a
compiled LangGraph ``StateGraph``.  The *template* owns the **graph topology**
(edges, routing, fan-out); the *domain* owns the **node implementations**.

Usage (domain code)::

    from harness.runtime.graph_builder import AgentGraphTemplate
    from harness.runtime.checkpoint import CheckpointManager

    template = AgentGraphTemplate(mode="plan_execute", domain=my_adapter)
    graph = template.build(checkpointer=CheckpointManager().checkpointer)

    # Or, for full control:
    graph = template.build_plan_execute(
        checkpointer=...,
        plan_node="create_analyst",
        revise_node="regenerate_analyst",
        dispatch_node="start_interviews",
        execute_subgraph=interview_graph,
        assemble_nodes=["write_report", "write_intro", "write_conclusion"],
        review_node="review_report",
        finalize_node="finalize_report",
        human_feedback_key="human_analyst_feedback",
        interrupt_before=["human_feedback"],
    )
"""
from __future__ import annotations

import enum
from typing import Any, Callable

from langgraph.graph import StateGraph, START, END
from langgraph.types import Send

from harness.human_loop.gate import HumanReviewGate
from harness.runtime.fanout import FanOutNode
from app.logger import GLOBAL_LOGGER


# ---------------------------------------------------------------------------
# Graph mode enum
# ---------------------------------------------------------------------------

class GraphMode(str, enum.Enum):
    PLAN_EXECUTE = "plan_execute"
    DEBATE = "debate"
    RESEARCH = "research"


# ---------------------------------------------------------------------------
# Node registry — domain → standardised node names
# ---------------------------------------------------------------------------

class NodeRegistry:
    """Maps standard graph-template node names to domain callables.

    The template defines the topology using standard names (``"plan"``,
    ``"execute"``, …).  The domain registers its implementations under
    those names.

    Usage::

        registry = NodeRegistry()
        registry.set("plan", domain.create_analyst)
        registry.set("execute", interview_subgraph)
        # ...
    """

    def __init__(self):
        self._nodes: dict[str, Callable] = {}
        self._subgraphs: dict[str, Any] = {}  # compiled sub-graphs

    def set(self, name: str, node: Callable | Any) -> None:
        """Register a node callable or compiled sub-graph under *name*."""
        self._nodes[name] = node

    def get(self, name: str) -> Callable | Any | None:
        return self._nodes.get(name)

    def __getitem__(self, name: str) -> Callable | Any:
        if name not in self._nodes:
            raise KeyError(f"Node {name!r} not registered. Available: {list(self._nodes)}")
        return self._nodes[name]

    def __contains__(self, name: str) -> bool:
        return name in self._nodes

    @property
    def names(self) -> list[str]:
        return list(self._nodes.keys())


# ---------------------------------------------------------------------------
# AgentGraphTemplate
# ---------------------------------------------------------------------------

class AgentGraphTemplate:
    """Build compiled LangGraph graphs from standard patterns and domain nodes.

    Each template method accepts a ``NodeRegistry`` (or individual callables)
    plus configuration, wires them into the correct topology, and returns a
    compiled ``StateGraph``.

    Parameters
    ----------
    mode:
        The graph pattern to use (``"plan_execute"`` | ``"debate"`` | ``"research"``).
    domain:
        A ``DomainAdapter`` instance providing ``state_schema``, ``domain_name``,
        and optionally ``get_nodes()`` → ``NodeRegistry``.
    """

    def __init__(self, mode: str, domain: Any = None):
        self.mode = GraphMode(mode)
        self.domain = domain
        self._logger = GLOBAL_LOGGER.bind(
            module="AgentGraphTemplate",
            mode=self.mode.value,
            domain=getattr(domain, "domain_name", "unknown") if domain else "none",
        )

    # ------------------------------------------------------------------
    # Public build entry point
    # ------------------------------------------------------------------

    def build(
        self,
        checkpointer: Any = None,
        interrupt_before: list[str] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Build and compile the graph.

        Dispatches to the appropriate mode-specific builder.  Extra *kwargs*
        are forwarded to the builder method.

        Parameters
        ----------
        checkpointer:
            A LangGraph checkpointer instance (MemorySaver, SqliteSaver, …).
        interrupt_before:
            Node names to interrupt before (e.g. review gates).
        """
        self._logger.info("Building graph", mode=self.mode.value)

        if self.mode == GraphMode.PLAN_EXECUTE:
            return self.build_plan_execute(
                checkpointer=checkpointer,
                interrupt_before=interrupt_before,
                **kwargs,
            )
        elif self.mode == GraphMode.DEBATE:
            return self.build_debate(
                checkpointer=checkpointer,
                **kwargs,
            )
        elif self.mode == GraphMode.RESEARCH:
            return self.build_research(
                checkpointer=checkpointer,
                interrupt_before=interrupt_before,
                **kwargs,
            )
        else:
            raise ValueError(f"Unknown graph mode: {self.mode}")

    # ------------------------------------------------------------------
    # Plan-Execute mode
    # ------------------------------------------------------------------

    def build_plan_execute(
        self,
        *,
        # -- state schema --
        state_schema: type | None = None,
        # -- checkpointer --
        checkpointer: Any = None,
        interrupt_before: list[str] | None = None,
        # -- prepare phase (optional) --
        prepare_nodes: list[str] | None = None,  # sequential nodes before planning
        # -- plan phase --
        plan_node: str = "plan",
        plan_subgraph: Any = None,  # compiled sub-graph for plan (optional)
        # -- human review gate --
        human_feedback_key: str = "human_analyst_feedback",
        review_gate_node: str = "human_feedback",
        review_target: str = "plan",
        revise_node: str = "revise",
        revise_subgraph: Any = None,
        # -- dispatch / fan-out --
        dispatch_node: str = "dispatch",
        fan_out_items_key: str = "plan_items",
        fan_out_target: str = "execute",
        fan_out_payload_mapping: dict[str, str] | None = None,
        # -- execute subgraph --
        execute_subgraph: Any = None,  # compiled sub-graph for each parallel item
        # -- assemble phase --
        assemble_nodes: list[str] | None = None,  # parallel nodes that produce sections
        # -- final review --
        final_review_node: str = "",
        # -- finalize --
        finalize_node: str = "finalize",
        finalize_subgraph: Any = None,
        # -- node registry (alternative to individual params) --
        registry: NodeRegistry | None = None,
    ) -> Any:
        """Build a **plan → review → fan-out execute → assemble → finalize** graph.

        This is the most common Agent topology.  It is used by due diligence,
        stock analysis, legal review, and any workflow that:

        1. Prepares (classify, load skills)
        2. Plans (create analysts / items)
        3. Pauses for human review
        4. Fans out to N parallel executions
        5. Assembles results
        6. Optionally reviews quality
        7. Finalizes output

        Returns a compiled ``StateGraph``.
        """
        reg = registry or NodeRegistry()
        schema = state_schema or (
            self.domain.state_schema if self.domain else dict
        )

        # Resolve nodes from registry or kwargs
        _prepare = prepare_nodes or []
        _assemble = assemble_nodes or []

        builder = StateGraph(schema)

        # -- add all nodes to builder ---------------------------------------

        # Prepare chain (sequential)
        for name in _prepare:
            node_fn = reg.get(name)
            if node_fn is not None:
                builder.add_node(name, node_fn)

        # Plan node
        if plan_subgraph is not None:
            builder.add_node(plan_node, plan_subgraph)
        else:
            _plan_fn = reg.get(plan_node)
            if _plan_fn is not None:
                builder.add_node(plan_node, _plan_fn)

        # Human review gate (inserted automatically)
        gate = HumanReviewGate(
            review_target=review_target,
            version_key="review_version",
            hint="Submit feedback to revise, or leave empty to approve and continue.",
        )
        builder.add_node(review_gate_node, gate)

        # Revise node (regenerate after feedback)
        if revise_subgraph is not None:
            builder.add_node(revise_node, revise_subgraph)
        else:
            _revise_fn = reg.get(revise_node)
            if _revise_fn is not None:
                builder.add_node(revise_node, _revise_fn)

        # Dispatch node (fan-out)
        _dispatch_fn = reg.get(dispatch_node)
        if _dispatch_fn is not None:
            builder.add_node(dispatch_node, _dispatch_fn)

        # Execute sub-graph (runs in parallel for each item)
        has_execute = False
        if execute_subgraph is not None:
            builder.add_node(fan_out_target, execute_subgraph)
            has_execute = True
        else:
            _exec_fn = reg.get(fan_out_target)
            if _exec_fn is not None:
                builder.add_node(fan_out_target, _exec_fn)
                has_execute = True

        # Assemble nodes (parallel, then converge)
        _registered_assemble: list[str] = []
        for name in _assemble:
            node_fn = reg.get(name)
            if node_fn is not None:
                builder.add_node(name, node_fn)
                _registered_assemble.append(name)

        # Final review (optional)
        has_final_review = False
        if final_review_node:
            _review_fn = reg.get(final_review_node)
            if _review_fn is not None:
                builder.add_node(final_review_node, _review_fn)
                has_final_review = True

        # Finalize
        has_finalize = False
        if finalize_subgraph is not None:
            builder.add_node(finalize_node, finalize_subgraph)
            has_finalize = True
        else:
            _finalize_fn = reg.get(finalize_node)
            if _finalize_fn is not None:
                builder.add_node(finalize_node, _finalize_fn)
                has_finalize = True

        # -- wire edges ----------------------------------------------------

        # Prepare chain
        prev = START
        for name in _prepare:
            builder.add_edge(prev, name)
            prev = name

        # Prepare → Plan
        builder.add_edge(prev if _prepare else START, plan_node)

        # Plan → Review gate
        builder.add_edge(plan_node, review_gate_node)

        # Review gate → revise (feedback) or dispatch (approved)
        has_revise = reg.get(revise_node) is not None
        review_router = HumanReviewGate.build_router(
            feedback_key=human_feedback_key,
            approved_next=dispatch_node,
            revise_next=revise_node if has_revise else plan_node,
        )
        builder.add_conditional_edges(
            review_gate_node,
            review_router,
            [revise_node if has_revise else plan_node, dispatch_node],
        )

        # Revise → back to review gate (loop until approved)
        if has_revise:
            builder.add_edge(revise_node, review_gate_node)

        # Dispatch → fan-out to execute (conditional Send), only if execute exists
        if has_execute:
            builder.add_conditional_edges(
                dispatch_node,
                _fan_out_edge,
                [fan_out_target, END],
            )
        else:
            # No execute node — dispatch routes directly to assemble or finalize
            if _registered_assemble:
                builder.add_edge(dispatch_node, _registered_assemble[0])
            elif has_finalize:
                builder.add_edge(dispatch_node, finalize_node)
            else:
                builder.add_edge(dispatch_node, END)

        # Execute → assemble OR finalize
        if has_execute:
            if _registered_assemble:
                for name in _registered_assemble:
                    builder.add_edge(fan_out_target, name)
                if has_final_review:
                    builder.add_edge(_registered_assemble, final_review_node)
                elif has_finalize:
                    builder.add_edge(_registered_assemble, finalize_node)
            else:
                if has_final_review:
                    builder.add_edge(fan_out_target, final_review_node)
                elif has_finalize:
                    builder.add_edge(fan_out_target, finalize_node)

        # Final review → finalize
        if has_final_review and has_finalize:
            builder.add_edge(final_review_node, finalize_node)

        # Finalize → END
        if has_finalize:
            builder.add_edge(finalize_node, END)

        # -- compile -------------------------------------------------------
        interrupts = list(interrupt_before or [])
        if review_gate_node not in interrupts:
            interrupts.append(review_gate_node)

        graph = builder.compile(
            interrupt_before=interrupts,
            checkpointer=checkpointer,
        )

        self._logger.info(
            "Plan-execute graph compiled",
            nodes=len(_prepare) + len(_assemble) + 6,
            interrupt_before=interrupts,
        )
        return graph

    # ------------------------------------------------------------------
    # Debate mode
    # ------------------------------------------------------------------

    def build_debate(
        self,
        *,
        state_schema: type | None = None,
        checkpointer: Any = None,
        # -- positions --
        present_node: str = "present_positions",
        present_subgraph: Any = None,
        # -- cross-examine --
        cross_examine_node: str = "cross_examine",
        cross_examine_subgraph: Any = None,
        # -- judge --
        judge_node: str = "judge",
        # -- node registry --
        registry: NodeRegistry | None = None,
    ) -> Any:
        """Build a **present → cross-examine → judge** debate graph.

        Each position is presented in parallel, then cross-examined,
        and finally a judge issues a ruling synthesising all arguments.

        Returns a compiled ``StateGraph``.
        """
        reg = registry or NodeRegistry()
        schema = state_schema or (
            self.domain.state_schema if self.domain else dict
        )

        builder = StateGraph(schema)

        # Present positions (can be a fan-out sub-graph)
        if present_subgraph is not None:
            builder.add_node(present_node, present_subgraph)
        else:
            _fn = reg.get(present_node)
            if _fn is not None:
                builder.add_node(present_node, _fn)

        # Cross-examine
        if cross_examine_subgraph is not None:
            builder.add_node(cross_examine_node, cross_examine_subgraph)
        else:
            _fn = reg.get(cross_examine_node)
            if _fn is not None:
                builder.add_node(cross_examine_node, _fn)

        # Judge
        _judge_fn = reg.get(judge_node)
        if _judge_fn is not None:
            builder.add_node(judge_node, _judge_fn)

        # Wire edges
        builder.add_edge(START, present_node)
        builder.add_edge(present_node, cross_examine_node)
        builder.add_edge(cross_examine_node, judge_node)
        builder.add_edge(judge_node, END)

        graph = builder.compile(checkpointer=checkpointer)
        self._logger.info("Debate graph compiled")
        return graph

    # ------------------------------------------------------------------
    # Research mode
    # ------------------------------------------------------------------

    def build_research(
        self,
        *,
        state_schema: type | None = None,
        checkpointer: Any = None,
        interrupt_before: list[str] | None = None,
        # -- investigate --
        investigate_node: str = "investigate",
        investigate_subgraph: Any = None,
        # -- evaluate sufficiency --
        evaluate_node: str = "evaluate",
        max_rounds: int = 5,
        # -- produce output --
        produce_node: str = "produce_output",
        # -- node registry --
        registry: NodeRegistry | None = None,
    ) -> Any:
        """Build a recursive **investigate → evaluate → (loop | produce)** graph.

        The *investigate* node gathers information for one round.  *evaluate*
        checks a sufficiency condition (e.g. coverage score).  If sufficient
        the graph proceeds to *produce_output*; otherwise it loops back.

        Parameters
        ----------
        max_rounds:
            Hard stop after this many investigation rounds (safety limit).

        Returns a compiled ``StateGraph``.
        """
        reg = registry or NodeRegistry()
        schema = state_schema or (
            self.domain.state_schema if self.domain else dict
        )

        builder = StateGraph(schema)

        # Investigate
        if investigate_subgraph is not None:
            builder.add_node(investigate_node, investigate_subgraph)
        else:
            _fn = reg.get(investigate_node)
            if _fn is not None:
                builder.add_node(investigate_node, _fn)

        # Evaluate sufficiency
        _eval_fn = reg.get(evaluate_node)
        if _eval_fn is not None:
            builder.add_node(evaluate_node, _eval_fn)

        # Produce output
        _prod_fn = reg.get(produce_node)
        if _prod_fn is not None:
            builder.add_node(produce_node, _prod_fn)

        # Wire edges
        builder.add_edge(START, investigate_node)
        builder.add_edge(investigate_node, evaluate_node)

        # Evaluate → loop back or proceed
        def _sufficiency_router(state: dict[str, Any]) -> str:
            score = float(state.get("sufficiency_score", 0) or 0)
            threshold = float(state.get("sufficiency_threshold", 0.7) or 0.7)
            round_num = int(state.get("investigation_round", 0) or 0)
            max_r = int(state.get("max_rounds", max_rounds) or max_rounds)

            if round_num >= max_r:
                GLOBAL_LOGGER.info(
                    "Research: max rounds reached",
                    round=round_num,
                    max_rounds=max_r,
                )
                return produce_node
            if score >= threshold:
                GLOBAL_LOGGER.info(
                    "Research: sufficiency met",
                    score=score,
                    threshold=threshold,
                )
                return produce_node
            return investigate_node

        builder.add_conditional_edges(
            evaluate_node,
            _sufficiency_router,
            [investigate_node, produce_node],
        )

        builder.add_edge(produce_node, END)

        graph = builder.compile(
            checkpointer=checkpointer,
            interrupt_before=list(interrupt_before or []),
        )
        self._logger.info("Research graph compiled", max_rounds=max_rounds)
        return graph


# ---------------------------------------------------------------------------
# Fan-out edge helper (for conditional_edges after dispatch)
# ---------------------------------------------------------------------------

def _fan_out_edge(state: dict[str, Any]) -> list[Send] | str:
    """Route from dispatch node: if the node returned list[Send], use them.

    The dispatch node may return ``list[Send]`` (fan-out) or an empty dict
    (no items to dispatch → END).
    """
    # LangGraph injects the return value into state via the node's output.
    # The conditional edge receives the state.  If the dispatch node set a
    # key with the sends, we'd read it here.  But usually the dispatch node
    # returns the list[Send] directly, which LangGraph handles internally.
    #
    # For nodes that return list[Send] directly from within a StateGraph node,
    # LangGraph intercepts the return value.  For conditional edges, the
    # state is passed and the routing function should return the next node.
    #
    # In practice: the dispatch node returns list[Send] (which LangGraph
    # handles as a special fan-out return).  This conditional edge function
    # only runs as a fallback — if the dispatch node returned {} (no items),
    # we route to END.
    return END


# ---------------------------------------------------------------------------
# Convenience — build from a domain adapter
# ---------------------------------------------------------------------------

def build_graph_from_domain(
    domain: Any,
    mode: str = "plan_execute",
    checkpointer: Any = None,
    **kwargs: Any,
) -> Any:
    """One-shot graph builder from a domain adapter.

    If the domain has a ``get_nodes()`` method, its returned ``NodeRegistry``
    is used.  All extra *kwargs* are forwarded to the template's build method.

    Usage::

        graph = build_graph_from_domain(
            domain=my_adapter,
            mode="plan_execute",
            checkpointer=MemorySaver(),
            plan_node="create_analyst",
            execute_subgraph=interview_graph,
            # ...
        )
    """
    template = AgentGraphTemplate(mode=mode, domain=domain)

    # Auto-discover node registry from domain
    registry = None
    if hasattr(domain, "get_nodes"):
        registry = domain.get_nodes()
    elif hasattr(domain, "nodes"):
        registry = domain.nodes

    return template.build(checkpointer=checkpointer, registry=registry, **kwargs)
