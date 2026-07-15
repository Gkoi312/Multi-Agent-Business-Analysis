"""
Real Evaluation Runner -- runs actual IncrementalCompressor + ToolPipeline with LLM.

Usage::
    cd backend
    python -m harness.evaluation.run_real_evals
    python -m harness.evaluation.run_real_evals --repeats 3
    python -m harness.evaluation.run_real_evals --state-file path/to/state.json

Environment variables:
    COMPRESSOR_LLM_PROVIDER / COMPRESSOR_LLM_MODEL
    JUDGE_LLM_PROVIDER / JUDGE_LLM_MODEL
"""

from __future__ import annotations

import json, logging, os, ssl, sys, time, uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

if os.getenv("SSL_NO_VERIFY") == "1":
    ssl._create_default_https_context = ssl._create_unverified_context
    os.environ["CURL_CA_BUNDLE"] = ""
    os.environ["REQUESTS_CA_BUNDLE"] = ""

OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "eval_results"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _save_json(data: Any, filename: str) -> Path:
    path = OUTPUT_DIR / filename
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    print(f"  [OK] Saved {path}")
    return path

def _save_markdown(text: str, filename: str) -> Path:
    path = OUTPUT_DIR / filename
    with open(path, "w", encoding="utf-8") as f: f.write(text)
    print(f"  [OK] Saved {path}")
    return path


def _load_llm(provider_env: str, model_env: str, fallback_provider_env: str, fallback_model_env: str) -> Any:
    from harness.llm_loader import ModelLoader
    provider = os.getenv(provider_env) or os.getenv(fallback_provider_env) or "openai"
    model_name = os.getenv(model_env) or os.getenv(fallback_model_env) or "gpt-4o-mini"
    old_provider = os.environ.get("LLM_PROVIDER"); old_model = os.environ.get("LLM_MODEL_NAME")
    os.environ["LLM_PROVIDER"] = provider; os.environ["LLM_MODEL_NAME"] = model_name
    try:
        return ModelLoader().load_llm()
    finally:
        if old_provider is not None: os.environ["LLM_PROVIDER"] = old_provider
        else: os.environ.pop("LLM_PROVIDER", None)
        if old_model is not None: os.environ["LLM_MODEL_NAME"] = old_model
        else: os.environ.pop("LLM_MODEL_NAME", None)


# ---------------------------------------------------------------------------
# Compression Fidelity
# ---------------------------------------------------------------------------
def run_compression_evals(compressor: Any, llm: Any, judge_llm: Any = None, repeats: int = 1) -> list[dict[str, Any]]:
    from harness.evaluation.fixtures import load_fixtures
    from harness.evaluation.scorers.compression_fidelity import CompressionFidelityScorer
    from harness.evaluation.scorer import SCORER_REGISTRY, ScoreResult

    scorer = CompressionFidelityScorer(llm=llm, judge_llm=judge_llm)
    SCORER_REGISTRY[scorer.dimension] = scorer
    fixtures = load_fixtures("compression")
    print(f"\n{'='*60}\nCOMPRESSION FIDELITY -- {len(fixtures)} fixtures x {repeats} repeats\n{'='*60}")
    all_runs: list[dict[str, Any]] = []

    for fix in fixtures:
        case_id = fix.get("case_id", "unknown")
        turn = fix.get("original_turn", {})
        question = turn.get("question", "")
        search_summary = turn.get("search_summary", "")
        answer = turn.get("answer", "")
        print(f"\n  [case] {case_id}")
        print(f"     Q: {question[:80]}...")
        print(f"     Labeled facts: {len(fix.get('labeled_facts', []))}, numbers: {len(fix.get('labeled_numbers', []))}")

        for r in range(repeats):
            run_id = f"{case_id}_r{r + 1}"
            started = time.perf_counter()
            try:
                compressed = compressor.compress_completed_turn(
                    question=question, answer=answer, search_summary=search_summary)
                # Pass FULL fixture (not just labeled_facts/labeled_numbers)
                score_result: ScoreResult = scorer.score(compressed_turn=compressed, fixture=fix)
                duration_ms = int((time.perf_counter() - started) * 1000); error = None
                fact_ret = score_result.evidence.get("fact_retention", 0)
                hallu = score_result.evidence.get("hallucination_rate", 0)
                num_ret = score_result.evidence.get("number_retention", 0)
                num_hallu = score_result.evidence.get("numeric_hallucination_rate", 0)
                jm = score_result.evidence.get("judge_method", "?"); fb = score_result.evidence.get("fallback_used", False)
                print(f"     [{run_id}] {score_result.status.upper()} | ret={fact_ret:.0%} hallu={hallu:.0%} "
                      f"num_ret={num_ret:.0%} num_hallu={num_hallu:.0%} | {jm}{'(fb)' if fb else ''} | {duration_ms}ms")
            except Exception as exc:
                duration_ms = int((time.perf_counter() - started) * 1000)
                error = f"{type(exc).__name__}: {exc}"
                score_result = ScoreResult(dimension="compression_fidelity", layer="component",
                                          value=0, max_value=2, normalized=0, status="fail",
                                          details=f"Exception: {exc}", issues=[str(exc)])
                print(f"     [{run_id}] ERROR: {error}")

            compressed_output: dict[str, Any] = {}
            try:
                compressed_output = {"question_intent": getattr(compressed, "question_intent", ""),
                    "fact_count": len(getattr(compressed, "facts", []) or []),
                    "numbers_count": len(getattr(compressed, "numbers_mentioned", []) or []),
                    "unanswered": getattr(compressed, "unanswered", []) or [],
                    "compression_error": getattr(compressed, "compression_error", "") or ""}
            except Exception: compressed_output = {"error": "Could not serialize"}
            all_runs.append({"run_id": run_id, "case_id": case_id,
                            "fixture_file": f"compression/{case_id}.json",
                            "score": score_result.to_dict(), "duration_ms": duration_ms,
                            "error": error, "compressed_output": compressed_output})
    return all_runs


