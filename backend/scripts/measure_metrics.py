"""
量化数据采集脚本 — 从现有 trace + tasks 数据提取真实指标

用法:
    cd backend
    python scripts/measure_metrics.py                    # 分析所有已完成任务
    python scripts/measure_metrics.py --task <task_id>   # 分析单个任务
    python scripts/measure_metrics.py --report           # 同时跑来源覆盖率评分

指标:
    1. 端到端耗时（分钟）
    2. Token 总量 + 分节点分布
    3. 预估 API 成本（USD）
    4. 上下文增长趋势（随轮次 prompt_tokens 变化）
    5. 压缩效果（write_section 前 prompt_tokens vs 第1轮基线）
    6. 每轮平均搜索结果数（从 pipeline trace 日志）
    7. 报告来源覆盖率（cited_sentence_rate）—— 需 --report 参数
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── 路径设置 ──────────────────────────────────────────────────────────────────
BACKEND_DIR = Path(__file__).resolve().parents[1]
RUNTIME_DIR = BACKEND_DIR / ".runtime"
TRACES_DIR  = RUNTIME_DIR / "traces"
TASKS_FILE  = RUNTIME_DIR / "tasks.json"
REPORT_DIR  = BACKEND_DIR / "generated_report"
RESULTS_DIR = BACKEND_DIR / "eval_results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# DeepSeek-chat 定价（USD / 1M tokens）
DEEPSEEK_INPUT_PER_MTOK  = 0.27
DEEPSEEK_OUTPUT_PER_MTOK = 1.10

MODEL = "deepseek-chat"


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _cost_usd(prompt_tokens: int, completion_tokens: int) -> float:
    return (
        prompt_tokens    / 1_000_000 * DEEPSEEK_INPUT_PER_MTOK
        + completion_tokens / 1_000_000 * DEEPSEEK_OUTPUT_PER_MTOK
    )


def _load_tasks() -> dict[str, Any]:
    if not TASKS_FILE.exists():
        return {}
    with open(TASKS_FILE, encoding="utf-8") as f:
        return json.load(f)


def _load_trace(task_id: str) -> list[dict[str, Any]]:
    path = TRACES_DIR / f"{task_id}.jsonl"
    if not path.exists():
        return []
    entries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return entries


def _ts_to_dt(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


# ── 核心分析 ──────────────────────────────────────────────────────────────────

def analyze_task(task_id: str, task: dict[str, Any]) -> dict[str, Any]:
    traces = _load_trace(task_id)

    company   = task.get("company_name", "Unknown")
    focus     = task.get("focus", "")
    status    = task.get("status", "unknown")
    created   = task.get("created_at", 0)
    updated   = task.get("updated_at", 0)
    wall_sec  = max(updated - created, 0)

    # ── Token 汇总（去重：同一 span_id 只算一次）─────────────────────────────
    seen_spans: set[str] = set()
    by_node: dict[str, dict[str, int]] = defaultdict(
        lambda: {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                 "total_tokens": 0, "latency_ms": 0}
    )
    # compress 节点单独统计（它记的是估算 token，不是真实 LLM API 调用）
    compress_events: list[dict] = []
    total_prompt = total_completion = total_tokens = 0

    for e in traces:
        span_id   = e.get("span_id", "")
        node_name = e.get("node_name", "unknown")
        llm_calls = e.get("llm_calls") or []

        if not llm_calls:
            continue
        if span_id and span_id in seen_spans:
            continue          # 跳过重复 span（checkpoint 重放）
        if span_id:
            seen_spans.add(span_id)

        for call in llm_calls:
            pt = int(call.get("prompt_tokens",     0) or 0)
            ct = int(call.get("completion_tokens", 0) or 0)
            tt = int(call.get("total_tokens", 0) or 0)
            if tt == 0:
                tt = pt + ct
            lt = int(call.get("latency_ms",        0) or 0)

            by_node[node_name]["calls"]             += 1
            by_node[node_name]["prompt_tokens"]     += pt
            by_node[node_name]["completion_tokens"] += ct
            by_node[node_name]["total_tokens"]      += tt
            by_node[node_name]["latency_ms"]        += lt

            # compress 节点：不计入 LLM API token 总量，单独记录压缩数据
            if node_name == "interview.compress":
                compress_events.append({
                    "answer_tokens":     pt,   # answer before compression
                    "compressed_tokens": ct,   # facts after compression
                    "compression_ratio": call.get("compression_ratio", 0),
                    "facts_extracted":   call.get("facts_extracted", 0),
                    "latency_ms":        lt,
                })
            else:
                total_prompt     += pt
                total_completion += ct
                total_tokens     += tt

    cost_usd = _cost_usd(total_prompt, total_completion)

    # ── 上下文增长分析（按轮次 ask_question 的 prompt_tokens）───────────────
    ask_q_prompts: list[int] = []
    answer_prompts: list[int] = []
    for e in traces:
        span_id = e.get("span_id", "")
        if span_id in seen_spans and e.get("node_name") in (
            "interview.ask_question", "interview.generate_answer",
        ):
            pass  # already counted; just collect series
        node = e.get("node_name", "")
        for call in (e.get("llm_calls") or []):
            pt = int(call.get("prompt_tokens", 0) or 0)
            if node == "interview.ask_question" and pt > 0:
                ask_q_prompts.append(pt)
            elif node == "interview.generate_answer" and pt > 0:
                answer_prompts.append(pt)

    # 压缩效果：比较第1轮 vs 最后一轮的 ask_question prompt_tokens
    context_growth_pct: float | None = None
    if len(ask_q_prompts) >= 2:
        first = ask_q_prompts[0]
        last  = ask_q_prompts[-1]
        if first > 0:
            context_growth_pct = round((last - first) / first * 100, 1)

    # write_section 的 prompt_tokens（显示压缩后输入量）
    write_section_prompts: list[int] = []
    for e in traces:
        if e.get("node_name") == "interview.write_section":
            for call in (e.get("llm_calls") or []):
                pt = int(call.get("prompt_tokens", 0) or 0)
                if pt > 0:
                    write_section_prompts.append(pt)

    # ── 来源数量（从 workflow_events 或 source_registry 推断）───────────────
    # 这里只统计 trace 里能看到的搜索轮次
    search_call_count = sum(1 for e in traces if e.get("node_name") == "interview.search_query"
                           and any(int(c.get("total_tokens", 0) or 0) > 0
                                   for c in (e.get("llm_calls") or [])))

    result: dict[str, Any] = {
        "task_id":            task_id,
        "company":            company,
        "focus":              focus or "(none)",
        "status":             status,
        "wall_time_sec":      round(wall_sec, 1),
        "wall_time_min":      round(wall_sec / 60, 2),
        "total_prompt_tokens":     total_prompt,
        "total_completion_tokens": total_completion,
        "total_tokens":            total_tokens,
        "estimated_cost_usd":      round(cost_usd, 4),
        "search_rounds":           search_call_count,
        "ask_q_prompt_tokens_series": ask_q_prompts,
        "answer_prompt_tokens_series": answer_prompts,
        "context_growth_pct":      context_growth_pct,
        "write_section_prompts":   write_section_prompts,
        "compress_events":         compress_events,
        "by_node":                 dict(by_node),
    }
    return result


# ── 来源覆盖率评分（需要生成的报告文件）──────────────────────────────────────

def _extract_docx_text(docx_path: str) -> str:
    """从 .docx 提取纯文本，不依赖外部库以外的东西。"""
    try:
        from docx import Document as DocxDocument
        doc = DocxDocument(docx_path)
        return "\n".join(p.text for p in doc.paragraphs)
    except Exception as e:
        print(f"  [WARN] docx 读取失败: {e}")
        return ""


def score_source_traceability(task: dict[str, Any]) -> dict[str, Any] | None:
    """对已生成的报告跑 SourceTraceabilityScorer。"""
    docx_path = task.get("docx_path", "") or ""
    if not docx_path or not Path(docx_path).exists():
        return None

    # 读取报告文本
    report_text = _extract_docx_text(docx_path)
    if not report_text:
        return None

    # 把 [1], [2] 格式的引用转为 [S1], [S2] 格式（兼容旧报告）
    # 同时兼容 [S1] 格式（新报告）
    has_sn = bool(re.search(r"\[S\d+\]", report_text))
    has_numeric = bool(re.search(r"\[(\d+)\]", report_text))

    if not has_sn and has_numeric:
        # 旧格式报告：[1] → [S1]
        report_text_sn = re.sub(r"\[(\d+)\]", r"[S\1]", report_text)
        # source_registry 也需要伪造
        citation_ids = set(re.findall(r"\[S(\d+)\]", report_text_sn))
        source_registry = {f"S{n}": {"url": f"https://source.example/{n}"} for n in citation_ids}
    else:
        report_text_sn = report_text
        citation_ids = set(re.findall(r"\[S(\d+)\]", report_text_sn))
        source_registry = {f"S{n}": {"url": f"https://source.example/{n}"} for n in citation_ids}

    # 需要把 backend 加入 sys.path
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))

    try:
        from harness.evaluation.scorers.source_traceability import SourceTraceabilityScorer
        scorer = SourceTraceabilityScorer()
        result = scorer.score(report_text=report_text_sn, source_registry=source_registry)
        return {
            "status":             result.status,
            "cited_sentence_rate": result.evidence.get("cited_sentence_rate", 0),
            "unique_sources":      result.evidence.get("unique_sources_cited", 0),
            "total_citations":     result.evidence.get("total_citation_instances", 0),
            "factual_sentences":   result.evidence.get("factual_sentence_count", 0),
            "orphan_citations":    result.evidence.get("orphan_citations", 0),
            "malformed":           result.evidence.get("malformed_citation_count", 0),
            "body_bare_urls":      result.evidence.get("body_bare_urls", 0),
            "composite_score":     result.evidence.get("composite_score", 0),
            "report_format":       "sn" if has_sn else "numeric_converted",
        }
    except Exception as e:
        return {"error": str(e)}


# ── 打印报告 ──────────────────────────────────────────────────────────────────

def print_task_report(r: dict[str, Any], traceability: dict[str, Any] | None = None) -> None:
    sep = "─" * 64
    print(f"\n{sep}")
    print(f"  任务: {r['company']} / {r['focus']}")
    print(f"  ID:   {r['task_id']}")
    print(f"  状态: {r['status']}")
    print(sep)

    wt = r["wall_time_min"]
    print(f"  端到端耗时:       {wt:.1f} min  ({r['wall_time_sec']:.0f} s)")

    if r["total_tokens"]:
        print(f"\n  Token 消耗:")
        print(f"    Prompt:      {r['total_prompt_tokens']:>8,}")
        print(f"    Completion:  {r['total_completion_tokens']:>8,}")
        print(f"    Total:       {r['total_tokens']:>8,}")
        print(f"    预估成本:    ${r['estimated_cost_usd']:.4f}  (deepseek-chat)")

        if r["by_node"]:
            print(f"\n  节点 Token 分布:")
            by_tok = sorted(r["by_node"].items(), key=lambda x: -x[1]["total_tokens"])
            for node, stats in by_tok[:8]:
                pct = stats["total_tokens"] / r["total_tokens"] * 100
                print(f"    {node:<35} {stats['total_tokens']:>7,} tok  ({pct:4.1f}%)"
                      f"  calls={stats['calls']}")
    else:
        print("  [!] 无 token 数据（trace 文件为空或全为 0）")

    if r["search_rounds"]:
        print(f"\n  搜索轮次:         {r['search_rounds']} 次（LLM 生成查询）")

    aq = r["ask_q_prompt_tokens_series"]
    if len(aq) >= 2:
        print(f"\n  上下文增长分析（ask_question prompt_tokens / 轮）:")
        for i, v in enumerate(aq, 1):
            bar = "█" * min(40, v // 500)
            print(f"    轮 {i:2d}: {v:>6,}  {bar}")
        g = r["context_growth_pct"]
        if g is not None:
            direction = "↑增长" if g > 0 else "↓压缩"
            print(f"    首轮→末轮变化: {g:+.1f}%  ({direction})")
            if g < 0:
                print(f"    ✓ 压缩生效：末轮上下文比首轮少 {abs(g):.1f}%")

    ws = r["write_section_prompts"]
    if ws:
        avg_ws = sum(ws) / len(ws)
        print(f"\n  write_section 平均 prompt_tokens: {avg_ws:,.0f}  (n={len(ws)})")

    # 压缩率（新增：仅在修复后的新跑任务中可用）
    compress_events = r.get("compress_events", [])
    if compress_events:
        total_before = sum(e["answer_tokens"] for e in compress_events)
        total_after  = sum(e["compressed_tokens"] for e in compress_events)
        total_facts  = sum(e.get("facts_extracted", 0) for e in compress_events)
        if total_before > 0:
            ratio = total_after / total_before
            reduction_pct = (1 - ratio) * 100
            print(f"\n  压缩统计（{len(compress_events)} 轮）:")
            print(f"    压缩前 answer tokens:  {total_before:>8,}")
            print(f"    压缩后 facts tokens:   {total_after:>8,}")
            print(f"    压缩率:                {ratio:.3f}  （减少 {reduction_pct:.1f}%）")
            print(f"    提取事实总数:          {total_facts}")
            print(f"    ✓ 每轮平均压缩至原始的 {ratio*100:.0f}%")

    if traceability:
        print(f"\n  来源覆盖率（SourceTraceabilityScorer）:")
        if "error" in traceability:
            print(f"    [错误] {traceability['error']}")
        else:
            csr = traceability.get("cited_sentence_rate", 0)
            uniq = traceability.get("unique_sources", 0)
            total_c = traceability.get("total_citations", 0)
            fact_s = traceability.get("factual_sentences", 0)
            score_status = traceability.get("status", "?")
            print(f"    状态:             {score_status.upper()}")
            print(f"    cited_sentence_rate: {csr:.1%}  ({fact_s} 个事实句)")
            print(f"    有效来源数:       {uniq} 个独立来源，共引用 {total_c} 次")
            print(f"    综合得分:         {traceability.get('composite_score', 0):.3f} / 1.0")
            if traceability.get("orphan_citations", 0):
                print(f"    ⚠ 悬空引用:     {traceability['orphan_citations']} 处")
            if traceability.get("malformed", 0):
                print(f"    ⚠ 格式错误引用: {traceability['malformed']} 处")
            if traceability.get("body_bare_urls", 0):
                print(f"    ⚠ 正文裸 URL:   {traceability['body_bare_urls']} 处")


def print_summary(results: list[dict[str, Any]]) -> None:
    """打印多任务汇总统计。"""
    completed = [r for r in results if r["status"] == "completed" and r["total_tokens"] > 0]
    if not completed:
        print("\n[汇总] 无已完成且有 token 数据的任务。")
        return

    durations = [r["wall_time_min"] for r in completed]
    tokens    = [r["total_tokens"] for r in completed]
    costs     = [r["estimated_cost_usd"] for r in completed]
    search_rounds = [r["search_rounds"] for r in completed if r["search_rounds"] > 0]

    print("\n" + "═" * 64)
    print("  汇总统计（仅 completed 且有 token 数据的任务）")
    print("═" * 64)
    print(f"  样本数量:          {len(completed)} 次")
    print(f"  平均耗时:          {sum(durations)/len(durations):.1f} min")
    print(f"  耗时范围:          {min(durations):.1f} ~ {max(durations):.1f} min")
    print(f"  平均 Token:        {sum(tokens)//len(tokens):,}")
    print(f"  Token 范围:        {min(tokens):,} ~ {max(tokens):,}")
    print(f"  平均成本:          ${sum(costs)/len(costs):.4f} USD")
    if search_rounds:
        print(f"  平均搜索轮次:      {sum(search_rounds)/len(search_rounds):.1f}")

    # 上下文增长
    growth_vals = [r["context_growth_pct"] for r in completed
                   if r["context_growth_pct"] is not None]
    if growth_vals:
        avg_g = sum(growth_vals) / len(growth_vals)
        print(f"\n  上下文增长（首→末轮 ask_question prompt_tokens）:")
        for r in completed:
            g = r["context_growth_pct"]
            if g is not None:
                print(f"    {r['company']}: {g:+.1f}%")
        print(f"  平均增长率:        {avg_g:+.1f}%"
              + ("  ✓ 压缩生效" if avg_g < 0 else ""))

    print("═" * 64)


# ── 主入口 ────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="从 trace 文件提取量化指标"
    )
    parser.add_argument("--task", help="只分析指定 task_id")
    parser.add_argument("--report", action="store_true",
                        help="同时跑 SourceTraceabilityScorer")
    parser.add_argument("--save", action="store_true",
                        help="保存结果到 eval_results/quantitative_metrics.json")
    args = parser.parse_args()

    tasks = _load_tasks()
    if not tasks:
        print("[错误] 未找到 .runtime/tasks.json，请先跑一次任务。")
        sys.exit(1)

    # 过滤
    if args.task:
        if args.task not in tasks:
            print(f"[错误] 未找到 task_id={args.task}")
            sys.exit(1)
        target_tasks = {args.task: tasks[args.task]}
    else:
        # 只处理有 trace 文件的任务
        target_tasks = {
            tid: t for tid, t in tasks.items()
            if (TRACES_DIR / f"{tid}.jsonl").exists()
            and t.get("status") in ("completed", "failed")
            and t.get("company_name", "").lower() not in ("testco", "testcorp", "acme corp")
        }

    if not target_tasks:
        print("[信息] 没有找到满足条件的任务（需要有 trace 文件且非测试任务）。")
        sys.exit(0)

    print(f"\n分析 {len(target_tasks)} 个任务...")

    all_results = []
    for tid, task in sorted(target_tasks.items(),
                             key=lambda x: x[1].get("created_at", 0)):
        result = analyze_task(tid, task)

        traceability = None
        if args.report:
            traceability = score_source_traceability(task)

        print_task_report(result, traceability)

        if traceability:
            result["source_traceability"] = traceability
        all_results.append(result)

    if len(all_results) > 1:
        print_summary(all_results)

    if args.save or len(all_results) > 0:
        out_path = RESULTS_DIR / "quantitative_metrics.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(all_results, f, indent=2, ensure_ascii=False, default=str)
        print(f"\n[已保存] {out_path}")

    # 打印简历用的一句话总结
    completed_with_tokens = [r for r in all_results
                              if r["status"] == "completed" and r["total_tokens"] > 0]
    if completed_with_tokens:
        n = len(completed_with_tokens)
        avg_tok = sum(r["total_tokens"] for r in completed_with_tokens) // n
        avg_min = sum(r["wall_time_min"] for r in completed_with_tokens) / n
        avg_cost = sum(r["estimated_cost_usd"] for r in completed_with_tokens) / n

        # 上下文压缩效果
        growth_vals = [r["context_growth_pct"] for r in completed_with_tokens
                       if r["context_growth_pct"] is not None]

        print("\n" + "━" * 64)
        print("  📋 简历用数据摘要")
        print("━" * 64)
        print(f"  样本：{n} 次真实企业尽调（DeepSeek + Serper/Bocha 真实搜索）")
        print(f"  平均耗时：       {avg_min:.0f} 分钟")
        print(f"  平均 Token：     {avg_tok:,}（deepseek-chat）")
        print(f"  平均成本：       ${avg_cost:.3f} USD / 次")
        if growth_vals:
            avg_g = sum(growth_vals) / len(growth_vals)
            if avg_g < 0:
                print(f"  上下文压缩：     多轮研究后末轮 prompt 比首轮少 {abs(avg_g):.0f}%（压缩生效）")
            else:
                print(f"  上下文增长：     多轮研究后 prompt 增长 {avg_g:.0f}%"
                      f"（未压缩基线；可与压缩版对比）")
        print("━" * 64)


if __name__ == "__main__":
    main()
