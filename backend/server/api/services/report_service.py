import uuid
import os
import re
import time
from typing import Any
from fastapi.responses import FileResponse
from harness.llm_loader import ModelLoader
from domains.due_diligence.graph import AutonomousReportGenerator
from harness.observability.logger import GLOBAL_LOGGER
from harness.exceptions import ResearchAnalystException
from server.config import GENERATED_REPORT_DIR, RUNTIME_DIR


def _create_checkpointer():
    """Create a persistent SqliteSaver-backed checkpointer.

    Falls back to MemorySaver if langgraph-checkpoint-sqlite is not installed.
    """
    db_path = os.fspath(RUNTIME_DIR / "checkpoints.db")
    os.makedirs(os.fspath(RUNTIME_DIR), exist_ok=True)
    try:
        import sqlite3
        from langgraph.checkpoint.sqlite import SqliteSaver

        GLOBAL_LOGGER.info("Using SqliteSaver for persistent checkpoints", db_path=db_path)
        conn = sqlite3.connect(db_path, check_same_thread=False)
        return SqliteSaver(conn)
    except ImportError:
        from langgraph.checkpoint.memory import MemorySaver
        GLOBAL_LOGGER.warning(
            "langgraph-checkpoint-sqlite not installed; "
            "using MemorySaver (checkpoints will not survive restarts)"
        )
        return MemorySaver()


_shared_checkpointer = _create_checkpointer()


def _feed_metrics(task_id: str, state_values: dict[str, Any]) -> None:
    """Extract llm_metrics from graph state and feed into MetricsCollector + NodeTracer."""
    from harness.observability.metrics import get_ledger
    from harness.observability.tracer import get_tracer

    llm_metrics: list[dict] = state_values.get("llm_metrics", []) or []
    if not llm_metrics:
        return

    ledger = get_ledger(task_id)
    tracer = get_tracer(task_id)

    for m in llm_metrics:
        if not isinstance(m, dict):
            continue
        node = str(m.get("node", "") or "unknown")
        model = str(m.get("model", ""))
        prompt_tokens = int(m.get("prompt_tokens", 0) or 0)
        completion_tokens = int(m.get("completion_tokens", 0) or 0)
        total_tokens = int(m.get("total_tokens", 0) or 0) or (prompt_tokens + completion_tokens)
        latency_ms = int(m.get("latency_ms", 0) or 0)

        # Feed MetricsCollector (cost/token tracking)
        ledger.record(
            node=node,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
        )

        # Feed NodeTracer (per-node execution traces)
        with tracer.trace(node) as span:
            span.record_llm_call(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                latency_ms=latency_ms,
                model=model,
            )