# ---------------------------------------------------------------------------
# Pipeline Quality
# ---------------------------------------------------------------------------
def run_pipeline_evals(pipeline: Any, repeats: int = 1) -> list[dict[str, Any]]:
    from harness.evaluation.fixtures import load_fixtures
    from harness.evaluation.scorers.pipeline_quality import PipelineQualityScorer
    from harness.evaluation.scorer import SCORER_REGISTRY, ScoreResult

    fixtures = load_fixtures("pipeline")
    print(f"\n{'='*60}\nPIPELINE QUALITY -- {len(fixtures)} fixtures x {repeats} repeats\n{'='*60}")
    scorer = PipelineQualityScorer(pipeline=pipeline); SCORER_REGISTRY[scorer.dimension] = scorer
    all_runs: list[dict[str, Any]] = []

    for fix in fixtures:
        case_id = fix.get("case_id", "unknown")
        print(f"\n  [case] {case_id}")
        print(f"     Results: {len(fix.get('raw_results', []))} docs")
        for r in range(repeats):
            run_id = f"{case_id}_r{r + 1}"; started = time.perf_counter()
            try:
                score_result = scorer.score(fixture=fix)
                duration_ms = int((time.perf_counter() - started) * 1000); error = None
                dp = score_result.evidence.get("drop_precision", 0)
                dr = score_result.evidence.get("drop_recall", 0)
                fdr = score_result.evidence.get("false_drop_rate", 0)
                fdc = score_result.evidence.get("false_drop_count", 0)
                ekc = score_result.evidence.get("expected_kept_count", 0)
                ra = score_result.evidence.get("reason_accuracy")
                ra_str = f"{ra:.0%}" if ra is not None else "N/A"
                sac = score_result.evidence.get("stage_attribution_coverage", 0)
                print(f"     [{run_id}] {score_result.status.upper()} | drop_P={dp:.0%} drop_R={dr:.0%} "
                      f"FDR={fdr:.0%}({fdc}/{ekc}) reason_acc={ra_str} stage_cov={sac:.0%} | {duration_ms}ms")
            except Exception as exc:
                duration_ms = int((time.perf_counter() - started) * 1000)
                error = f"{type(exc).__name__}: {exc}"
                score_result = ScoreResult(dimension="pipeline_quality", layer="component",
                                          value=0, max_value=2, normalized=0, status="fail",
                                          details=f"Exception: {exc}", issues=[str(exc)])
                print(f"     [{run_id}] ERROR: {error}")
            all_runs.append({"run_id": run_id, "case_id": case_id,
                            "fixture_file": f"pipeline/{case_id}.json",
                            "score": score_result.to_dict(), "duration_ms": duration_ms, "error": error})

    print(f"\n  [metrics] Collecting pipeline StageTrace data...")
    _collect_pipeline_trace_metrics(pipeline, fixtures)
    return all_runs


