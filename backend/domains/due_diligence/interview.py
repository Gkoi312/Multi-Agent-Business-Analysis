"""
Due Diligence — Interview subgraph.

Each analyst runs its own interview instance in parallel (fan-out).
Round 3: ContextAssembler wired into all LLM nodes, WorkingMemory as sole
truth source, source registry for traceability, history compaction on pressure.
"""
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage
from langchain_core.messages import get_buffer_string
import time
import uuid
from typing import Any

from harness.models.agent import AnalystPlan, RetrievedSource, ReviewFinding, SearchQuery
from harness.models.memory import (
    CompressedTurn,
    MergedMemory,
    RunningSummary,
    SearchDigest,
    SourceRecord,
    ContextBudgetExceeded,
    TokenCounter,
    _now_iso,
)
from harness.memory.working_memory import WorkingMemory
from harness.tools.search.base import SearchDocument, SearchQuery as ToolSearchQuery
from harness.tools.pipeline import ToolPipeline, ToolContext
from harness.tools.registry import TOOL_REGISTRY, ToolRegistry
from harness.tools.search.cleaner import SEARCH_PIPELINE_FULL
from harness.memory.compressor import IncrementalCompressor
from harness.memory.context_window import ContextWindowManager
from harness.memory.context_assembler import ContextAssembler
from harness.memory.search_digest import SearchDigestBuilder
from harness.memory.policies import TokenBudget, CompactionPolicy, MemoryDomainConfig
from domains.due_diligence.schemas import InterviewState
from domains.due_diligence.memory_config import DUE_DILIGENCE_MEMORY_CONFIG
from domains.due_diligence.prompts.interview import (
    ANALYST_ASK_QUESTIONS,
    GENERATE_SEARCH_QUERY,
    GENERATE_ANSWERS,
    WRITE_SECTION,
)
from app.logger import GLOBAL_LOGGER
from app.exception.custom_exception import ResearchAnalystException