class ReportService:
    def __init__(self):
        self.llm = ModelLoader().load_llm()
        self.reporter = AutonomousReportGenerator(self.llm)
        self.reporter.memory = _shared_checkpointer
        self.graph = self.reporter.build_graph()
        self.logger = GLOBAL_LOGGER.bind(module="ReportService")

    @staticmethod
    def _extract_risk_counts(final_report: str) -> dict[str, int]:
        text = final_report or ""
        risk_section_match = re.search(
            r"##\s*(?:Risk Assessment|风险评估)\s*(.*?)(?:\n##\s|\Z)",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        scope = risk_section_match.group(1) if risk_section_match else text
        high_count = len(
            re.findall(
                r"(?:Risk level|风险等级|等级)\s*[:：]\s*(?:High|高)",
                scope,
                re.IGNORECASE,
            )
        )
        medium_count = len(
            re.findall(
                r"(?:Risk level|风险等级|等级)\s*[:：]\s*(?:Medium|中)",
                scope,
                re.IGNORECASE,
            )
        )
        low_count = len(
            re.findall(
                r"(?:Risk level|风险等级|等级)\s*[:：]\s*(?:Low|低)",
                scope,
                re.IGNORECASE,
            )
        )
        if high_count or medium_count or low_count:
            return {
                "high": high_count,
                "medium": medium_count,
                "low": low_count,
            }
        return {
            "high": len(re.findall(r"\bhigh\b", scope, flags=re.IGNORECASE)),
            "medium": len(re.findall(r"\bmedium\b", scope, flags=re.IGNORECASE)),
            "low": len(re.findall(r"\blow\b", scope, flags=re.IGNORECASE)),
        }

    @staticmethod
    def _extract_final_recommendation(final_report: str) -> str:
        if not final_report:
            return ""
        match = re.search(
            r"##\s*(?:Final Recommendations|最终建议|Final Recommendation)\s*(.*?)(?:\n##\s|\Z)",
            final_report,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return ""
        # preserve line breaks — only collapse multiple spaces within lines,
        # strip each line, and remove blank lines
        lines = [re.sub(r"\s+", " ", line).strip() for line in match.group(1).split("\n")]
        section = "\n".join(line for line in lines if line)
        return section[:600]

    @staticmethod
    def _extract_analysts_preview(state_values: dict) -> list[dict]:
        analysts = state_values.get("analysts", []) or []
        preview = []
        for a in analysts:
            preview.append(
                {
                    "name": getattr(a, "name", ""),
                    "role": getattr(a, "role", ""),
                    "affiliation": getattr(a, "affiliation", ""),
                    "description": getattr(a, "description", ""),
                }
            )
        return preview

    def start_report_generation(
        self,
        research_query: str,
        max_analysts: int,
        company_name: str,
        focus: str = "",
        target_role: str = "",
        task_id: str = "",
        thread_id: str = "",
    ):
        """Trigger the autonomous report pipeline."""
        try:
            thread_id = thread_id or str(uuid.uuid4())
            thread = {"configurable": {"thread_id": thread_id}}
            self.logger.info(
                "Starting report pipeline",
                research_query=research_query,
                company_name=company_name,
                thread_id=thread_id,
                task_id=task_id,
            )

            overall_started = time.perf_counter()

            for _ in self.graph.stream(
                {
                    "research_query": research_query,
                    "company_name": company_name,
                    "focus": focus,
                    "target_role": target_role,
                    "max_analysts": max_analysts,
                    "max_num_turns": 3,
                    "planner_enabled": True,
                    "review_enabled": True,
                    "company_type": "unknown",
                    "skill_bundle": [],
                    "research_skills": [],
                    "skill_mapping": {},
                    "source_policy_map": {},
                    "domain_memory": [],
                    "router_decisions": [],
                    "review_notes": [],
                    "workflow_events": [],
                    "llm_metrics": [],
                },
                thread,
                stream_mode="values",
            ):
                pass

            overall_elapsed_ms = int((time.perf_counter() - overall_started) * 1000)
            state = self.graph.get_state(thread)
            analysts_preview = self._extract_analysts_preview(state.values)

            # Feed metrics from final state into the harness ledger/tracer
            if task_id:
                _feed_metrics(task_id, state.values)
                # Record overall execution as a tracer entry
                from harness.observability.tracer import get_tracer
                tracer = get_tracer(task_id)
                with tracer.trace("_total_generation") as span:
                    span.set_output(f"elapsed_ms={overall_elapsed_ms}, analysts={len(analysts_preview)}")
                    span.tag(overall_elapsed_ms=overall_elapsed_ms)

            return {
                "thread_id": thread_id,
                "analysts_preview": analysts_preview,
            }
        except Exception as e:
            self.logger.error("Error initiating report generation", error=str(e))
            raise ResearchAnalystException("Failed to start report generation", e)

    def submit_feedback(self, thread_id: str, feedback: str, task_id: str = ""):
        """Update human feedback in graph state."""
        try:
            thread = {"configurable": {"thread_id": thread_id}}
            self.graph.update_state(thread, {"human_analyst_feedback": feedback}, as_node="human_feedback")
            self.logger.info("Feedback updated", thread_id=thread_id)
            overall_started = time.perf_counter()
            for _ in self.graph.stream(None, thread, stream_mode="values"):
                pass
            overall_elapsed_ms = int((time.perf_counter() - overall_started) * 1000)
            state = self.graph.get_state(thread)
            pending_nodes = list(getattr(state, "next", []) or [])
            analysts_preview = self._extract_analysts_preview(state.values)
            awaiting_feedback = "human_feedback" in pending_nodes

            # Feed metrics
            if task_id:
                _feed_metrics(task_id, state.values)
                from harness.observability.tracer import get_tracer
                tracer = get_tracer(task_id)
                with tracer.trace("_total_feedback") as span:
                    span.set_output(f"elapsed_ms={overall_elapsed_ms}, awaiting_feedback={awaiting_feedback}")
                    span.tag(overall_elapsed_ms=overall_elapsed_ms)

            return {
                "feedback_elapsed_ms": overall_elapsed_ms,
                "awaiting_feedback": awaiting_feedback,
                "analysts_preview": analysts_preview,
            }
        except Exception as e:
            self.logger.error("Error updating feedback", error=str(e))
            raise ResearchAnalystException("Failed to update feedback", e)

    def get_report_status(self, thread_id: str, task_id: str = ""):
        """Fetch latest state or final report."""
        try:
            thread = {"configurable": {"thread_id": thread_id}}
            state = self.graph.get_state(thread)
            final_report = state.values.get("final_report")
            company_name = state.values.get("company_name", "")
            report_name = company_name or "Company_Due_Diligence"

            if final_report:
                file_docx = self.reporter.save_report(final_report, report_name, "docx")
                file_pdf = self.reporter.save_report(final_report, report_name, "pdf")
                risk_counts = self._extract_risk_counts(final_report)
                final_recommendation = self._extract_final_recommendation(final_report)
                review = state.values.get("report_review")
                review_status = ""
                review_summary = ""
                if review:
                    review_status = getattr(review, "status", "")
                    review_summary = getattr(review, "summary", "")

                # Feed metrics one final time (includes write_report, review, finalize nodes)
                if task_id:
                    _feed_metrics(task_id, state.values)

                return {
                    "status": "completed",
                    "docx_path": file_docx,
                    "pdf_path": file_pdf,
                    "risk_summary": risk_counts,
                    "final_recommendation": final_recommendation,
                    "report_review_status": review_status,
                    "report_review_summary": review_summary,
                }
            return {
                "status": "in_progress",
            }
        except Exception as e:
            self.logger.error("Error fetching report status", error=str(e))
            raise ResearchAnalystException("Failed to fetch report status", e)

    @staticmethod
    def download_file(file_name: str):
        """Download generated report."""
        report_dir = os.fspath(GENERATED_REPORT_DIR)
        for root, _, files in os.walk(report_dir):
            if file_name in files:
                return FileResponse(
                    path=os.path.join(root, file_name),
                    filename=file_name,
                    media_type="application/octet-stream"
                )
        return {"error": f"File not found: {file_name}"}