def _collect_pipeline_trace_metrics(pipeline: Any, fixtures: list[dict[str, Any]]) -> None:
    from harness.evaluation.pipeline_metrics import aggregate_trace, format_metrics_table
    from harness.tools.pipeline import ToolContext
    from harness.tools.search.base import SearchDocument
    for fix in fixtures:
        case_id = fix.get("case_id", "unknown"); raw_results = fix.get("raw_results", [])
        docs = [SearchDocument(url=str(rd.get("url", "") or f"https://fixture.example/doc_{idx}"),
                title=str(rd.get("title", "") or ""),
                raw_content=str(rd.get("content", "") or rd.get("snippet", "") or ""),
                source_type=str(rd.get("source_type", "web") or "web"),
                metadata={"fixture_index": idx, "fixture_doc_id": f"fixture_{idx}"})
                for idx, rd in enumerate(raw_results)]
        ctx = ToolContext(target_entity=fix.get("target_entity", ""), target_focus="", source_type="web")
        cleaned, traces = pipeline.run_with_trace(docs, ctx)
        metrics = aggregate_trace(traces, pipeline_name=f"search_pipeline_{case_id}")
        table = format_metrics_table(metrics)
        print(f"\n  Pipeline trace for {case_id}: Input: {metrics.total_input_docs} -> Output: {metrics.total_output_docs}")
        _save_markdown(f"# Pipeline Trace -- {case_id}\n\nGenerated: {_now_iso()}\n\n{table}\n\n"
            + "\n".join(f"- **{s.stage}**: {s.input_count}->{s.output_count}" for s in metrics.per_stage),
            f"pipeline_trace_{case_id}.md")
        _save_json(metrics.to_dict(), f"pipeline_trace_{case_id}.json")


# ---------------------------------------------------------------------------
# Source Traceability
# ---------------------------------------------------------------------------
def run_source_traceability_evals() -> list[dict[str, Any]]:
    from harness.evaluation.scorers.source_traceability import SourceTraceabilityScorer
    from harness.evaluation.scorer import SCORER_REGISTRY
    scorer = SourceTraceabilityScorer(); SCORER_REGISTRY[scorer.dimension] = scorer
    print(f"\n{'='*60}\nSOURCE TRACEABILITY -- sanity checks\n{'='*60}")

    test_cases: list[dict[str, Any]] = [
        {"name": "well_cited", "expected_status": "pass", "expected_valid": True,
         "report": "## Analysis\n\nApple's AI strategy centers on Apple Intelligence [S1]. "
                   "The partnership with OpenAI enables Siri integration [S2]. "
                   "Revenue from Services reached $85B in 2025 [S3].\n\n"
                   "## Sources\n[1] https://techcrunch.com/apple-ai\n"
                   "[2] https://openai.com/blog/chatgpt-siri\n[3] https://apple.com/investor\n",
         "source_registry": {"S1": {}, "S2": {}, "S3": {}}},
        {"name": "no_citations", "expected_status": "fail", "expected_valid": False,
         "report": "Apple is a great company. It makes iPhones. Revenue is high."},
        {"name": "orphan_citations", "expected_status": "fail", "expected_valid": False,
         "report": "The company has strong AI [S1] and growing revenue [S99].",
         "source_registry": {"S1": {}}},
        {"name": "registry_gap_S1_S3_cites_S2", "expected_status": "fail", "expected_valid": False,
         "report": "The company announced [S2] new features.",
         "source_registry": {"S1": {}, "S3": {}}},
        {"name": "duplicate_citations", "expected_status": "pass", "expected_valid": True,
         "report": "Revenue was strong [S1]. Further analysis confirms [S1] the trend [S2].\n\n"
                   "## Sources\n[1] https://a.com\n[2] https://b.com",
         "source_registry": {"S1": {}, "S2": {}}},
        {"name": "body_bare_url", "expected_status": "fail", "expected_valid": False,
         "report": "Check https://example.com/news for details [S1].",
         "source_registry": {"S1": {}}},
        {"name": "url_only_in_references", "expected_status": "fail", "expected_valid": False,
         "report": "The company is performing well.\n\n## Sources\n[1] https://example.com/report"},
        {"name": "malformed_citation_Sx", "expected_status": "fail", "expected_valid": False,
         "report": "The report references [Sx] and [S1].",
         "source_registry": {"S1": {}}},
        {"name": "repeated_valid_citation", "expected_status": "pass", "expected_valid": True,
         "report": "Analysis [S1] shows growth. The same source [S1] confirms margins. "
                   "Another angle [S2] supports this.\n\n## Sources\n[1] https://a.com\n[2] https://b.com",
         "source_registry": {"S1": {}, "S2": {}}},
        {"name": "empty_report", "expected_status": "fail", "expected_valid": False, "report": ""},
    ]

    results: list[dict[str, Any]] = []
    for tc in test_cases:
        result = scorer.score(report_text=tc["report"], source_registry=tc.get("source_registry"))
        actual_status = result.status; expected_status = tc.get("expected_status", "pass")
        test_passed = actual_status == expected_status
        print(f"  [case] {tc['name']}: actual={actual_status.upper()} expected={expected_status.upper()} "
              f"| test={'PASS' if test_passed else 'FAIL'} "
              f"| citations={result.evidence.get('unique_sources_cited',0)} "
              f"| orphans={result.evidence.get('orphan_citations',0)} "
              f"| malformed={result.evidence.get('malformed_citation_count',0)} "
              f"| body_urls={result.evidence.get('body_bare_urls',0)}")
        if not test_passed:
            print(f"         MISMATCH: expected {expected_status}, got {actual_status}")
            print(f"         Reason: {result.evidence.get('status_reason','?')}")
        results.append({"test_case": tc["name"], "expected_status": expected_status,
                       "expected_valid": tc.get("expected_valid"), "actual_status": actual_status,
                       "test_passed": test_passed,
                       "mismatch_reason": result.evidence.get("status_reason","") if not test_passed else "",
                       "score": result.to_dict()})
    return results


