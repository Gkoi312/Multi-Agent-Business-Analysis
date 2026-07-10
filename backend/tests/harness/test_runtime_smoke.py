"""Smoke test for harness.runtime — verifies all modules import and basic functionality."""
import sys
sys.path.insert(0, ".")

from harness.runtime import (
    AgentGraphTemplate, GraphMode, NodeRegistry,
    build_graph_from_domain,
    fan_out, fan_out_if, FanOutNode,
    collect_sections, collect_metrics,
    CheckpointManager,
    keep_latest, merge_lists, merge_dicts,
)
from langgraph.types import Send


def test_checkpoint_manager():
    cpm = CheckpointManager(backend="memory")
    thread = cpm.new_thread()
    assert cpm.thread_id(thread)

    cpm2 = CheckpointManager(backend="sqlite", db_path=":memory:")
    assert cpm2.checkpointer is not None
    print("  CheckpointManager OK")


def test_node_registry():
    reg = NodeRegistry()
    reg.set("plan", lambda s: {"result": "ok"})
    reg.set("execute", lambda s: {"done": True})
    assert "plan" in reg
    assert "execute" in reg
    print(f"  NodeRegistry: {reg.names} OK")


def test_fan_out():
    items = [{"name": "alice"}, {"name": "bob"}]
    sends = fan_out(items, "interview", payload_fn=lambda x: {"analyst": x})
    assert len(sends) == 2
    assert isinstance(sends[0], Send)
    assert sends[0].node == "interview"
    print(f"  fan_out: {len(sends)} sends OK")

    # fan_out_if
    sends2 = fan_out_if(
        items, "interview",
        payload_fn=lambda x: {"analyst": x},
        predicate=lambda x: x["name"] == "alice",
        fallback_target="skip",
        fallback_payload_fn=lambda x: {"reason": "skipped"},
    )
    assert len(sends2) == 2
    print(f"  fan_out_if: {len(sends2)} sends OK")

    # FanOutNode
    fan_node = FanOutNode(
        items_key="analysts",
        target="conduct_interview",
        payload_mapping={"max_num_turns": "max_num_turns"},
        extra_payload={"turn_count": 0},
    )
    result = fan_node({"analysts": [{"name": "alice"}], "max_num_turns": 3})
    assert len(result) == 1
    assert isinstance(result[0], Send)
    print(f"  FanOutNode: {fan_node!r} OK")

    # Empty
    empty = fan_node({"analysts": [], "max_num_turns": 3})
    assert empty == {}
    print("  FanOutNode empty: returns {} OK")


def test_reducers():
    assert keep_latest(1, 2) == 2
    assert merge_lists([1], [2]) == [1, 2]
    assert merge_lists(None, [2]) == [2]
    result = merge_dicts({"a": 1}, {"a": 99})
    assert result == {"a": 99}
    print("  Reducers OK")


def test_collectors():
    state = {"sections": ["s1", "s2"]}
    assert collect_sections(state) == ["s1", "s2"]

    state2 = {
        "llm_metrics": [
            {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            {"prompt_tokens": 200, "completion_tokens": 100, "total_tokens": 300},
        ]
    }
    agg = collect_metrics(state2)
    assert agg["total_prompt_tokens"] == 300
    assert agg["total_completion_tokens"] == 150
    assert agg["call_count"] == 2
    print(f"  collect_metrics: {agg} OK")


def test_graph_templates():
    from domains.base import DomainAdapter

    class MockDomain(DomainAdapter):
        @property
        def domain_name(self): return "mock"
        @property
        def state_schema(self): return dict

        def get_nodes(self):
            reg = NodeRegistry()
            reg.set("plan", lambda s: {"plan_items": [{"id": 1}]})
            reg.set("revise", lambda s: {"plan_items": [{"id": 2}]})
            reg.set("dispatch", lambda s: [])
            reg.set("execute", lambda s: {"section": "mock"})
            reg.set("write_report", lambda s: {"content": "mock report"})
            reg.set("finalize", lambda s: {"final_output": "done"})
            return reg

    domain = MockDomain()
    cpm = CheckpointManager()

    # Plan-execute
    template_pe = AgentGraphTemplate(mode="plan_execute", domain=domain)
    graph_pe = template_pe.build_plan_execute(
        checkpointer=cpm.checkpointer,
        registry=domain.get_nodes(),
        plan_node="plan",
        revise_node="revise",
        dispatch_node="dispatch",
        assemble_nodes=["write_report"],
        finalize_node="finalize",
    )
    print(f"  Plan-execute: {type(graph_pe).__name__} OK")

    # Debate
    template_db = AgentGraphTemplate(mode="debate", domain=domain)
    graph_db = template_db.build_debate(
        checkpointer=cpm.checkpointer,
        registry=domain.get_nodes(),
        present_node="plan",
        cross_examine_node="execute",
        judge_node="finalize",
    )
    print(f"  Debate: {type(graph_db).__name__} OK")

    # Research
    template_rs = AgentGraphTemplate(mode="research", domain=domain)
    graph_rs = template_rs.build_research(
        checkpointer=cpm.checkpointer,
        registry=domain.get_nodes(),
        investigate_node="execute",
        evaluate_node="plan",
        produce_node="finalize",
        max_rounds=3,
    )
    print(f"  Research: {type(graph_rs).__name__} OK")


def test_build_graph_from_domain():
    from domains.base import DomainAdapter

    class MockDomain(DomainAdapter):
        @property
        def domain_name(self): return "mock"
        @property
        def state_schema(self): return dict

        def get_nodes(self):
            reg = NodeRegistry()
            reg.set("plan", lambda s: {"plan_items": [{"id": 1}]})
            reg.set("dispatch", lambda s: [])
            reg.set("write_report", lambda s: {"content": "mock"})
            reg.set("finalize", lambda s: {"final_output": "done"})
            return reg

    domain = MockDomain()
    cpm = CheckpointManager()

    graph = build_graph_from_domain(
        domain=domain,
        mode="plan_execute",
        checkpointer=cpm.checkpointer,
        plan_node="plan",
        dispatch_node="dispatch",
        assemble_nodes=["write_report"],
        finalize_node="finalize",
    )
    print(f"  build_graph_from_domain: {type(graph).__name__} OK")

    graph2 = domain.build_graph(
        checkpointer=cpm.checkpointer,
        plan_node="plan",
        dispatch_node="dispatch",
        assemble_nodes=["write_report"],
        finalize_node="finalize",
    )
    print(f"  DomainAdapter.build_graph: {type(graph2).__name__} OK")


if __name__ == "__main__":
    print("=== harness.runtime smoke test ===")
    test_checkpoint_manager()
    test_node_registry()
    test_fan_out()
    test_reducers()
    test_collectors()
    test_graph_templates()
    test_build_graph_from_domain()
    print("=== ALL PASSED ===")
