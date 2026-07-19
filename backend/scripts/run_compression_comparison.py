"""
压缩效果对比实验 — 同一公司跑两次，对比上下文增长曲线

用法（在 backend/ 目录下）:
    python scripts/run_compression_comparison.py --company "拼多多" --focus "电商AI战略"
    python scripts/run_compression_comparison.py --company "DeepSeek" --turns 3

原理:
    - 实验组 A（无压缩）: 把 compress 节点替换成 pass-through，
      每轮完整原始消息都保留在 messages 里，prompt_tokens 随轮次线性增长
    - 实验组 B（有压缩）: 正常运行，compress → update_memory → compact_history
      全部生效，prompt_tokens 增长受控

两次跑完后对比:
    - 每轮 ask_question 的 prompt_tokens 序列
    - 最终轮 prompt_tokens 差值（节省量）
    - 压缩后事实提取情况

注意: 两次跑都调用真实 API（DeepSeek + Serper/Bocha），会消耗 token 和搜索配额。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

# ── 路径 ──────────────────────────────────────────────────────────────────────
BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
os.chdir(BACKEND_DIR)  # .env 在 backend/

from dotenv import load_dotenv
load_dotenv(BACKEND_DIR / ".env")

import ssl
if os.getenv("SSL_NO_VERIFY") == "1":
    ssl._create_default_https_context = ssl._create_unverified_context
    os.environ.setdefault("CURL_CA_BUNDLE", "")
    os.environ.setdefault("REQUESTS_CA_BUNDLE", "")

RESULTS_DIR = BACKEND_DIR / "eval_results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# ── 深色分隔线 ────────────────────────────────────────────────────────────────
SEP  = "─" * 64
DSEP = "═" * 64


# ── 指标收集 hook ─────────────────────────────────────────────────────────────

class MetricsCollector:
    """收集 graph stream 过程中的 llm_metrics。"""

    def __init__(self):
        self.entries: list[dict[str, Any]] = []
        self._seen: set[str] = set()

    def feed(self, chunk: dict[str, Any]) -> None:
        for m in (chunk.get("llm_metrics") or []):
            if not isinstance(m, dict):
                continue
            key = f"{m.get('node')}:{m.get('prompt_tokens')}:{m.get('completion_tokens')}:{m.get('latency_ms')}"
            if key in self._seen:
                continue
            self._seen.add(key)
            self.entries.append(dict(m))

    def by_node(self) -> dict[str, dict]:
        result: dict[str, dict] = {}
        for m in self.entries:
            node = m.get("node", "unknown")
            s = result.setdefault(node, {"calls": 0, "prompt_tokens": 0,
                                         "completion_tokens": 0, "total_tokens": 0,
                                         "latency_ms": 0})
            s["calls"]             += 1
            s["prompt_tokens"]     += int(m.get("prompt_tokens", 0) or 0)
            s["completion_tokens"] += int(m.get("completion_tokens", 0) or 0)
            s["total_tokens"]      += int(m.get("total_tokens", 0) or 0) or (
                int(m.get("prompt_tokens", 0) or 0) + int(m.get("completion_tokens", 0) or 0))
            s["latency_ms"]        += int(m.get("latency_ms", 0) or 0)
        return result

    def ask_question_series(self) -> list[int]:
        return [int(m.get("prompt_tokens", 0) or 0)
                for m in self.entries if m.get("node") == "interview.ask_question"]

    def compress_events(self) -> list[dict]:
        return [m for m in self.entries if m.get("node") == "interview.compress"]

    def total_llm_tokens(self) -> int:
        """只统计真实 LLM API 调用的 token（排除 compress 节点的估算值）。"""
        return sum(
            int(m.get("total_tokens", 0) or 0) or
            (int(m.get("prompt_tokens", 0) or 0) + int(m.get("completion_tokens", 0) or 0))
            for m in self.entries if m.get("node") != "interview.compress"
        )


# ── 无压缩 monkey-patch ───────────────────────────────────────────────────────

def _make_passthrough_compress():
    """返回一个不做任何压缩的 pass-through compress 节点。"""
    def _noop_compress(state: dict[str, Any]) -> dict:
        turn_count = int(state.get("turn_count", 1) or 1)
        # 仍然要追加一个空 CompressedTurn，否则 update_memory 的 turns_completed 对不上
        from harness.models.memory import CompressedTurn
        empty = CompressedTurn(
            question_intent="(no-compression baseline)",
            facts=[],
            numbers_mentioned=[],
            key_findings=[],
            evidence_quality="low",
            compression_error="disabled for baseline test",
        )
        compressed_history = list(state.get("compressed_turns", []) or [])
        compressed_history.append(empty.to_dict())
        return {
            "compressed_turns": compressed_history,
            "workflow_events": [{
                "event": "compress.skipped",
                "payload": {"turn": turn_count, "reason": "baseline_no_compression"},
            }],
            "llm_metrics": [{
                "node": "interview.compress",
                "latency_ms": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "facts_extracted": 0,
                "baseline": True,
            }],
        }
    return _noop_compress


# ── 单次跑 ────────────────────────────────────────────────────────────────────

def run_once(
    company: str,
    focus: str,
    max_turns: int,
    max_analysts: int,
    label: str,
    use_compression: bool,
) -> dict[str, Any]:
    """跑一次完整的尽调流程，返回采集到的指标。"""

    print(f"\n{SEP}")
    print(f"  [{label}] 公司: {company}  focus: {focus or '(通用)'}  "
          f"轮次: {max_turns}  压缩: {'ON' if use_compression else 'OFF'}")
    print(SEP)

    from harness.llm_loader import ModelLoader
    from domains.due_diligence.graph import AutonomousReportGenerator
    from domains.due_diligence.interview import InterviewGraphBuilder
    from harness.memory.nodes import make_compress_node
    from langgraph.checkpoint.memory import MemorySaver
    from langchain_core.messages import HumanMessage

    llm = ModelLoader().load_llm()
    checkpointer = MemorySaver()

    # 构建 interview graph，选择是否替换 compress 节点
    if use_compression:
        interview_graph = InterviewGraphBuilder(
            llm,
            checkpointer=checkpointer,
        ).build()
    else:
        # 把 compress 替换成 pass-through：重新 build 但注入 noop
        builder_obj = InterviewGraphBuilder(llm, checkpointer=checkpointer)
        # 重新 patch build 方法里的 compress node
        from langgraph.graph import StateGraph, END, START
        from domains.due_diligence.schemas import InterviewState
        from harness.memory.nodes import (
            make_update_memory_node, make_compact_history_node, make_should_continue_router,
        )
        ig = StateGraph(InterviewState)
        ig.add_node("ask_question",    builder_obj._generate_question)
        ig.add_node("search_web",      builder_obj._search_web)
        ig.add_node("generate_answer", builder_obj._generate_answer)
        ig.add_node("compress",        _make_passthrough_compress())        # ← noop
        ig.add_node("update_memory",   make_update_memory_node(builder_obj._domain_config))
        ig.add_node("compact_history", make_compact_history_node(builder_obj.compressor))
        ig.add_node("save_interview",  builder_obj._save_interview)
        ig.add_node("write_section",   builder_obj._write_section)
        ig.add_node("review_section",  builder_obj._review_section)
        ig.add_edge(START, "ask_question")
        ig.add_edge("ask_question",    "search_web")
        ig.add_edge("search_web",      "generate_answer")
        ig.add_edge("generate_answer", "compress")
        ig.add_edge("compress",        "update_memory")
        ig.add_edge("update_memory",   "compact_history")
        ig.add_conditional_edges(
            "compact_history",
            make_should_continue_router(continue_node="ask_question", stop_node="save_interview"),
            ["ask_question", "save_interview"],
        )
        ig.add_edge("save_interview", "write_section")
        ig.add_edge("write_section",  "review_section")
        ig.add_edge("review_section", END)
        interview_graph = ig.compile(checkpointer=checkpointer)

    # 构建外层 report graph
    reporter = AutonomousReportGenerator(llm, checkpointer=checkpointer)
    reporter.memory = checkpointer

    # 替换 conduct_interview 节点为我们定制的 interview_graph
    from langgraph.graph import StateGraph, END as GEND, START as GSTART
    from langgraph.types import Send
    from domains.due_diligence.schemas import ResearchGraphState
    from harness.models.agent import ResearchPlan

    # 简化：直接用 reporter 的 build_graph，但 monkey-patch interviewgraph 内的 compress
    # 对于"无压缩"版本，我们把整个外层图也重新 build
    if not use_compression:
        # 重新构建外层图，把 conduct_interview 替换
        from langgraph.graph import StateGraph as SG2
        from langchain_core.messages import HumanMessage as HM2
        outer = SG2(ResearchGraphState)

        def _initiate(state):
            rq = state.get("research_query", "")
            cn = state.get("company_name", "") or ""
            fc = state.get("focus", "") or ""
            analysts = state.get("analysts", [])
            research_plan = state.get("research_plan")
            skill_bundle = state.get("skill_bundle", []) or []
            domain_memory = state.get("domain_memory", []) or []
            mnt = int(state.get("max_num_turns", 1) or 1)
            if not analysts:
                return GEND

            analyst_plan_map = {}
            analyst_plans = (research_plan.analyst_plans if research_plan else []) or []
            if analyst_plans:
                analyst_plan_map = {p.analyst_name: p for p in analyst_plans}
            skill_card_by_id = {
                str(s.get("id", "") or "").strip(): s
                for s in skill_bundle if str(s.get("id", "") or "").strip()
            }
            return [
                Send("conduct_interview", {
                    "analyst": a,
                    "skill_card": skill_card_by_id.get(str(a.skill_id or "").strip()),
                    "assigned_plan": analyst_plan_map.get(a.name),
                    "domain_memory": domain_memory,
                    "messages": [HM2(content=f"Let's discuss: {rq}", id=str(uuid.uuid4()))],
                    "max_num_turns": mnt,
                    "turn_count": 0,
                    "company_name": cn,
                    "focus": fc,
                    "context": [], "retrieved_sources": [], "router_decisions": [],
                    "review_notes": [], "workflow_events": [], "interview": "",
                    "sections": [], "llm_metrics": [], "compressed_turns": [],
                    "working_memory": {}, "running_summary": {}, "search_digest": {},
                    "source_registry": {}, "memory_snapshot": {},
                })
                for a in analysts
            ]

        outer.add_node("classify_company_type", reporter.classify_company_type)
        outer.add_node("assemble_skills",        reporter.assemble_skills)
        outer.add_node("create_analyst",         reporter.create_analyst)
        outer.add_node("human_feedback",         reporter.human_feedback)
        outer.add_node("regenerate_analyst",     reporter.regenerate_analyst)
        outer.add_node("plan_research",          reporter.plan_research)
        outer.add_node("start_interviews",       lambda s: {})
        outer.add_node("conduct_interview",      interview_graph)
        outer.add_node("write_report",           reporter.write_report)
        outer.add_node("write_introduction",     reporter.write_introduction)
        outer.add_node("write_conclusion",       reporter.write_conclusion)
        outer.add_node("review_report",          reporter.review_report)
        outer.add_node("finalize_report",        reporter.finalize_report)

        def _route_feedback(state):
            fb = (state.get("human_analyst_feedback", "") or "").strip()
            return "regenerate_analyst" if fb else "plan_research"

        outer.add_edge(GSTART, "classify_company_type")
        outer.add_edge("classify_company_type", "assemble_skills")
        outer.add_edge("assemble_skills", "create_analyst")
        outer.add_edge("create_analyst", "human_feedback")
        outer.add_conditional_edges("human_feedback", _route_feedback,
                                    ["regenerate_analyst", "plan_research"])
        outer.add_edge("plan_research", "start_interviews")
        outer.add_conditional_edges("start_interviews", _initiate,
                                    ["conduct_interview", GEND])
        outer.add_edge("regenerate_analyst", "human_feedback")
        outer.add_edge("conduct_interview", "write_report")
        outer.add_edge("conduct_interview", "write_introduction")
        outer.add_edge("conduct_interview", "write_conclusion")
        outer.add_edge(["write_report", "write_introduction", "write_conclusion"], "review_report")
        outer.add_edge("review_report", "finalize_report")
        outer.add_edge("finalize_report", GEND)
        graph = outer.compile(interrupt_before=["human_feedback"], checkpointer=checkpointer)
    else:
        graph = reporter.build_graph()

    thread_id = str(uuid.uuid4())
    thread = {"configurable": {"thread_id": thread_id}}
    metrics = MetricsCollector()

    research_query = f"Conduct due diligence research on {company}"
    if focus:
        research_query += f", focus on {focus}"

    t0 = time.perf_counter()

    # Phase 1: 生成 analyst（在 human_feedback 前停止）
    for chunk in graph.stream(
        {
            "research_query": research_query,
            "company_name": company,
            "focus": focus,
            "target_role": "",
            "max_analysts": max_analysts,
            "max_num_turns": max_turns,
            "planner_enabled": True,
            "review_enabled": True,
            "company_type": "unknown",
            "skill_bundle": [], "research_skills": [], "skill_mapping": {},
            "source_policy_map": {}, "domain_memory": [], "router_decisions": [],
            "review_notes": [], "workflow_events": [], "llm_metrics": [],
        },
        thread,
        stream_mode="values",
    ):
        if isinstance(chunk, dict):
            metrics.feed(chunk)

    # Phase 2: 提交空 feedback，继续跑到完成
    graph.update_state(thread, {"human_analyst_feedback": ""}, as_node="human_feedback")
    for chunk in graph.stream(None, thread, stream_mode="values"):
        if isinstance(chunk, dict):
            metrics.feed(chunk)

    elapsed_sec = time.perf_counter() - t0
    state = graph.get_state(thread)

    # 从 state 里捞 workflow_events 中的 compress 事件（补充 compress_events）
    wf_events = state.values.get("workflow_events") or []
    compress_wf = [e for e in wf_events if e.get("event") == "compress.completed"]

    ask_series = metrics.ask_question_series()
    compress_ev = metrics.compress_events()

    # 计算压缩前 vs 压缩后 token（从 compress_events 里）
    total_answer_tokens = sum(int(e.get("prompt_tokens", 0) or 0) for e in compress_ev
                              if not e.get("baseline"))
    total_compressed_tokens = sum(int(e.get("completion_tokens", 0) or 0) for e in compress_ev
                                   if not e.get("baseline"))

    result = {
        "label":            label,
        "company":          company,
        "focus":            focus,
        "use_compression":  use_compression,
        "elapsed_sec":      round(elapsed_sec, 1),
        "elapsed_min":      round(elapsed_sec / 60, 2),
        "total_llm_tokens": metrics.total_llm_tokens(),
        "by_node":          metrics.by_node(),
        "ask_q_series":     ask_series,
        "compress_events":  compress_ev,
        "compress_wf":      compress_wf,
        "total_answer_tokens_before": total_answer_tokens,
        "total_compressed_tokens_after": total_compressed_tokens,
        "compression_ratio": round(total_compressed_tokens / total_answer_tokens, 3)
                             if total_answer_tokens > 0 else None,
        "facts_extracted":  sum(e.get("facts_extracted", 0) or
                                e.get("payload", {}).get("facts_extracted", 0)
                                for e in (compress_ev + compress_wf)),
    }
    return result


# ── 打印对比报告 ──────────────────────────────────────────────────────────────

def print_comparison(a: dict, b: dict) -> None:
    """a = 无压缩基线，b = 有压缩实验组"""
    print(f"\n{DSEP}")
    print("  对比结果：无压缩(A) vs 有压缩(B)")
    print(DSEP)

    def _series_str(series: list[int]) -> str:
        return "  ".join(f"{v:,}" for v in series)

    # ask_question prompt_tokens 序列
    sa, sb = a["ask_q_series"], b["ask_q_series"]
    print(f"\n  ask_question prompt_tokens / 轮:")
    print(f"  {'轮':>4}  {'A (无压缩)':>12}  {'B (有压缩)':>12}  {'节省':>10}")
    print(f"  {'-'*4}  {'-'*12}  {'-'*12}  {'-'*10}")
    for i, (va, vb) in enumerate(zip(sa, sb), 1):
        saved = va - vb
        pct = f"{saved/va*100:.0f}%" if va > 0 and saved > 0 else ("增加" if saved < 0 else "—")
        print(f"  {i:>4}  {va:>12,}  {vb:>12,}  {saved:>+10,}  {pct}")

    # 首尾对比
    if len(sa) >= 2 and len(sb) >= 2:
        a_growth = (sa[-1] - sa[0]) / sa[0] * 100 if sa[0] > 0 else 0
        b_growth = (sb[-1] - sb[0]) / sb[0] * 100 if sb[0] > 0 else 0
        print(f"\n  首→末轮增长率:  A = {a_growth:+.1f}%   B = {b_growth:+.1f}%")
        if len(sa) > 0 and len(sb) > 0:
            saved_last = sa[-1] - sb[-1]
            if sa[-1] > 0:
                print(f"  末轮节省 token: {saved_last:,}  ({saved_last/sa[-1]*100:.1f}%)")

    # 压缩统计
    if b["compression_ratio"] is not None:
        ratio = b["compression_ratio"]
        reduction = (1 - ratio) * 100
        print(f"\n  压缩统计（实验组 B）:")
        print(f"    压缩前 answer tokens:  {b['total_answer_tokens_before']:>8,}")
        print(f"    压缩后 facts tokens:   {b['total_compressed_tokens_after']:>8,}")
        print(f"    压缩比:                {ratio:.3f}  （减少 {reduction:.1f}%）")
        print(f"    共提取事实:            {b['facts_extracted']} 条")

    # 总体 token 对比
    ta, tb = a["total_llm_tokens"], b["total_llm_tokens"]
    print(f"\n  总 LLM API tokens:  A = {ta:,}   B = {tb:,}")
    if ta > 0:
        diff = ta - tb
        print(f"  节省:               {diff:,}  ({diff/ta*100:.1f}%)")

    # 耗时
    print(f"\n  耗时:  A = {a['elapsed_min']:.1f} min   B = {b['elapsed_min']:.1f} min")

    # 简历用一句话
    print(f"\n{DSEP}")
    print("  📋 简历用数据")
    print(DSEP)
    if b["compression_ratio"] is not None and len(sa) >= 2 and len(sb) >= 2:
        saved_last_pct = (sa[-1] - sb[-1]) / sa[-1] * 100 if sa[-1] > 0 else 0
        ratio = b["compression_ratio"]
        facts = b["facts_extracted"]
        n_turns = min(len(sa), len(sb))
        print(f"  在 {n_turns} 轮多角色并行尽调中，启用增量压缩后：")
        print(f"  · 末轮 ask_question prompt_tokens 减少 {saved_last_pct:.0f}%")
        print(f"    （A={sa[-1]:,} → B={sb[-1]:,}，节省 {sa[-1]-sb[-1]:,} tokens）")
        print(f"  · 每轮答案平均压缩至原文的 {ratio*100:.0f}%（提取结构化事实 {facts} 条）")
    print(DSEP)


# ── 主入口 ────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="压缩效果对比实验（真实 API 调用）")
    parser.add_argument("--company", default="拼多多",   help="调研公司名称")
    parser.add_argument("--focus",   default="AI战略与竞争格局", help="研究重点")
    parser.add_argument("--turns",   type=int, default=3, help="每个 analyst 的最大轮次（默认 3）")
    parser.add_argument("--analysts", type=int, default=2, help="analyst 数量（默认 2，节省成本）")
    parser.add_argument("--only",    choices=["A", "B"],   help="只跑 A（无压缩）或 B（有压缩）")
    parser.add_argument("--save",    action="store_true",  help="保存结果到 eval_results/")
    args = parser.parse_args()

    results = {}

    if args.only != "B":
        print(f"\n🔵 实验组 A — 无压缩基线")
        results["A"] = run_once(
            company=args.company, focus=args.focus,
            max_turns=args.turns, max_analysts=args.analysts,
            label="A-no-compression", use_compression=False,
        )

    if args.only != "A":
        print(f"\n🟢 实验组 B — 有压缩")
        results["B"] = run_once(
            company=args.company, focus=args.focus,
            max_turns=args.turns, max_analysts=args.analysts,
            label="B-with-compression", use_compression=True,
        )

    if "A" in results and "B" in results:
        print_comparison(results["A"], results["B"])

    if args.save:
        ts = time.strftime("%Y%m%d_%H%M%S")
        out = RESULTS_DIR / f"compression_comparison_{args.company}_{ts}.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False, default=str)
        print(f"\n[已保存] {out}")


if __name__ == "__main__":
    main()