# ---------------------------------------------------------------------------
# Consistency
# ---------------------------------------------------------------------------
def run_consistency_evals(state: dict[str, Any] | None = None,
                          state_file: str | None = None,
                          is_synthetic: bool = False) -> dict[str, Any]:
    from harness.evaluation.consistency import run_consistency_checks, CONSISTENCY_CHECKS
    print(f"\n{'='*60}\nCONSISTENCY CHECKS -- {len(CONSISTENCY_CHECKS)} rules\n{'='*60}")

    if state is None and state_file:
        with open(state_file, "r", encoding="utf-8") as f: state = json.load(f)
        print(f"  Loaded state from {state_file}")

    if state is None:
        print("  No state provided — generating synthetic state from fixtures")
        state = _build_synthetic_state()
        is_synthetic = True

    result = run_consistency_checks(state)
    print(f"  Result: {'PASS' if result.passed else 'FAIL'} "
          f"({result.passed_rules}/{result.total_rules} passed, "
          f"{result.failed_rules} failed, {result.warned_rules} warned)")
    for v in result.violations:
        print(f"    [{'ERROR' if v['severity']=='error' else 'WARN'}] {v['rule']}: {v['detail']}")

    return {"generated_at": _now_iso(), "check_count": result.total_rules,
            "passed": result.passed, "evaluated_real_state": not is_synthetic,
            "dimension_name": "consistency_sanity_check" if is_synthetic else "state_consistency",
            "result": result.to_dict()}


def _build_synthetic_state() -> dict[str, Any]:
    from harness.evaluation.fixtures import load_fixtures
    state: dict[str, Any] = {"working_memory": {"turns_completed": 0, "facts": [], "knowledge_gaps": []},
        "compressed_turns": [], "source_registry": {}, "llm_metrics": [],
        "workflow_events": [], "knowledge_gap_history": []}
    try:
        comp_fixtures = load_fixtures("compression")
        state["compressed_turns"] = [{"facts": [{"text": f} for f in fix.get("labeled_facts", [])]} for fix in comp_fixtures]
        state["working_memory"]["turns_completed"] = len(comp_fixtures)
        all_facts = [{"fact_id": f"f_{i}_{j}", "text": ft, "source_ids": [f"S{i+1}"]}
                     for i, fix in enumerate(comp_fixtures)
                     for j, ft in enumerate(fix.get("labeled_facts", []))]
        state["working_memory"]["facts"] = all_facts
        state["source_registry"] = {f"S{i+1}": {"source_id": f"S{i+1}", "url": f"https://fixture.example/{i}"}
                                    for i in range(len(comp_fixtures))}
        state["knowledge_gap_history"] = [{"turn": 1, "count": 8}, {"turn": 2, "count": 6}, {"turn": 3, "count": 5}]
    except Exception as e: logger.warning(f"Could not populate synthetic state: {e}")
    return state