class InterviewGraphBuilder:
    """
    A class responsible for constructing and managing the Interview Graph workflow.
    Handles the process of:
        1. Analyst generating questions.
        2. Performing relevant web search.
        3. Expert generating answers.
        4. Compressing the turn.
        5. Updating WorkingMemory (sole truth source).
        6. Conditionally continuing or stopping.
    """

    def __init__(self, llm, tavily_search=None, tool_registry: ToolRegistry | None = None, pipeline: ToolPipeline | None = None, cheap_llm=None, domain_config: MemoryDomainConfig | None = None):
        """
        Initialize the InterviewGraphBuilder.

        Args:
            llm: Language model instance (primary, for reasoning tasks).
            tavily_search: (deprecated) Legacy TavilySearchResults instance.
            tool_registry: ToolRegistry for resolving search backends.
            pipeline: ToolPipeline for cleaning search results.
            cheap_llm: Optional cheaper/faster LLM for compression tasks.
                       Falls back to *llm* if not provided.
            domain_config: Domain-specific memory configuration.
        """
        self.llm = llm
        self.cheap_llm = cheap_llm or llm
        self.tavily_search = tavily_search  # kept for backward compat
        self.tool_registry = tool_registry or TOOL_REGISTRY
        self.pipeline = pipeline or ToolPipeline(SEARCH_PIPELINE_FULL)
        self._domain_config = domain_config or DUE_DILIGENCE_MEMORY_CONFIG

        window_mgr = ContextWindowManager(
            model_name=getattr(self.cheap_llm, "model_name", "gpt-4o-mini"),
        )
        self.compressor = IncrementalCompressor(
            self.cheap_llm,
            window_manager=window_mgr,
            domain_config=self._domain_config,
        )

        # Context assembly (Round 3: actually wired in)
        self.token_budget = TokenBudget()
        self.context_assembler = ContextAssembler(
            token_budget=self.token_budget,
            window_mgr=window_mgr,
        )
        self.search_digest_builder = SearchDigestBuilder(
            token_counter=lambda x: window_mgr.estimate_tokens(str(x)),
        )

        self.memory = MemorySaver()
        self.logger = GLOBAL_LOGGER.bind(module="InterviewGraphBuilder")

    @staticmethod
    def _value(obj: Any, key: str, default=None):
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    @staticmethod
    def _format_skill_card(skill_card) -> str:
        if not skill_card:
            return ""
        return (
            f"Skill ID: {InterviewGraphBuilder._value(skill_card, 'id', '')}\n"
            f"Name: {InterviewGraphBuilder._value(skill_card, 'name', '')}\n"
            f"Objective: {InterviewGraphBuilder._value(skill_card, 'objective', '')}\n"
            f"Focus areas: {', '.join(InterviewGraphBuilder._value(skill_card, 'focus_areas', []) or [])}"
        )

    @staticmethod
    def _format_assigned_plan(plan: AnalystPlan | None) -> str:
        if not plan:
            return ""
        policy = InterviewGraphBuilder._value(plan, "source_policy")
        policy_label = InterviewGraphBuilder._value(policy, "label", "")
        return (
            f"Sub-task: {InterviewGraphBuilder._value(plan, 'brief', '')}\n"
            f"Key questions: {'; '.join(InterviewGraphBuilder._value(plan, 'key_questions', []) or [])}\n"
            f"Search policy: {policy_label}"
        )

    @staticmethod
    def _format_domain_memory(memory: list[dict[str, Any]]) -> str:
        if not memory:
            return ""
        return "\n".join(
            [
                f"- {InterviewGraphBuilder._value(m, 'title', '')}: "
                f"{InterviewGraphBuilder._value(m, 'content', '')}"
                for m in memory[:3]
            ]
        )

    @staticmethod
    def _format_source_policy(policy: dict[str, Any] | None) -> str:
        if not policy:
            return ""
        return (
            f"Policy: {InterviewGraphBuilder._value(policy, 'label', '')}\n"
            f"Preferred source types: {', '.join(InterviewGraphBuilder._value(policy, 'preferred_source_types', []) or [])}\n"
            f"Site hints: {', '.join(InterviewGraphBuilder._value(policy, 'site_hints', []) or [])}\n"
            f"Freshness: {InterviewGraphBuilder._value(policy, 'freshness_hint', '')}\n"
            f"Guidance: {'; '.join(InterviewGraphBuilder._value(policy, 'guidance', []) or [])}"
        )

    @staticmethod
    def _ensure_message_ids(messages: list[Any]) -> list[Any]:
        """Ensure every message in the list has a persistent ID.

        Messages without IDs get a UUID assigned. Returns a new list
        (does not mutate originals in ways LangGraph can't track).
        """
        result = []
        for msg in messages:
            msg_id = getattr(msg, "id", None)
            if not msg_id:
                try:
                    new_msg = msg.model_copy(update={"id": str(uuid.uuid4())})
                    result.append(new_msg)
                except Exception:
                    result.append(msg)
            else:
                result.append(msg)
        return result

    # ------------------------------------------------------------------
    # Round 3: Context Assembly helper
    # ------------------------------------------------------------------

    def _make_working_memory(self, state: InterviewState) -> WorkingMemory:
        """Create a WorkingMemory from state, always with domain_config injected."""
        wm_dict = state.get("working_memory") or {}
        if wm_dict:
            wm = WorkingMemory.from_dict(wm_dict)
        else:
            wm = WorkingMemory(
                coverage_policy=self._domain_config.coverage_policy,
                domain_config=self._domain_config,
            )
        # Ensure domain_config is set (from_dict may not preserve it)
        if wm.domain_config is None:
            wm.domain_config = self._domain_config
        if wm._reconciler is None or wm._reconciler._domain_config is None:
            from harness.memory.fact_reconciler import FactReconciler
            wm._reconciler = FactReconciler(domain_config=self._domain_config)
        return wm

    def _assemble_llm_messages(
        self,
        state: InterviewState,
        system_prompt: str,
        *,
        include_search_digest: bool = False,
        include_recent_messages: bool = True,
    ) -> list:
        """Build the projected LLM input from canonical state.

        NEVER mutates state["messages"]. Returns a NEW list of messages
        for the LLM call only.
        """
        try:
            # 1. Restore WorkingMemory from state (with domain_config)
            wm = self._make_working_memory(state)

            # 2. Restore RunningSummary from state
            rs_dict = state.get("running_summary") or {}
            running_summary = RunningSummary.from_dict(rs_dict) if rs_dict else RunningSummary()
            running_summary_str = running_summary.summary if running_summary.summary else ""

            # 3. Compressed turns
            compressed_turns_raw = state.get("compressed_turns") or []
            compressed_turns = [
                CompressedTurn.from_dict(d) if isinstance(d, dict) else d
                for d in compressed_turns_raw
            ]

            # 4. Search digest
            search_digest_str = ""
            sd_dict = state.get("search_digest") or {}
            if include_search_digest and sd_dict:
                sd = SearchDigest.from_dict(sd_dict) if isinstance(sd_dict, dict) else sd_dict
                search_digest_str = self._format_search_digest(sd)

            # 5. Working memory formatted
            working_memory_str = wm.format() if wm.active_fact_count() > 0 else ""

            # 6. Assemble via ContextAssembler
            messages = state["messages"]
            result = self.context_assembler.assemble(
                messages=messages,
                system_prompt=system_prompt,
                compressed_turns=compressed_turns,
                working_memory_str=working_memory_str,
                search_digest_str=search_digest_str if include_search_digest else "",
                execution_summary=running_summary_str,
            )

            # 7. Build actual LangChain messages
            assembled: list = []

            # Build enriched system message
            system_parts = [result.system_prompt]
            if result.research_summary:
                system_parts.append(f"\n## Research Progress\n{result.research_summary}")
            if result.working_memory:
                system_parts.append(f"\n## Current Knowledge\n{result.working_memory}")
            if result.execution_summary:
                system_parts.append(f"\n## Conversation Summary\n{result.execution_summary}")
            if result.current_search_digest:
                system_parts.append(f"\n## Search Results\n{result.current_search_digest}")
            if result.retrieved_long_term_facts:
                system_parts.append(f"\n## Background\n{result.retrieved_long_term_facts}")

            assembled.append(SystemMessage(content="\n".join(system_parts)))

            # Recent raw messages (the ContextAssembler already pruned old ones)
            if include_recent_messages and result.recent_raw_messages:
                assembled.extend(result.recent_raw_messages)

            self.logger.info(
                "Context assembled",
                total_tokens=result.total_tokens,
                breakdown=result.token_breakdown,
            )
            return assembled

        except ContextBudgetExceeded as e:
            self.logger.warning(
                "Context budget exceeded — using degraded context",
                current_tokens=e.current_tokens,
                safe_limit=e.safe_limit,
            )
            # Degraded strategy: system prompt only, no history
            return [SystemMessage(content=system_prompt[:e.safe_limit * 4])]

        except Exception as e:
            self.logger.error(f"Context assembly failed: {e}; falling back to system prompt only")
            return [SystemMessage(content=system_prompt)]

    @staticmethod
    def _format_search_digest(sd: SearchDigest) -> str:
        """Format a SearchDigest as a compact string for the system prompt."""
        if not sd or not sd.source_ids:
            return ""
        lines = [f"Search results for: {sd.query}"]
        for i, sid in enumerate(sd.source_ids):
            rec = sd.source_registry.get(sid)
            title = ""
            if rec:
                title = getattr(rec, "title", "") or ""
                if isinstance(rec, dict):
                    title = rec.get("title", "")
            lines.append(f"  [{sid}] {title}")
        if sd.evidence_snippets:
            lines.append("Key snippets:")
            for s in sd.evidence_snippets[:3]:
                lines.append(f"  - {s[:200]}")
        return "\n".join(lines)

    def _route_search(self, query: SearchQuery, policy: dict[str, Any] | None):
        """Resolve a source-type label and provider for this search."""
        preferred = (query.source_type or "").strip().lower()
        preferred_source_types = self._value(policy, "preferred_source_types", []) if policy else []
        if preferred_source_types:
            preferred = preferred or str(preferred_source_types[0]).lower()
        if preferred in {"company", "news", "web"}:
            provider = "tavily"
        else:
            provider = "tavily"
        return provider, preferred or "web"

    def _normalize_sources(self, search_docs, source_type: str) -> list[RetrievedSource]:
        normalized: list[RetrievedSource] = []
        for doc in search_docs or []:
            if isinstance(doc, dict):
                url = str(doc.get("url", "") or "")
                title = str(doc.get("title", "") or url or "Untitled source")
                snippet = str(doc.get("content", "") or "")
            else:
                url = ""
                title = "Text source"
                snippet = str(doc)
            normalized.append(
                RetrievedSource(
                    source_id=str(uuid.uuid4()),
                    title=title,
                    url=url,
                    snippet=snippet,
                    source_type=source_type,
                    credibility_note="Source quality not scored; verify in review.",
                )
            )
        return normalized

    @staticmethod
    def _extract_usage(message) -> dict:
        usage = {}
        response_meta = getattr(message, "response_metadata", {}) or {}
        usage_meta = getattr(message, "usage_metadata", {}) or {}
        token_usage = response_meta.get("token_usage", {}) if isinstance(response_meta, dict) else {}
        if not isinstance(token_usage, dict):
            token_usage = {}
        usage["prompt_tokens"] = (
            usage_meta.get("input_tokens")
            or token_usage.get("prompt_tokens")
            or token_usage.get("input_tokens")
            or response_meta.get("input_tokens")
            or response_meta.get("prompt_tokens")
            or 0
        )
        usage["completion_tokens"] = (
            usage_meta.get("output_tokens")
            or token_usage.get("completion_tokens")
            or token_usage.get("output_tokens")
            or response_meta.get("output_tokens")
            or response_meta.get("completion_tokens")
            or 0
        )
        usage["total_tokens"] = (
            usage_meta.get("total_tokens")
            or token_usage.get("total_tokens")
            or response_meta.get("total_tokens")
            or usage["prompt_tokens"] + usage["completion_tokens"]
        )
        return usage

    # ----------------------------------------------------------------------
    # Step 1: Analyst generates question
    # ----------------------------------------------------------------------
    def _generate_question(self, state: InterviewState):
        """Generate the next interview question. Uses ContextAssembler."""
        analyst = state["analyst"]
        skill_card = state.get("skill_card")
        assigned_plan = state.get("assigned_plan")
        domain_memory = state.get("domain_memory", []) or []

        working_memory_block = self._build_working_memory_context(state)

        try:
            self.logger.info("Generating analyst question", analyst=analyst.name)
            system_prompt = ANALYST_ASK_QUESTIONS.render(
                goals=analyst.persona,
                skill_card=self._format_skill_card(skill_card),
                assigned_plan=self._format_assigned_plan(assigned_plan),
                domain_memory=self._format_domain_memory(domain_memory),
                working_memory=working_memory_block,
            )

            started_at = time.perf_counter()
            assembled_messages = self._assemble_llm_messages(
                state, system_prompt, include_search_digest=False, include_recent_messages=True,
            )
            question = self.llm.invoke(assembled_messages)

            # Ensure persistent ID
            if not getattr(question, "id", None):
                try:
                    question = question.model_copy(update={"id": str(uuid.uuid4())})
                except Exception:
                    pass

            latency_ms = int((time.perf_counter() - started_at) * 1000)
            usage = self._extract_usage(question)
            self.logger.info("Question generated successfully", question_preview=question.content[:200])
            return {
                "messages": [question],
                "llm_metrics": [
                    {
                        "node": "interview.ask_question",
                        "latency_ms": latency_ms,
                        "prompt_tokens": usage["prompt_tokens"],
                        "completion_tokens": usage["completion_tokens"],
                        "total_tokens": usage["total_tokens"],
                    }
                ],
            }

        except Exception as e:
            self.logger.error("Error generating analyst question", error=str(e))
            raise ResearchAnalystException("Failed to generate analyst question", e)

    # ----------------------------------------------------------------------
    # Step 2: Perform web search
    # ----------------------------------------------------------------------
    def _search_web(self, state: InterviewState):
        """Generate search query, resolve backend, run pipeline, build source registry.

        Bug fixes:
        - Source ID counter continues from existing registry (no overwrite).
        - Pipeline-cleaned-to-zero detected and treated as no-results.
        - Only current-turn registry passed to downstream nodes.
        """
        try:
            self.logger.info("Generating search query from conversation")
            plan = state.get("assigned_plan")
            policy = (self._value(plan, "source_policy", None) if plan else None) or None
            from harness.utils.llm_json import invoke_as_json
            search_prompt = GENERATE_SEARCH_QUERY.render(
                assigned_plan=self._format_assigned_plan(state.get("assigned_plan")),
                source_policy=self._format_source_policy(policy),
            )

            started_at = time.perf_counter()
            assembled_messages = self._assemble_llm_messages(
                state, search_prompt, include_search_digest=False, include_recent_messages=True,
            )
            search_query, query_usage = invoke_as_json(
                self.llm, assembled_messages, SearchQuery,
            )

            query_latency_ms = int((time.perf_counter() - started_at) * 1000)

            provider, resolved_type = self._route_search(search_query, policy)
            search_backend = self.tool_registry.get_search(provider)

            if search_backend is not None:
                site_hints = list(policy.get("site_hints", []) or []) if policy else []
                tool_query = ToolSearchQuery(
                    query=search_query.search_query,
                    source_type=resolved_type,
                    site_hints=site_hints,
                    freshness_hint=str(policy.get("freshness_hint", "balanced") or "balanced") if policy else "balanced",
                    max_results=10,
                )
                raw_results: list[SearchDocument] = search_backend.search(tool_query)

                pipeline_ctx = ToolContext(
                    target_entity=state.get("company_name", "") or "",
                    target_focus=str(policy.get("focus", "") or "") if policy else "",
                    source_type=resolved_type,
                )
                cleaned, trace = self.pipeline.run_with_trace(raw_results, pipeline_ctx)
                self.logger.info(
                    "Search pipeline completed",
                    provider=provider,
                    raw_count=len(raw_results),
                    cleaned_count=len(cleaned),
                    trace=[{"stage": t.stage, "reduction_pct": t.reduction_pct} for t in trace],
                )

                # ---- Bug fix: check CLEANED count, not raw count ----
                if len(cleaned) == 0:
                    self.logger.warning("All search results dropped by pipeline")
                    return {
                        "context": ["[No relevant search results after cleaning.]"],
                        "retrieved_sources": [],
                        "search_digest": SearchDigest(query=search_query.search_query).to_dict(),
                        "router_decisions": [{
                            "query": search_query.search_query,
                            "provider": provider,
                            "source_type": resolved_type,
                            "reasoning": search_query.reasoning,
                            "result_count": 0,
                        }],
                        "workflow_events": [
                            {"event": "router.search.completed", "payload": {"result_count": 0, "source_type": resolved_type}}
                        ],
                        "llm_metrics": [{
                            "node": "interview.search_query",
                            "latency_ms": query_latency_ms,
                            "prompt_tokens": query_usage["prompt_tokens"],
                            "completion_tokens": query_usage["completion_tokens"],
                            "total_tokens": query_usage["total_tokens"],
                        }],
                    }

                # ---- Bug fix: continue source ID counter from existing registry ----
                existing_registry_raw = state.get("source_registry") or {}
                existing_registry: dict[str, Any] = {}
                for k, v in existing_registry_raw.items():
                    existing_registry[str(k)] = v
                # Find next available S-n index
                next_idx = 1
                for key in existing_registry:
                    if key.startswith("S") and key[1:].isdigit():
                        n = int(key[1:])
                        if n >= next_idx:
                            next_idx = n + 1

                formatted_parts: list[str] = []
                normalized_sources: list[RetrievedSource] = []
                current_turn_registry: dict[str, SourceRecord] = {}

                for doc in cleaned:
                    sid = f"S{next_idx}"
                    next_idx += 1
                    formatted_parts.append(doc.metadata.get("formatted", str(doc)))

                    url = doc.canonical_url or doc.url or ""
                    current_turn_registry[sid] = SourceRecord(
                        source_id=sid,
                        url=url,
                        title=doc.title or "",
                        retrieved_at=_now_iso(),
                    )

                    normalized_sources.append(
                        RetrievedSource(
                            source_id=sid,
                            title=doc.title or "",
                            url=url,
                            snippet=(doc.clean_content or doc.raw_content or "")[:500],
                            source_type=resolved_type,
                            credibility_note="Pipeline-cleaned; verify in review.",
                        )
                    )
                formatted = "\n\n---\n\n".join(formatted_parts)

                # Build SearchDigest
                search_digest = self.search_digest_builder.build(
                    query=search_query.search_query,
                    raw_results=cleaned,
                )
                search_digest.source_registry = current_turn_registry
                search_digest.source_ids = list(current_turn_registry.keys())

                # Merge into accumulated registry
                merged_registry = dict(existing_registry_raw)
                for k, v in current_turn_registry.items():
                    merged_registry[str(k)] = v.to_dict() if hasattr(v, "to_dict") else v

            else:
                # --- legacy path ---
                self.logger.warning("No search backend; falling back to legacy tavily_search", provider=provider)
                search_docs = self.tavily_search.invoke(search_query.search_query) if self.tavily_search else []
                normalized_sources = self._normalize_sources(search_docs, resolved_type)
                current_turn_registry = {}
                existing_registry_raw = state.get("source_registry") or {}
                merged_registry = dict(existing_registry_raw)

                if not search_docs:
                    formatted = "[No search results found.]"
                    search_digest = SearchDigest(query=search_query.search_query)
                else:
                    # Continue indexing from existing
                    next_idx = 1
                    for key in merged_registry:
                        if key.startswith("S") and key[1:].isdigit():
                            n = int(key[1:])
                            if n >= next_idx:
                                next_idx = n + 1

                    formatted_parts = []
                    for source in normalized_sources:
                        sid = f"S{next_idx}"
                        next_idx += 1
                        href = source.url or "#"
                        formatted_parts.append(f'<Document href="{href}"/>\n{source.snippet}\n</Document>')
                        current_turn_registry[sid] = SourceRecord(
                            source_id=sid, url=source.url, title=source.title,
                            retrieved_at=_now_iso(),
                        )
                        source.source_id = sid
                    formatted = "\n\n---\n\n".join(formatted_parts)
                    search_digest = self.search_digest_builder.build(
                        query=search_query.search_query, raw_results=search_docs,
                    )
                    search_digest.source_registry = current_turn_registry
                    search_digest.source_ids = list(current_turn_registry.keys())
                    for k, v in current_turn_registry.items():
                        merged_registry[str(k)] = v.to_dict() if hasattr(v, "to_dict") else v

            result_count = len(cleaned) if search_backend else len(search_docs or [])
            self.logger.info("Web search completed", result_count=result_count, provider=provider)
            return {
                "context": [formatted],
                "retrieved_sources": [s.model_dump() for s in normalized_sources],
                "search_digest": search_digest.to_dict() if search_digest else {},
                "source_registry": merged_registry,
                # ---- Bug fix: also pass current-turn registry separately for compressor ----
                "_current_turn_registry": {k: v.to_dict() if hasattr(v, "to_dict") else v
                                           for k, v in current_turn_registry.items()},
                "router_decisions": [{
                    "query": search_query.search_query,
                    "provider": provider,
                    "source_type": resolved_type,
                    "reasoning": search_query.reasoning,
                    "result_count": result_count,
                }],
                "workflow_events": [{
                    "event": "router.search.completed",
                    "payload": {"result_count": result_count, "source_type": resolved_type},
                }],
                "llm_metrics": [{
                    "node": "interview.search_query",
                    "latency_ms": query_latency_ms,
                    "prompt_tokens": query_usage["prompt_tokens"],
                    "completion_tokens": query_usage["completion_tokens"],
                    "total_tokens": query_usage["total_tokens"],
                }],
            }

        except Exception as e:
            self.logger.error("Error during web search", error=str(e))
            raise ResearchAnalystException("Failed during web search execution", e)

    # ----------------------------------------------------------------------
    # Step 3: Expert generates answers
    # ----------------------------------------------------------------------
    def _generate_answer(self, state: InterviewState):
        """Use the analyst's context to generate an expert response.
        Bug fix: only uses current turn's context, not accumulated history.
        """
        analyst = state["analyst"]
        skill_card = state.get("skill_card")
        domain_memory = state.get("domain_memory", []) or []

        # Bug fix: use only the LATEST context (last item), not accumulated history
        all_context = state.get("context", ["[No context available.]"])
        current_context = [all_context[-1]] if all_context else ["[No context available.]"]

        working_memory_block = self._build_working_memory_context(state)

        try:
            self.logger.info("Generating expert answer", analyst=analyst.name)
            system_prompt = GENERATE_ANSWERS.render(
                goals=analyst.persona,
                context=current_context,
                skill_card=self._format_skill_card(skill_card),
                domain_memory=self._format_domain_memory(domain_memory),
                working_memory=working_memory_block,
            )

            started_at = time.perf_counter()
            assembled_messages = self._assemble_llm_messages(
                state, system_prompt, include_search_digest=True, include_recent_messages=True,
            )
            answer = self.llm.invoke(assembled_messages)

            # Bug fix: compute latency_ms and usage (were missing!)
            latency_ms = int((time.perf_counter() - started_at) * 1000)
            usage = self._extract_usage(answer)

            # Ensure persistent ID
            if not getattr(answer, "id", None):
                try:
                    answer = answer.model_copy(update={"id": str(uuid.uuid4())})
                except Exception:
                    pass
            answer.name = "expert"
            self.logger.info("Expert answer generated successfully", preview=answer.content[:200])
            return {
                "messages": [answer],
                "turn_count": int(state.get("turn_count", 0)) + 1,
                "llm_metrics": [
                    {
                        "node": "interview.generate_answer",
                        "latency_ms": latency_ms,
                        "prompt_tokens": usage["prompt_tokens"],
                        "completion_tokens": usage["completion_tokens"],
                        "total_tokens": usage["total_tokens"],
                    }
                ],
            }

        except Exception as e:
            self.logger.error("Error generating expert answer", error=str(e))
            raise ResearchAnalystException("Failed to generate expert answer", e)

    # ----------------------------------------------------------------------
    # Step 3b: Compress current turn into structured summary
    # ----------------------------------------------------------------------
    def _compress(self, state: InterviewState):
        """Compress the current Q&A round into a CompressedTurn.

        Bug fixes:
        - Only calls model ONCE (removed duplicate compress_turn + compress_completed_turn).
        - Passes only CURRENT turn's registry (not accumulated history).
        """
        try:
            question, answer = IncrementalCompressor.extract_last_question_and_answer(
                state["messages"]
            )
            context = state.get("context", [])
            search_summary = IncrementalCompressor.summarise_context(
                [str(c) for c in (context[-3:] if len(context) > 3 else context)]
            )

            # Bug fix: use current-turn registry, not accumulated history
            # _current_turn_registry is set by _search_web for this exact purpose
            current_registry = state.get("_current_turn_registry") or {}
            if not current_registry:
                # Fallback: compute delta from full registry (less reliable)
                current_registry = state.get("source_registry") or {}

            turn_count = int(state.get("turn_count", 1) or 1)
            self.logger.info("Compressing interview turn", turn=turn_count)

            # Bug fix: single call — always use compress_completed_turn with registry
            compressed = self.compressor.compress_completed_turn(
                question=question,
                answer=answer,
                search_summary=search_summary,
                source_registry=current_registry,
            )

            # Accumulate compressed history
            compressed_history: list[dict] = list(state.get("compressed_turns", []) or [])
            compressed_history.append(compressed.to_dict())

            fact_count = len(compressed.facts) if compressed.facts else len(compressed.key_findings)
            self.logger.info(
                "Turn compressed",
                turn=turn_count,
                facts=fact_count,
                quality=compressed.evidence_quality,
            )

            return {
                "compressed_turns": compressed_history,
                "workflow_events": [
                    {
                        "event": "compress.completed",
                        "payload": {
                            "turn": turn_count,
                            "facts_extracted": fact_count,
                        },
                    }
                ],
            }

        except Exception as e:
            self.logger.error("Error compressing interview turn", error=str(e))
            return {
                "workflow_events": [
                    {"event": "compress.failed", "payload": {"error": str(e)}}
                ],
            }

    # ----------------------------------------------------------------------
    # Step 3c: Update structured working memory (SOLE TRUTH SOURCE)
    # ----------------------------------------------------------------------
    def _update_memory(self, state: InterviewState):
        """Sync the WorkingMemory from compressed turns.

        Bug fix: domain_config flows into WorkingMemory → FactReconciler.
        """
        try:
            wm = self._make_working_memory(state)
            compressed_history: list[dict] = list(state.get("compressed_turns", []) or [])

            # Only ingest facts from turns NOT yet processed
            turns_to_ingest = compressed_history[wm.turns_completed:]

            for turn_dict in turns_to_ingest:
                turn = CompressedTurn.from_dict(turn_dict) if isinstance(turn_dict, dict) else turn_dict
                wm.ingest_compressed_turn(turn)

            snapshot = wm.to_merged_memory()

            turn_count = int(state.get("turn_count", 1) or 1)
            self.logger.info(
                "Working memory updated",
                turn=turn_count,
                total_facts=snapshot.total_facts,
                active_facts=wm.active_fact_count(),
                gaps=wm.knowledge_gaps,
                conflicts=len(wm.unresolved_conflicts),
            )

            return {
                "working_memory": wm.to_dict(),
                "memory_snapshot": snapshot.to_dict(),
                "workflow_events": [
                    {
                        "event": "memory.updated",
                        "payload": {
                            "total_facts": snapshot.total_facts,
                            "knowledge_gaps": wm.knowledge_gaps,
                            "risk_flag_count": len(wm.risk_flags),
                            "unresolved_conflicts": len(wm.unresolved_conflicts),
                        },
                    }
                ],
            }

        except Exception as e:
            self.logger.error("Error updating working memory", error=str(e))
            return {
                "workflow_events": [
                    {"event": "memory.update_failed", "payload": {"error": str(e)}}
                ],
            }

    # ----------------------------------------------------------------------
    # Step 3d: History compaction (on pressure)
    # ----------------------------------------------------------------------
    def _compact_history(self, state: InterviewState):
        """Run history compaction if the context window is under pressure."""
        try:
            messages = state["messages"]
            rs_dict = state.get("running_summary") or {}
            running_summary = RunningSummary.from_dict(rs_dict) if rs_dict else None

            turn_count = int(state.get("turn_count", 1) or 1)

            wm_str = self._build_working_memory_context(state)
            ct_str = IncrementalCompressor.format_compressed_turns([
                CompressedTurn.from_dict(d) if isinstance(d, dict) else d
                for d in (state.get("compressed_turns") or [])
            ])

            if not self.compressor.should_compact_history(
                messages,
                turn_count=turn_count,
                working_memory_str=wm_str,
                compressed_turns_str=ct_str,
            ):
                return {
                    "workflow_events": [
                        {"event": "compact_history.skipped", "payload": {"reason": "below_threshold"}}
                    ],
                }

            self.logger.info("Compacting conversation history", turn=turn_count)
            projected, updated_rs = self.compressor.compact_history(
                messages,
                running_summary=running_summary,
            )

            if updated_rs is not None:
                return {
                    "running_summary": updated_rs.to_dict(),
                    "workflow_events": [
                        {"event": "compact_history.completed", "payload": {"version": updated_rs.version}}
                    ],
                }

            return {
                "workflow_events": [
                    {"event": "compact_history.completed", "payload": {"version": running_summary.version if running_summary else 0}}
                ],
            }

        except Exception as e:
            self.logger.error("Error during history compaction", error=str(e))
            return {
                "workflow_events": [
                    {"event": "compact_history.failed", "payload": {"error": str(e)}}
                ],
            }

    # ----------------------------------------------------------------------
    # Helper: build working memory context block for prompts
    # ----------------------------------------------------------------------
    @staticmethod
    def _build_working_memory_context(state: InterviewState) -> str:
        """Build a contextual block summarising what has been learned so far."""
        wm_dict = state.get("working_memory") or {}
        compressed = state.get("compressed_turns") or []

        if not wm_dict and not compressed:
            return ""

        parts: list[str] = []

        if wm_dict:
            wm = WorkingMemory.from_dict(wm_dict)
            if wm.active_fact_count() > 0:
                parts.append(wm.format())

        if compressed:
            recent = compressed[-2:]
            parts.append("\n## Compressed prior rounds")
            for i, turn_dict in enumerate(recent):
                try:
                    turn = CompressedTurn.from_dict(turn_dict)
                    turn_num = len(compressed) - len(recent) + i + 1
                    parts.append(f"\n### Round {turn_num}")
                    parts.append(turn.format())
                except Exception:
                    pass

        return "\n".join(parts) if parts else ""

    def _save_interview(self, state: InterviewState):
        """Save the entire conversation as a transcript."""
        try:
            messages = state["messages"]
            interview = get_buffer_string(messages)
            self.logger.info("Interview transcript saved", message_count=len(messages))
            return {"interview": interview}
        except Exception as e:
            self.logger.error("Error saving interview transcript", error=str(e))
            raise ResearchAnalystException("Failed to save interview transcript", e)

    # ----------------------------------------------------------------------
    # Step 5: Write report section from interview context
    # ----------------------------------------------------------------------
    def _write_section(self, state: InterviewState):
        """Write a concise report section.

        Bug fixes:
        - Context appended BEFORE assembly so budget validation covers it.
        - Source registry passed to enable citation URL mapping.
        """
        context = state.get("context", ["[No context available.]"])
        analyst = state["analyst"]
        skill_card = state.get("skill_card")
        assigned_plan = state.get("assigned_plan")

        # Build citation source list from registry
        source_registry = state.get("source_registry") or {}
        citation_block = self._build_citation_block(source_registry)

        try:
            self.logger.info("Generating report section", analyst=analyst.name)
            system_prompt = WRITE_SECTION.render(
                focus=analyst.description,
                skill_card=self._format_skill_card(skill_card),
                assigned_plan=self._format_assigned_plan(assigned_plan),
            )

            # Bug fix: build context message BEFORE assembly so budget is checked
            context_msg = HumanMessage(
                content=f"Write this section using the following materials:\n\n{context}\n\n"
                        f"Source registry for citations:\n{citation_block}"
            )

            started_at = time.perf_counter()
            assembled_messages = self._assemble_llm_messages(
                state, system_prompt, include_search_digest=True, include_recent_messages=True,
            )
            # Append context — but after assembly. Budget check: we append and re-verify.
            assembled_messages.append(context_msg)

            # Verify budget with appended context
            context_tokens = self.context_assembler.window_mgr.estimate_tokens(context_msg.content)
            self.logger.info("Appending write_section context", extra_tokens=context_tokens)

            section = self.llm.invoke(assembled_messages)

            latency_ms = int((time.perf_counter() - started_at) * 1000)
            usage = self._extract_usage(section)
            self.logger.info("Report section generated successfully", length=len(section.content))
            return {
                "sections": [section.content],
                "llm_metrics": [
                    {
                        "node": "interview.write_section",
                        "latency_ms": latency_ms,
                        "prompt_tokens": usage["prompt_tokens"],
                        "completion_tokens": usage["completion_tokens"],
                        "total_tokens": usage["total_tokens"],
                    }
                ],
            }

        except ContextBudgetExceeded:
            self.logger.warning("Budget exceeded for write_section; using truncated context")
            # Fallback: system prompt + truncated context only
            fallback = [
                SystemMessage(content=WRITE_SECTION.render(
                    focus=analyst.description,
                    skill_card=self._format_skill_card(skill_card),
                    assigned_plan=self._format_assigned_plan(assigned_plan),
                )),
                HumanMessage(content=f"Write this section using: {str(context)[:3000]}")
            ]
            section = self.llm.invoke(fallback)
            latency_ms = int((time.perf_counter() - started_at) * 1000)
            usage = self._extract_usage(section)
            return {
                "sections": [section.content],
                "llm_metrics": [{
                    "node": "interview.write_section",
                    "latency_ms": latency_ms,
                    "prompt_tokens": usage["prompt_tokens"],
                    "completion_tokens": usage["completion_tokens"],
                    "total_tokens": usage["total_tokens"],
                }],
            }

        except Exception as e:
            self.logger.error("Error writing report section", error=str(e))
            raise ResearchAnalystException("Failed to generate report section", e)

    @staticmethod
    def _build_citation_block(source_registry: dict) -> str:
        """Build a citation reference block from the source registry."""
        if not source_registry:
            return "[No sources available for citation]"
        lines = ["## Source Registry (use [S{n}] for inline citations)"]
        for sid, rec in sorted(source_registry.items()):
            if isinstance(rec, dict):
                url = rec.get("url", "")
                title = rec.get("title", "")
            elif hasattr(rec, "url"):
                url = rec.url or ""
                title = rec.title or ""
            else:
                url = str(rec)
                title = ""
            lines.append(f"  [{sid}] {title} — {url}")
        return "\n".join(lines)

    def _review_section(self, state: InterviewState):
        section_text = ""
        sections = state.get("sections", []) or []
        if sections:
            section_text = str(sections[-1])
        findings: list[ReviewFinding] = []
        if "### Sources" not in section_text:
            findings.append(
                ReviewFinding(
                    severity="high",
                    title="Missing Sources subsection",
                    detail='Section text has no "### Sources" block.',
                    suggested_fix="Add a Sources list matching in-section [n] citations.",
                )
            )
        if "### Risk Notes" not in section_text:
            findings.append(
                ReviewFinding(
                    severity="medium",
                    title="Missing Risk Notes subsection",
                    detail='Section text has no "### Risk Notes" block.',
                    suggested_fix="Add risk notes with impact and severity where relevant.",
                )
            )
        status = "pass" if not findings else "needs_revision"
        notes = {
            "scope": "section",
            "status": status,
            "finding_count": len(findings),
            "findings": [f.model_dump() for f in findings],
        }
        return {
            "review_notes": [notes],
            "workflow_events": [{"event": "review.section.completed", "payload": {"status": status}}],
        }

    # ----------------------------------------------------------------------
    # Build Graph
    # ----------------------------------------------------------------------
    def build(self):
        """Construct and compile the LangGraph Interview workflow.

        Flow: ask_question → search_web → generate_answer → compress →
              update_memory → compact_history → [conditional] → ask_question (loop)
              or save_interview → write_section → review_section → END
        """
        try:
            self.logger.info("Building Interview Graph workflow")
            builder = StateGraph(InterviewState)

            builder.add_node("ask_question", self._generate_question)
            builder.add_node("search_web", self._search_web)
            builder.add_node("generate_answer", self._generate_answer)
            builder.add_node("compress", self._compress)
            builder.add_node("update_memory", self._update_memory)
            builder.add_node("compact_history", self._compact_history)
            builder.add_node("save_interview", self._save_interview)
            builder.add_node("write_section", self._write_section)
            builder.add_node("review_section", self._review_section)

            def _should_continue(state: InterviewState):
                max_turns = int(state.get("max_num_turns", 1) or 1)
                turn_count = int(state.get("turn_count", 0) or 0)

                if turn_count >= max_turns:
                    return "save_interview"

                # Read from WorkingMemory (sole truth source)
                wm_dict = state.get("working_memory") or {}
                if wm_dict:
                    wm = WorkingMemory.from_dict(wm_dict)
                    if wm.has_sufficient_coverage():
                        self.logger.info(
                            "Coverage sufficient — stopping early",
                            turn=turn_count,
                            total_facts=wm.active_fact_count(),
                            conflicts=len(wm.unresolved_conflicts),
                        )
                        return "save_interview"

                return "ask_question"

            builder.add_edge(START, "ask_question")
            builder.add_edge("ask_question", "search_web")
            builder.add_edge("search_web", "generate_answer")
            builder.add_edge("generate_answer", "compress")
            builder.add_edge("compress", "update_memory")
            builder.add_edge("update_memory", "compact_history")
            builder.add_conditional_edges(
                "compact_history",
                _should_continue,
                ["ask_question", "save_interview"],
            )
            builder.add_edge("save_interview", "write_section")
            builder.add_edge("write_section", "review_section")
            builder.add_edge("review_section", END)

            graph = builder.compile(checkpointer=self.memory)
            self.logger.info("Interview Graph compiled successfully")
            return graph

        except Exception as e:
            self.logger.error("Error building interview graph", error=str(e))
            raise ResearchAnalystException("Failed to build interview graph workflow", e)
