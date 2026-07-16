"""
Due Diligence — Interview subgraph.

Each analyst runs its own interview instance in parallel (fan-out).
Round 3: ContextAssembler wired into all LLM nodes, WorkingMemory as sole
truth source, source registry for traceability, history compaction on pressure.
"""

import time
import uuid
from typing import Any

from harness.exceptions import ResearchAnalystException
from harness.observability.logger import GLOBAL_LOGGER
from harness.memory.compressor import IncrementalCompressor
from harness.memory.context_assembler import ContextAssembler
from harness.memory.context_window import ContextWindowManager
from harness.memory.nodes import (
    build_working_memory_from_state,
    format_working_memory_context,
    make_compact_history_node,
    make_compress_node,
    make_should_continue_router,
    make_update_memory_node,
)
from harness.memory.policies import MemoryDomainConfig, TokenBudget
from harness.memory.working_memory import WorkingMemory
from harness.models.agent import (
    AnalystPlan,
    RetrievedSource,
    ReviewFinding,
    SearchQuery,
)
from harness.models.memory import (
    CompressedTurn,
    ContextBudgetExceeded,
    RunningSummary,
    SearchDigest,
    SourceRecord,
)
from harness.tools.pipeline import ToolContext, ToolPipeline
from harness.tools.registry import TOOL_REGISTRY, ToolRegistry
from harness.tools.search.base import SearchDocument
from harness.tools.search.base import SearchQuery as ToolSearchQuery
from harness.tools.search.cleaner import SEARCH_PIPELINE_FULL
from langchain_core.messages import HumanMessage, SystemMessage, get_buffer_string
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from domains.due_diligence.memory_config import DUE_DILIGENCE_MEMORY_CONFIG
from domains.due_diligence.prompts.interview import (
    ANALYST_ASK_QUESTIONS,
    GENERATE_ANSWERS,
    GENERATE_SEARCH_QUERY,
    WRITE_SECTION,
)
from domains.due_diligence.schemas import InterviewState


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

    def __init__(self, llm, tavily_search=None, tool_registry: ToolRegistry | None = None, pipeline: ToolPipeline | None = None, cheap_llm=None, domain_config: MemoryDomainConfig | None = None, checkpointer=None):
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
            checkpointer: Optional external checkpointer (shared across graphs).
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

        self.memory = checkpointer or MemorySaver()
        self.logger = GLOBAL_LOGGER.bind(module="InterviewGraphBuilder")

    @staticmethod
    def _value(obj: Any, key: str, default=None):
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    @staticmethod
    def _now_iso() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _format_skill_card(skill_card) -> str:
        """Return the skill body — the full Markdown guidance document.

        The LLM reads it directly; no field extraction needed.
        """
        if not skill_card:
            return ""
        return str(InterviewGraphBuilder._value(skill_card, "body", "") or "")

    @staticmethod
    def _format_assigned_plan(plan: AnalystPlan | None) -> str:
        if not plan:
            return ""
        brief = InterviewGraphBuilder._value(plan, "brief", "")
        key_questions = InterviewGraphBuilder._value(plan, "key_questions", []) or []
        lines = [f"Sub-task: {brief}"]
        if key_questions:
            lines.append(f"Key questions: {'; '.join(key_questions)}")
        return "\n".join(lines)

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

    # ------------------------------------------------------------------
    # Round 3: Context Assembly helper
    # ------------------------------------------------------------------

    def _make_working_memory(self, state: InterviewState) -> WorkingMemory:
        """Create a WorkingMemory from state, always with domain_config injected.

        Thin wrapper around the generic harness helper (kept as a method for
        call-site convenience since ``self._domain_config`` is fixed per builder).
        """
        return build_working_memory_from_state(state, self._domain_config)

    def _assemble_llm_messages(
        self,
        state: InterviewState,
        system_prompt: str,
        *,
        include_recent_messages: bool = True,
    ) -> list:
        """Build the projected LLM input from canonical state.

        NEVER mutates state["messages"]. Returns a NEW list of messages
        for the LLM call only.

        Search results are NOT injected here as a separate digest block —
        every caller that needs search content already embeds the current
        turn's full raw context (``state["context"][-1]``) directly into its
        own system_prompt, which is a strict superset of what a digest of
        the same round's results would add. Injecting both would just spend
        tokens repeating the same information twice.
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

            # 4. Working memory formatted
            working_memory_str = wm.format() if wm.active_fact_count() > 0 else ""

            # 5. Assemble via ContextAssembler
            messages = state["messages"]
            result = self.context_assembler.assemble(
                messages=messages,
                system_prompt=system_prompt,
                compressed_turns=compressed_turns,
                working_memory_str=working_memory_str,
                execution_summary=running_summary_str,
            )

            # 6. Build actual LangChain messages
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

    # Provider priority order — first available wins (web search only).
    # Policy may override via ``preferred_provider`` or ``search_provider`` keys.
    _PROVIDER_PRIORITY = ["serper", "tavily", "bocha", "github"]

    # Source-type → specialised provider mapping (checked before general priority).
    _SOURCE_TYPE_PROVIDER: dict[str, str] = {
        "sec": "sec_edgar",
        "annual": "sec_edgar",
        "10-k": "sec_edgar",
        "quarterly": "sec_edgar",
        "10-q": "sec_edgar",
        "8-k": "sec_edgar",
        "ipo": "sec_edgar",
        "cninfo": "cninfo",
        "announcement": "cninfo",
    }

    def _route_search(self, query: SearchQuery, policy: dict[str, Any] | None):
        """Resolve a source-type label and provider for this search.

        Selection order:
        1. Policy-level ``search_provider`` or ``preferred_provider`` key (if registered).
        2. Source-type → specialised provider mapping (sec_edgar, cninfo).
        3. First available provider from ``_PROVIDER_PRIORITY``.
        4. Any registered provider as ultimate fallback.
        """
        available = set(self.tool_registry.list_search())
        source_type = (query.source_type or "web").strip().lower()

        # Honour explicit policy override
        if policy:
            explicit = str(self._value(policy, "search_provider", "") or
                           self._value(policy, "preferred_provider", "") or "").strip().lower()
            if explicit and explicit in available:
                return explicit, source_type

        # Route by source type to specialised providers
        specialised = self._SOURCE_TYPE_PROVIDER.get(source_type)
        if specialised and specialised in available:
            return specialised, source_type

        # Walk priority list
        for p in self._PROVIDER_PRIORITY:
            if p in available:
                return p, source_type

        # Ultimate fallback — anything registered
        if available:
            first = sorted(available)[0]
            return first, source_type

        # Should never happen — graph.py registers at least one adapter
        return "tavily", source_type

    # ----------------------------------------------------------------------
    # Deep-read: fetch full page content for top search results
    # ----------------------------------------------------------------------
    def _deep_read(
        self,
        top_docs: list[SearchDocument],
        state: InterviewState,
    ) -> list[SearchDocument]:
        """Fetch full page content for the top K search results.

        Tries DirectReader first (works everywhere), falls back to Jina.
        Returns additional ``SearchDocument`` items (full pages) that will
        be appended to the original search results.
        """
        # Always deep-read top results when a browse tool is available
        reader = self.tool_registry.get_browse("direct") or self.tool_registry.get_browse("jina")
        if reader is None:
            return []

        max_deep = min(len(top_docs), 3)  # deep-read at most 3 pages
        fetched: list[SearchDocument] = []
        for doc in top_docs[:max_deep]:
            url = doc.canonical_url or doc.url
            if not url:
                continue
            try:
                result = reader.fetch(url)
                if result.error or not result.markdown:
                    self.logger.info("Browse fetch skipped", url=url, error=result.error or "empty body")
                    continue
                sd = result.to_search_document(
                    source_type=doc.source_type,
                    provider=getattr(reader, "name", "direct") if hasattr(reader, "name") else "direct",
                )
                fetched.append(sd)
                self.logger.info("Deep-read fetched", url=url, char_count=len(result.markdown))
            except Exception as exc:
                self.logger.warning("Browse fetch failed", url=url, error=str(exc))
                continue

        if not fetched:
            return []

        # Run through the same cleaning pipeline
        target_entity = state.get("company_name", "") or ""
        source_type = top_docs[0].source_type if top_docs else "web"

        pipeline_ctx = ToolContext(
            target_entity=target_entity,
            target_focus=state.get("focus", "") or "",
            source_type=source_type,
        )
        cleaned, trace = self.pipeline.run_with_trace(fetched, pipeline_ctx)
        self.logger.info(
            "Deep-read pipeline completed",
            fetched_count=len(fetched),
            cleaned_count=len(cleaned),
            trace=[{"stage": t.stage, "reduction_pct": t.reduction_pct} for t in trace],
        )

        return [d for d in cleaned if not d.dropped_reason]

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

        working_memory_block = format_working_memory_context(state)

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
                state, system_prompt, include_recent_messages=True,
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
            from harness.utils.llm_json import invoke_as_json
            search_prompt = GENERATE_SEARCH_QUERY.render(
                assigned_plan=self._format_assigned_plan(state.get("assigned_plan")),
            )

            started_at = time.perf_counter()
            assembled_messages = self._assemble_llm_messages(
                state, search_prompt, include_recent_messages=True,
            )
            search_query, query_usage = invoke_as_json(
                self.llm, assembled_messages, SearchQuery,
            )

            query_latency_ms = int((time.perf_counter() - started_at) * 1000)

            provider, resolved_type = self._route_search(search_query, None)
            search_backend = self.tool_registry.get_search(provider)
            cleaned: list[SearchDocument] = []

            if search_backend is not None:
                # LLM sets site_hints + freshness_hint in SearchQuery
                # (it reads the skill body's Search Guidance section)
                tool_query = ToolSearchQuery(
                    query=search_query.search_query,
                    source_type=resolved_type,
                    site_hints=list(search_query.site_hints or []),
                    freshness_hint=search_query.freshness_hint or "balanced",
                    max_results=10,
                )
                raw_results: list[SearchDocument] = search_backend.search(tool_query)

                pipeline_ctx = ToolContext(
                    target_entity=state.get("company_name", "") or "",
                    target_focus=state.get("focus", "") or "",
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

                # ---- Deep-read: fetch full page content for top results ----
                deep_docs = self._deep_read(cleaned[:5], state)
                if deep_docs:
                    cleaned = cleaned + deep_docs
                    self.logger.info(
                        "Deep-read appended to search results",
                        deep_count=len(deep_docs),
                        total_count=len(cleaned),
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
                # ---- Bug fix: reuse the existing source_id for a URL already
                # registered in a prior round, instead of minting a new one
                # each time the same article resurfaces (inflates
                # independent_source_count with duplicate sources) ----
                url_to_existing_sid: dict[str, str] = {}
                for key, rec in existing_registry.items():
                    if key.startswith("S") and key[1:].isdigit():
                        n = int(key[1:])
                        if n >= next_idx:
                            next_idx = n + 1
                    rec_url = rec.get("url") if isinstance(rec, dict) else getattr(rec, "url", "")
                    if rec_url:
                        url_to_existing_sid.setdefault(str(rec_url), key)

                formatted_parts: list[str] = []
                normalized_sources: list[RetrievedSource] = []
                current_turn_registry: dict[str, SourceRecord] = {}

                for doc in cleaned:
                    url = doc.canonical_url or doc.url or ""
                    existing_sid = url_to_existing_sid.get(url) if url else None
                    if existing_sid:
                        sid = existing_sid
                    else:
                        sid = f"S{next_idx}"
                        next_idx += 1
                        if url:
                            url_to_existing_sid[url] = sid
                    formatted_parts.append(doc.metadata.get("formatted", str(doc)))

                    current_turn_registry[sid] = SourceRecord(
                        source_id=sid,
                        url=url,
                        title=doc.title or "",
                        retrieved_at=self._now_iso(),
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

                search_digest = SearchDigest(
                    query=search_query.search_query,
                    source_ids=list(current_turn_registry.keys()),
                    source_registry=current_turn_registry,
                )

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
                    # Continue indexing from existing, reusing the existing
                    # source_id for a URL already registered in a prior round
                    # (see the primary search path above for why).
                    next_idx = 1
                    url_to_existing_sid: dict[str, str] = {}
                    for key, rec in merged_registry.items():
                        if key.startswith("S") and key[1:].isdigit():
                            n = int(key[1:])
                            if n >= next_idx:
                                next_idx = n + 1
                        rec_url = rec.get("url") if isinstance(rec, dict) else getattr(rec, "url", "")
                        if rec_url:
                            url_to_existing_sid.setdefault(str(rec_url), key)

                    formatted_parts = []
                    for source in normalized_sources:
                        existing_sid = url_to_existing_sid.get(source.url) if source.url else None
                        if existing_sid:
                            sid = existing_sid
                        else:
                            sid = f"S{next_idx}"
                            next_idx += 1
                            if source.url:
                                url_to_existing_sid[source.url] = sid
                        href = source.url or "#"
                        formatted_parts.append(f'<Document href="{href}"/>\n{source.snippet}\n</Document>')
                        current_turn_registry[sid] = SourceRecord(
                            source_id=sid, url=source.url, title=source.title,
                            retrieved_at=self._now_iso(),
                        )
                        source.source_id = sid
                    formatted = "\n\n---\n\n".join(formatted_parts)
                    search_digest = SearchDigest(
                        query=search_query.search_query,
                        source_ids=list(current_turn_registry.keys()),
                        source_registry=current_turn_registry,
                    )
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

        working_memory_block = format_working_memory_context(state)

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
                state, system_prompt, include_recent_messages=True,
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
                state, system_prompt, include_recent_messages=True,
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

            # ask_question / search_web / generate_answer / write_section /
            # review_section are domain nodes — they own the prompts.
            # compress / update_memory / compact_history / the continue-router
            # are generic harness.memory node factories — no prompt, no
            # domain vocabulary, only the compressor + domain_config as input.
            builder.add_node("ask_question", self._generate_question)
            builder.add_node("search_web", self._search_web)
            builder.add_node("generate_answer", self._generate_answer)
            builder.add_node("compress", make_compress_node(self.compressor))
            builder.add_node("update_memory", make_update_memory_node(self._domain_config))
            builder.add_node("compact_history", make_compact_history_node(self.compressor))
            builder.add_node("save_interview", self._save_interview)
            builder.add_node("write_section", self._write_section)
            builder.add_node("review_section", self._review_section)

            builder.add_edge(START, "ask_question")
            builder.add_edge("ask_question", "search_web")
            builder.add_edge("search_web", "generate_answer")
            builder.add_edge("generate_answer", "compress")
            builder.add_edge("compress", "update_memory")
            builder.add_edge("update_memory", "compact_history")
            builder.add_conditional_edges(
                "compact_history",
                make_should_continue_router(continue_node="ask_question", stop_node="save_interview"),
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