# ---------------------------------------------------------------------------
# Reliability
# ---------------------------------------------------------------------------
def generate_reliability_report(compression_runs: list[dict[str, Any]],
                                pipeline_runs: list[dict[str, Any]]) -> str:
    from harness.evaluation.runner import EvalRunResult
    from harness.evaluation.scorer import ScoreResult
    from harness.evaluation.reliability import ReliabilityReport

    all_runs: list[EvalRunResult] = []
    for rd in compression_runs + pipeline_runs:
        sr = ScoreResult.from_dict(rd.get("score", {}))
        all_runs.append(EvalRunResult(run_id=rd.get("run_id", str(uuid.uuid4())[:8]),
                         case_id=rd.get("case_id", "unknown"), scores=[sr],
                         duration_ms=rd.get("duration_ms", 0), error=rd.get("error")))

    scorer_types = {"compression_fidelity": "llm_judged", "pipeline_quality": "deterministic",
                    "source_traceability": "deterministic", "state_consistency": "deterministic"}
    report = ReliabilityReport.from_runs(all_runs, scorer_types=scorer_types)
    return report.format_markdown(title="AI Harness Evaluation Reliability Report -- Real LLM Runs")


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
def build_summary(compression_runs: list[dict[str, Any]],
                  pipeline_runs: list[dict[str, Any]],
                  traceability_results: list[dict[str, Any]],
                  consistency_result: dict[str, Any] | None,
                  reliability_report_obj: Any = None,  # ReliabilityReport instance
                  evaluation_version: str = "2.1.0") -> dict[str, Any]:
    def _micro_mean(runs: list[dict[str, Any]]) -> float:
        scores = [r["score"]["normalized"] for r in runs
                  if r.get("score",{}).get("eligible",True) and r.get("score",{}).get("status")!="skipped"]
        return round(sum(scores)/len(scores), 4) if scores else 0.0

    def _macro_mean(runs: list[dict[str, Any]]) -> float:
        by_case: dict[str, list[float]] = {}
        for r in runs:
            if r.get("score",{}).get("eligible",True) and r.get("score",{}).get("status")!="skipped":
                cid = r.get("case_id","unknown"); by_case.setdefault(cid,[]).append(r["score"]["normalized"])
        case_means = [sum(v)/len(v) for v in by_case.values() if v]
        return round(sum(case_means)/len(case_means), 4) if case_means else 0.0

    def _pass_rate(runs: list[dict[str, Any]]) -> float:
        eligible = [r for r in runs if r.get("score",{}).get("eligible",True) and r.get("score",{}).get("status")!="skipped"]
        if not eligible: return 0.0
        return round(sum(1 for r in eligible if r["score"]["status"]=="pass")/len(eligible), 4)

    def _count_status(runs: list[dict[str, Any]], status: str) -> int:
        return sum(1 for r in runs if r.get("score",{}).get("status")==status)

    def _dim_limitations(runs: list[dict[str, Any]]) -> list[str]:
        lims: list[str] = []
        for r in runs:
            ev = r.get("score",{}).get("evidence",{})
            if ev.get("same_model_self_judge"): lims.append("Same-model self-judging"); break
            if ev.get("fallback_used"): lims.append("Heuristic fallback used"); break
        return lims

    # Populate from ReliabilityReport if available
    rel_data: dict[str, Any] = {}
    if reliability_report_obj is not None:
        for ds in reliability_report_obj.dimension_stats:
            rel_data[ds.dimension] = {
                "macro_mean": ds.macro_mean, "micro_mean": ds.micro_mean,
                "stable_case_rate": ds.stable_case_rate,
                "within_case_std": ds.within_case_std,
                "between_case_std": ds.between_case_std,
                "repeatability_status": ds.repeatability_status,
                "repeatability_reason": ds.repeatability_reason,
                "performance_status": ds.performance_status,
            }

    comp_cases = len(set(r.get("case_id","") for r in compression_runs))
    comp_total = len(compression_runs)
    comp_eligible = comp_total - _count_status(compression_runs, "skipped")
    comp_skipped = _count_status(compression_runs, "skipped")
    comp_rel = rel_data.get("compression_fidelity", {})

    pipe_cases = len(set(r.get("case_id","") for r in pipeline_runs))
    pipe_total = len(pipeline_runs)
    pipe_eligible = pipe_total - _count_status(pipeline_runs, "skipped")
    pipe_skipped = _count_status(pipeline_runs, "skipped")
    pipe_rel = rel_data.get("pipeline_quality", {})

    trace_test_cases = len(traceability_results)
    trace_correct = sum(1 for r in traceability_results if r.get("test_passed", False))
    trace_acc = round(trace_correct/trace_test_cases, 4) if trace_test_cases else 0.0

    summary: dict[str, Any] = {
        "generated_at": _now_iso(), "evaluation_version": evaluation_version,
        "total_cases": comp_cases + pipe_cases,
        "total_runs": comp_total + pipe_total,
        "eligible_runs": comp_eligible + pipe_eligible,
        "skipped_runs": comp_skipped + pipe_skipped,
        "by_dimension": {
            "compression_fidelity": {
                "cases": comp_cases, "runs": comp_total,
                "eligible_runs": comp_eligible, "skipped_runs": comp_skipped,
                "macro_mean": comp_rel.get("macro_mean", _macro_mean(compression_runs)),
                "micro_mean": comp_rel.get("micro_mean", _micro_mean(compression_runs)),
                "pass_rate": _pass_rate(compression_runs),
                "stable_case_rate": comp_rel.get("stable_case_rate"),
                "within_case_std": comp_rel.get("within_case_std"),
                "between_case_std": comp_rel.get("between_case_std"),
                "repeatability_status": comp_rel.get("repeatability_status"),
                "performance_status": comp_rel.get("performance_status"),
                "limitations": _dim_limitations(compression_runs),
            },
            "pipeline_quality": {
                "cases": pipe_cases, "runs": pipe_total,
                "eligible_runs": pipe_eligible, "skipped_runs": pipe_skipped,
                "macro_mean": pipe_rel.get("macro_mean", _macro_mean(pipeline_runs)),
                "micro_mean": pipe_rel.get("micro_mean", _micro_mean(pipeline_runs)),
                "pass_rate": _pass_rate(pipeline_runs),
                "stable_case_rate": pipe_rel.get("stable_case_rate"),
                "within_case_std": pipe_rel.get("within_case_std"),
                "between_case_std": pipe_rel.get("between_case_std"),
                "repeatability_status": pipe_rel.get("repeatability_status"),
                "performance_status": pipe_rel.get("performance_status"),
                "limitations": _dim_limitations(pipeline_runs),
            },
            "source_traceability": {
                "test_cases": trace_test_cases,
                "classification_accuracy": trace_acc,
                "all_tests_passed": all(r.get("test_passed",False) for r in traceability_results),
                "correct_classifications": trace_correct,
            },
            "state_consistency": consistency_result or {},
        },
    }
    return summary


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(repeats: int = 1, state_file: str | None = None) -> None:
    print("=" * 60); print("AI Harness -- Real Evaluation Runner")
    print(f"Time: {_now_iso()}"); print(f"Repeats: {repeats}"); print("=" * 60)

    print("\n[setup] Loading LLMs...")
    compressor_llm = _load_llm("COMPRESSOR_LLM_PROVIDER", "COMPRESSOR_LLM_MODEL", "LLM_PROVIDER", "LLM_MODEL_NAME")
    comp_provider = os.getenv("COMPRESSOR_LLM_PROVIDER") or os.getenv("LLM_PROVIDER", "openai")
    comp_model = os.getenv("COMPRESSOR_LLM_MODEL") or os.getenv("LLM_MODEL_NAME", "gpt-4o-mini")
    print(f"   Compressor: {comp_provider}/{comp_model}")

    if os.getenv("JUDGE_LLM_MODEL"):
        judge_llm = _load_llm("JUDGE_LLM_PROVIDER", "JUDGE_LLM_MODEL", "COMPRESSOR_LLM_PROVIDER", "COMPRESSOR_LLM_MODEL")
        print(f"   Judge:      {os.getenv('JUDGE_LLM_PROVIDER') or comp_provider}/{os.getenv('JUDGE_LLM_MODEL')}")
    else:
        judge_llm = compressor_llm
        print(f"   Judge:      SAME as compressor ({comp_provider}/{comp_model})")
        print(f"   !! WARNING: Same-model self-judging may inflate scores. Set JUDGE_LLM_MODEL.")

    print("\n[setup] Building IncrementalCompressor...")
    from harness.memory.compressor import IncrementalCompressor
    from harness.memory.context_window import ContextWindowManager
    from domains.due_diligence.memory_config import DUE_DILIGENCE_MEMORY_CONFIG
    window_mgr = ContextWindowManager(model_name="deepseek-chat")
    compressor = IncrementalCompressor(llm=compressor_llm, window_manager=window_mgr, domain_config=DUE_DILIGENCE_MEMORY_CONFIG)
    print(f"   Compressor ready: categories={compressor.categories_str}")

    print("\n[setup] Building ToolPipeline...")
    from harness.tools.pipeline import ToolPipeline
    from harness.tools.search.cleaner import SEARCH_PIPELINE_FULL
    pipeline = ToolPipeline(SEARCH_PIPELINE_FULL)
    print(f"   Pipeline ready: {len(SEARCH_PIPELINE_FULL)} stages")

    compression_runs = run_compression_evals(compressor, compressor_llm, judge_llm=judge_llm, repeats=repeats)
    pipeline_runs = run_pipeline_evals(pipeline, repeats=repeats)
    traceability_results = run_source_traceability_evals()

    # Consistency: pass state_file from CLI, mark synthetic vs real
    consistency_result = run_consistency_evals(state_file=state_file)

    print(f"\n{'='*60}\nRELIABILITY REPORT\n{'='*60}")
    reliability_md = generate_reliability_report(compression_runs, pipeline_runs)
    print(reliability_md)

    # Build ReliabilityReport object for summary
    from harness.evaluation.runner import EvalRunResult as ERR
    from harness.evaluation.scorer import ScoreResult as SR
    from harness.evaluation.reliability import ReliabilityReport
    all_eruns = []
    for rd in compression_runs + pipeline_runs:
        sr = SR.from_dict(rd.get("score", {}))
        all_eruns.append(ERR(run_id=rd.get("run_id", str(uuid.uuid4())[:8]),
                            case_id=rd.get("case_id", "unknown"), scores=[sr],
                            duration_ms=rd.get("duration_ms", 0), error=rd.get("error")))
    rel_obj = ReliabilityReport.from_runs(all_eruns, scorer_types={
        "compression_fidelity": "llm_judged", "pipeline_quality": "deterministic"})

    print(f"\n{'='*60}\nSAVING RESULTS -> {OUTPUT_DIR}\n{'='*60}")
    _save_json(compression_runs, "compression_results.json")
    _save_json(pipeline_runs, "pipeline_results.json")
    _save_json(traceability_results, "source_traceability_results.json")
    _save_json(consistency_result, "consistency_results.json")
    _save_markdown(reliability_md, "reliability_report.md")

    summary = build_summary(compression_runs, pipeline_runs, traceability_results,
                           consistency_result, rel_obj, evaluation_version="2.1.0")
    _save_json(summary, "summary.json")

    print(f"\n{'='*60}\n[DONE] ALL EVALUATIONS COMPLETE\n{'='*60}")
    print(f"Compression: {len(compression_runs)} runs, macro={summary['by_dimension']['compression_fidelity']['macro_mean']:.3f}, micro={summary['by_dimension']['compression_fidelity']['micro_mean']:.3f}")
    print(f"Pipeline: {len(pipeline_runs)} runs")
    print(f"Traceability: {summary['by_dimension']['source_traceability']['correct_classifications']}/{summary['by_dimension']['source_traceability']['test_cases']} tests correct")
    if consistency_result:
        print(f"Consistency: {'PASS' if consistency_result.get('passed') else 'FAIL'} (real_state={consistency_result.get('evaluated_real_state')})")
    print(f"Results saved to: {OUTPUT_DIR}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run real evaluations on IncrementalCompressor and ToolPipeline")
    parser.add_argument("--repeats", type=int, default=1, help="Number of repeats per case (default: 1)")
    parser.add_argument("--state-file", type=str, default=None, help="Path to JSON state file for consistency checks")
    args = parser.parse_args()
    main(repeats=args.repeats, state_file=args.state_file)
