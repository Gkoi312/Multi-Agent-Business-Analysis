"""
压缩效果对比实验 — 同一公司跑两次，对比上下文增长曲线

用法（在 backend/ 目录下）:
    python scripts/run_compression_comparison.py --company "拼多多" --focus "AI战略"
    python scripts/run_compression_comparison.py --company "DeepSeek" --turns 3 --analysts 2
    python scripts/run_compression_comparison.py --company "Meta" --only B --save

原理:
    A（无压缩基线）: compress 节点替换成 noop，每轮原始消息全部保留，
                     prompt_tokens 随轮次线性增长
    B（有压缩）:     正常运行 compress → update_memory → compact_history，
                     prompt_tokens 增长受控

注意: 两次都调用真实 API（DeepSeek + Serper/Bocha），消耗 token 和搜索配额。
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
os.chdir(BACKEND_DIR)

from dotenv import load_dotenv
load_dotenv(BACKEND_DIR / ".env")

import ssl
if os.getenv("SSL_NO_VERIFY") == "1":
    ssl._create_default_https_context = ssl._create_unverified_context
    os.environ.setdefault("CURL_CA_BUNDLE", "")
    os.environ.setdefault("REQUESTS_CA_BUNDLE", "")

RESULTS_DIR = BACKEND_DIR / "eval_results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

SEP  = "─" * 64
DSEP = "═" * 64


# ── 指标收集 ──────────────────────────────────────────────────────────────────

class MetricsCollector:
    def __init__(self):
        self.entries: list[dict[str, Any]] = []
        self._seen: set[str] = set()

    def feed(self, chunk: dict[str, Any]) -> None:
        for m in (chunk.get("llm_metrics") or []):
            if not isinstance(m, dict):
                continue
            key = (f"{m.get('node')}:{m.get('prompt_tokens')}:"
                   f"{m.get('completion_tokens')}:{m.get('latency_ms')}")
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
            tt = int(m.get("total_tokens", 0) or 0) or (
                int(m.get("prompt_tokens", 0) or 0) +
                int(m.get("completion_tokens", 0) or 0))
            s["total_tokens"]  += tt
            s["latency_ms"]    += int(m.get("latency_ms", 0) or 0)
        return result

    def ask_question_series(self) -> list[int]:
        return [int(m.get("prompt_tokens", 0) or 0)
                for m in self.entries if m.get("node") == "interview.ask_question"]

    def compress_events(self) -> list[dict]:
        return [m for m in self.entries if m.get("node") == "interview.compress"]

    def total_llm_tokens(self) -> int:
        """只统计真实 LLM API tokens（排除 compress 节点的估算值）。"""
        return sum(
            (int(m.get("total_tokens", 0) or 0) or
             int(m.get("prompt_tokens", 0) or 0) + int(m.get("completion_tokens", 0) or 0))
            for m in self.entries if m.get("node") != "interview.compress"
        )


# ── noop compress 节点 ────────────────────────────────────────────────────────

def _make_passthrough_compress():
    """不做任何 LLM 调用，只追加一个空 CompressedTurn 保持 turns_completed 对齐。"""
    def _noop(state: dict[str, Any]) -> dict:
        from harness.models.memory import CompressedTurn
        turn_count = int(state.get("turn_count", 1) or 1)
        empty = CompressedTurn(
            question_intent="(baseline: compression disabled)",
            facts=[], numbers_mentioned=[], key_findings=[],
            evidence_quality="low",
            compression_error="disabled for A/B baseline test",
        )
        history = list(state.get("compressed_turns", []) or [])
        history.append(empty.to_dict())
        return {
            "compressed_turns": history,
            "workflow_events": [{"event": "compress.skipped",
                                  "payload": {"turn": turn_count}}],
            "llm_metrics": [{"node": "interview.compress", "latency_ms": 0,
                              "prompt_tokens": 0, "completion_tokens": 0,
                              "total_tokens": 0, "facts_extracted": 0,
                              "baseline": True}],
        }
    return _noop


# ── 单次跑 ────────────────────────────────────────────────────────────────────

def run_once(company: str, focus: str, max_turns: int,
             max_analysts: int, label: str, use_compression: bool) -> dict[str, Any]:

    print(f"\n{SEP}")
    print(f"  [{label}]  {company} / {focus or '(通用)'}  "
          f"轮次={max_turns}  analysts={max_analysts}  "
          f"压缩={'ON' if use_compression else 'OFF'}")
    print(SEP)

    from harness.llm_loader import ModelLoader
    from domains.due_diligence.graph import AutonomousReportGenerator
    from domains.due_diligence.interview import InterviewGraphBuilder
    from langgraph.checkpoint.memory import MemorySaver

    llm = ModelLoader().load_llm()
    checkpointer = MemorySaver()
    reporter = AutonomousReportGenerator(llm, checkpointer=checkpointer)
    reporter.memory = checkpointer

    if use_compression:
        # 正常路径
        graph = reporter.build_graph()
    else:
        # Monkey-patch InterviewGraphBuilder.build，把 compress 换成 noop
        # reporter.build_graph() 内部会 new 一个 InterviewGraphBuilder 并调用 .build()
        orig_build = InterviewGraphBuilder.build

        def _patched_build(self_inner):
            from langgraph.graph import StateGraph, END as _END, START as _START
            from domains.due_diligence.schemas import InterviewState
            from harness.memory.nodes import (
                make_update_memory_node, make_compact_history_node,
                make_should_continue_router,
            )
            b = StateGraph(InterviewState)
            b.add_node("ask_question",    self_inner._generate_question)
            b.add_node("search_web",      self_inner._search_web)
            b.add_node("generate_answer", self_inner._generate_answer)
            b.add_node("compress",        _make_passthrough_compress())       # ← noop
            b.add_node("update_memory",   make_update_memory_node(self_inner._domain_config))
            b.add_node("compact_history", make_compact_history_node(self_inner.compressor))
            b.add_node("save_interview",  self_inner._save_interview)
            b.add_node("write_section",   self_inner._write_section)
            b.add_node("review_section",  self_inner._review_section)
            b.add_edge(_START,            "ask_question")
            b.add_edge("ask_question",    "search_web")
            b.add_edge("search_web",      "generate_answer")
            b.add_edge("generate_answer", "compress")
            b.add_edge("compress",        "update_memory")
            b.add_edge("update_memory",   "compact_history")
            b.add_conditional_edges(
                "compact_history",
                make_should_continue_router(continue_node="ask_question",
                                            stop_node="save_interview"),
                ["ask_question", "save_interview"],
            )
            b.add_edge("save_interview", "write_section")
            b.add_edge("write_section",  "review_section")
            b.add_edge("review_section", _END)
            return b.compile(checkpointer=self_inner.memory)

        InterviewGraphBuilder.build = _patched_build
        try:
            graph = reporter.build_graph()
        finally:
            InterviewGraphBuilder.build = orig_build  # 恢复，不影响后续调用

    thread_id = str(uuid.uuid4())
    thread = {"configurable": {"thread_id": thread_id}}
    metrics = MetricsCollector()

    rq = f"Conduct due diligence research on {company}"
    if focus:
        rq += f", focus on {focus}"

    t0 = time.perf_counter()

    # Phase 1: start_report_generation（在 human_feedback interrupt 前停止）
    for chunk in graph.stream(
        {
            "research_query": rq,
            "company_name":   company,
            "focus":          focus,
            "target_role":    "",
            "max_analysts":   max_analysts,
            "max_num_turns":  max_turns,
            "planner_enabled": True,
            "review_enabled":  True,
            "company_type":    "unknown",
            "skill_bundle": [], "research_skills": [], "skill_mapping": {},
            "source_policy_map": {}, "domain_memory": [],
            "router_decisions": [], "review_notes": [],
            "workflow_events": [], "llm_metrics": [],
        },
        thread,
        stream_mode="values",
    ):
        if isinstance(chunk, dict):
            metrics.feed(chunk)

    # Phase 2: 提交空 feedback，继续跑完
    graph.update_state(thread, {"human_analyst_feedback": ""}, as_node="human_feedback")
    for chunk in graph.stream(None, thread, stream_mode="values"):
        if isinstance(chunk, dict):
            metrics.feed(chunk)

    elapsed_sec = time.perf_counter() - t0
    state = graph.get_state(thread)

    wf_events    = state.values.get("workflow_events") or []
    compress_wf  = [e for e in wf_events if e.get("event") == "compress.completed"]
    ask_series   = metrics.ask_question_series()
    compress_ev  = metrics.compress_events()

    total_before = sum(int(e.get("prompt_tokens", 0) or 0) for e in compress_ev
                       if not e.get("baseline"))
    total_after  = sum(int(e.get("completion_tokens", 0) or 0) for e in compress_ev
                       if not e.get("baseline"))
    facts_total  = sum(e.get("facts_extracted", 0) or 0 for e in compress_ev
                       if not e.get("baseline"))

    return {
        "label":           label,
        "company":         company,
        "focus":           focus,
        "use_compression": use_compression,
        "elapsed_sec":     round(elapsed_sec, 1),
        "elapsed_min":     round(elapsed_sec / 60, 2),
        "total_llm_tokens": metrics.total_llm_tokens(),
        "by_node":         metrics.by_node(),
        "ask_q_series":    ask_series,
        "compress_events": compress_ev,
        "total_answer_tokens_before":    total_before,
        "total_compressed_tokens_after": total_after,
        "compression_ratio": round(total_after / total_before, 3) if total_before > 0 else None,
        "facts_extracted": facts_total,
    }


# ── 打印对比报告 ──────────────────────────────────────────────────────────────

def print_comparison(a: dict, b: dict) -> None:
    print(f"\n{DSEP}")
    print("  对比结果：A（无压缩基线）vs B（有压缩）")
    print(DSEP)

    sa, sb = a["ask_q_series"], b["ask_q_series"]
    n = min(len(sa), len(sb))
    if n:
        print(f"\n  ask_question prompt_tokens / 轮:")
        print(f"  {'轮':>4}  {'A 无压缩':>12}  {'B 有压缩':>12}  {'节省':>10}  {'减少%':>7}")
        print(f"  {'-'*4}  {'-'*12}  {'-'*12}  {'-'*10}  {'-'*7}")
        for i in range(n):
            va, vb = sa[i], sb[i]
            saved = va - vb
            pct = f"{saved/va*100:.0f}%" if va > 0 else "—"
            mark = "✓" if saved > 0 else ("△" if saved < 0 else "")
            print(f"  {i+1:>4}  {va:>12,}  {vb:>12,}  {saved:>+10,}  {pct:>7}  {mark}")

    if len(sa) >= 2 and len(sb) >= 2:
        ga = (sa[-1] - sa[0]) / sa[0] * 100 if sa[0] else 0
        gb = (sb[-1] - sb[0]) / sb[0] * 100 if sb[0] else 0
        print(f"\n  首→末轮增长率:  A = {ga:+.1f}%  |  B = {gb:+.1f}%")
        sl = sa[-1] - sb[-1]
        if sa[-1] > 0:
            print(f"  末轮节省 token: {sl:,}  ({sl/sa[-1]*100:.0f}%)")

    if b["compression_ratio"] is not None:
        ratio = b["compression_ratio"]
        print(f"\n  压缩统计（B 实验组，共 {len([e for e in b['compress_events'] if not e.get('baseline')])} 轮）:")
        print(f"    压缩前 answer tokens:  {b['total_answer_tokens_before']:>8,}")
        print(f"    压缩后 facts tokens:   {b['total_compressed_tokens_after']:>8,}")
        print(f"    压缩比:                {ratio:.3f}  （减少 {(1-ratio)*100:.1f}%）")
        print(f"    共提取结构化事实:      {b['facts_extracted']} 条")

    ta, tb = a["total_llm_tokens"], b["total_llm_tokens"]
    print(f"\n  总 LLM API tokens:  A = {ta:,}  |  B = {tb:,}")
    if ta > 0 and tb > 0:
        d = ta - tb
        print(f"  差值:               {d:+,}  ({d/ta*100:.1f}%)")

    print(f"\n  端到端耗时:  A = {a['elapsed_min']:.1f} min  |  B = {b['elapsed_min']:.1f} min")

    # 简历用摘要
    print(f"\n{DSEP}")
    print("  📋 简历用数据摘要")
    print(DSEP)
    if n >= 2 and b["compression_ratio"] is not None:
        saved_last_pct = (sa[-1] - sb[-1]) / sa[-1] * 100 if sa[-1] > 0 else 0
        ratio = b["compression_ratio"]
        facts = b["facts_extracted"]
        print(f"  在 {max_analysts_used} 个 analyst × {n//max_analysts_used} 轮并行尽调中（{b['company']}）：")
        print(f"  · 启用增量压缩后，末轮 prompt_tokens 减少 {saved_last_pct:.0f}%")
        print(f"    （A={sa[-1]:,} → B={sb[-1]:,}，节省 {sa[-1]-sb[-1]:,} tokens / 轮）")
        print(f"  · 每轮答案平均压缩至原始的 {ratio*100:.0f}%，提取结构化事实 {facts} 条")
    print(DSEP)


max_analysts_used = 2   # 用于摘要文字，由 main() 写入


# ── 主入口 ────────────────────────────────────────────────────────────────────

def main() -> None:
    global max_analysts_used
    parser = argparse.ArgumentParser(description="压缩效果 A/B 对比实验（真实 API）")
    parser.add_argument("--company",  default="拼多多",        help="调研公司")
    parser.add_argument("--focus",    default="AI战略与竞争格局", help="研究重点")
    parser.add_argument("--turns",    type=int, default=3,     help="每 analyst 最大轮次")
    parser.add_argument("--analysts", type=int, default=2,     help="analyst 数量（默认 2）")
    parser.add_argument("--only",     choices=["A", "B"],      help="只跑 A 或 B")
    parser.add_argument("--save",     action="store_true",     help="保存 JSON 结果")
    args = parser.parse_args()
    max_analysts_used = args.analysts

    results: dict[str, dict] = {}

    if args.only != "B":
        print("\n🔵 实验组 A — 无压缩基线")
        results["A"] = run_once(
            company=args.company, focus=args.focus,
            max_turns=args.turns, max_analysts=args.analysts,
            label="A-no-compression", use_compression=False,
        )

    if args.only != "A":
        print("\n🟢 实验组 B — 有压缩")
        results["B"] = run_once(
            company=args.company, focus=args.focus,
            max_turns=args.turns, max_analysts=args.analysts,
            label="B-with-compression", use_compression=True,
        )

    if "A" in results and "B" in results:
        print_comparison(results["A"], results["B"])
    elif results:
        only_key = next(iter(results))
        r = results[only_key]
        print(f"\n  [{only_key}] 耗时 {r['elapsed_min']:.1f} min  "
              f"tokens={r['total_llm_tokens']:,}")
        print(f"  ask_q series: {r['ask_q_series']}")

    if args.save:
        ts = time.strftime("%Y%m%d_%H%M%S")
        safe = args.company.replace("/", "_").replace(" ", "_")
        out  = RESULTS_DIR / f"compression_comparison_{safe}_{ts}.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False, default=str)
        print(f"\n[已保存] {out}")


if __name__ == "__main__":
    main()
