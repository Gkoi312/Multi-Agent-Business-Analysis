from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.messages import get_buffer_string
import time
import uuid
from typing import Any

from app.schemas.models import AnalystPlan, InterviewState, RetrievedSource, ReviewFinding, SearchQuery
from app.prompt_lib.prompt_locator import (
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
        4. Saving the interview transcript.
        5. Writing a summarized report section.
    """

    def __init__(self, llm, tavily_search):
        """
        Initialize the InterviewGraphBuilder with the LLM model and Tavily search tool.
        """
        self.llm = llm
        self.tavily_search = tavily_search
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

    def _route_search(self, query: SearchQuery, policy: dict[str, Any] | None):
        """
        Resolve a source-type label for this search (logging and RetrievedSource.source_type).
        Prefer `source_type` from structured output; if empty, fall back to the first entry in
        `source_policy.preferred_source_types`; if still empty, use "web". Provider is always tavily.
        """
        # Model-filled type in SearchQuery (preferred path)
        preferred = (query.source_type or "").strip().lower()
        preferred_source_types = self._value(policy, "preferred_source_types", []) if policy else []
        if preferred_source_types:
            # When source_type is empty, use first policy preference
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
    # 🔹 Step 1: Analyst generates question
    # ----------------------------------------------------------------------
    def _generate_question(self, state: InterviewState):
        """
        Generate the first question for the interview based on the analyst's persona.
        """
        analyst = state["analyst"]
        skill_card = state.get("skill_card")
        assigned_plan = state.get("assigned_plan")
        domain_memory = state.get("domain_memory", []) or []

        try:
            self.logger.info("Generating analyst question", analyst=analyst.name)
            system_prompt = ANALYST_ASK_QUESTIONS.render(
                goals=analyst.persona,
                skill_card=self._format_skill_card(skill_card),
                assigned_plan=self._format_assigned_plan(assigned_plan),
                domain_memory=self._format_domain_memory(domain_memory),
            )
            started_at = time.perf_counter()
            question = self.llm.invoke([SystemMessage(content=system_prompt)] + state["messages"])
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
    # 🔹 Step 2: Perform web search
    # ----------------------------------------------------------------------
    def _search_web(self, state: InterviewState):
        """
        Generate a structured search query and perform Tavily web search.
        """
        try:
            self.logger.info("Generating search query from conversation")
            # Use assigned_plan.source_policy only (from main graph plan_research); None if missing
            plan = state.get("assigned_plan")
            policy = (self._value(plan, "source_policy", None) if plan else None) or None
            structure_llm = self.llm.with_structured_output(SearchQuery)
            search_prompt = GENERATE_SEARCH_QUERY.render(
                assigned_plan=self._format_assigned_plan(state.get("assigned_plan")),
                source_policy=self._format_source_policy(policy),
            )
            started_at = time.perf_counter()
            search_query = structure_llm.invoke([SystemMessage(content=search_prompt)] + state["messages"])
            latency_ms = int((time.perf_counter() - started_at) * 1000)
            usage = self._extract_usage(search_query)

            provider, resolved_type = self._route_search(search_query, policy)
            self.logger.info(
                "Performing routed web search",
                provider=provider,
                source_type=resolved_type,
                query=search_query.search_query,
            )
            search_docs = self.tavily_search.invoke(search_query.search_query)
            normalized_sources = self._normalize_sources(search_docs, resolved_type)

            if not search_docs:
                self.logger.warning("No search results found")
                return {
                    "context": ["[No search results found.]"],
                    "router_decisions": [
                        {
                            "query": search_query.search_query,
                            "provider": provider,
                            "source_type": resolved_type,
                            "reasoning": search_query.reasoning,
                            "result_count": 0,
                        }
                    ],
                    "workflow_events": [
                        {"event": "router.search.completed", "payload": {"result_count": 0, "source_type": resolved_type}}
                    ],
                    "llm_metrics": [
                        {
                            "node": "interview.search_query",
                            "latency_ms": latency_ms,
                            "prompt_tokens": usage["prompt_tokens"],
                            "completion_tokens": usage["completion_tokens"],
                            "total_tokens": usage["total_tokens"],
                        }
                    ],
                }

            formatted_docs = []
            for source in normalized_sources:
                href = source.url or "#"
                content = source.snippet
                formatted_docs.append(f'<Document href="{href}"/>\n{content}\n</Document>')
            formatted = "\n\n---\n\n".join(formatted_docs)
            self.logger.info("Web search completed", result_count=len(search_docs))
            return {
                "context": [formatted],
                "retrieved_sources": [source.model_dump() for source in normalized_sources],
                "router_decisions": [
                    {
                        "query": search_query.search_query,
                        "provider": provider,
                        "source_type": resolved_type,
                        "reasoning": search_query.reasoning,
                        "result_count": len(normalized_sources),
                    }
                ],
                "workflow_events": [
                    {
                        "event": "router.search.completed",
                        "payload": {"result_count": len(normalized_sources), "source_type": resolved_type},
                    }
                ],
                "llm_metrics": [
                    {
                        "node": "interview.search_query",
                        "latency_ms": latency_ms,
                        "prompt_tokens": usage["prompt_tokens"],
                        "completion_tokens": usage["completion_tokens"],
                        "total_tokens": usage["total_tokens"],
                    }
                ],
            }

        except Exception as e:
            self.logger.error("Error during web search", error=str(e))
            raise ResearchAnalystException("Failed during web search execution", e)

    # ----------------------------------------------------------------------
    # 🔹 Step 3: Expert generates answers
    # ----------------------------------------------------------------------
    def _generate_answer(self, state: InterviewState):
        """
        Use the analyst's context to generate an expert response.
        """
        analyst = state["analyst"]
        context = state.get("context", ["[No context available.]"])
        skill_card = state.get("skill_card")
        domain_memory = state.get("domain_memory", []) or []

        try:
            self.logger.info("Generating expert answer", analyst=analyst.name)
            system_prompt = GENERATE_ANSWERS.render(
                goals=analyst.persona,
                context=context,
                skill_card=self._format_skill_card(skill_card),
                domain_memory=self._format_domain_memory(domain_memory),
            )
            started_at = time.perf_counter()
            answer = self.llm.invoke([SystemMessage(content=system_prompt)] + state["messages"])
            latency_ms = int((time.perf_counter() - started_at) * 1000)
            usage = self._extract_usage(answer)
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
    # 🔹 Step 4: Save interview transcript
    # ----------------------------------------------------------------------
    def _save_interview(self, state: InterviewState):
        """
        Save the entire conversation between the analyst and expert as a transcript.
        """
        try:
            messages = state["messages"]
            interview = get_buffer_string(messages)
            self.logger.info("Interview transcript saved", message_count=len(messages))
            return {"interview": interview}

        except Exception as e:
            self.logger.error("Error saving interview transcript", error=str(e))
            raise ResearchAnalystException("Failed to save interview transcript", e)

    # ----------------------------------------------------------------------
    # 🔹 Step 5: Write report section from interview context
    # ----------------------------------------------------------------------
    def _write_section(self, state: InterviewState):
        """
        Write a concise report section based on the interview and gathered context.
        """
        context = state.get("context", ["[No context available.]"])
        analyst = state["analyst"]
        skill_card = state.get("skill_card")
        assigned_plan = state.get("assigned_plan")

        try:
            self.logger.info("Generating report section", analyst=analyst.name)
            system_prompt = WRITE_SECTION.render(
                focus=analyst.description,
                skill_card=self._format_skill_card(skill_card),
                assigned_plan=self._format_assigned_plan(assigned_plan),
            )
            started_at = time.perf_counter()
            section = self.llm.invoke(
                [SystemMessage(content=system_prompt)]
                + [HumanMessage(content=f"Write this section using the following materials: {context}")]
            )
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

        except Exception as e:
            self.logger.error("Error writing report section", error=str(e))
            raise ResearchAnalystException("Failed to generate report section", e)

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
    # 🔹 Build Graph
    # ----------------------------------------------------------------------
    def build(self):
        """
        Construct and compile the LangGraph Interview workflow.
        """
        try:
            self.logger.info("Building Interview Graph workflow")
            builder = StateGraph(InterviewState)

            builder.add_node("ask_question", self._generate_question)
            builder.add_node("search_web", self._search_web)
            builder.add_node("generate_answer", self._generate_answer)
            builder.add_node("save_interview", self._save_interview)
            builder.add_node("write_section", self._write_section)
            builder.add_node("review_section", self._review_section)

            def _should_continue(state: InterviewState):
                max_turns = int(state.get("max_num_turns", 1) or 1)
                turn_count = int(state.get("turn_count", 0) or 0)
                return "ask_question" if turn_count < max_turns else "save_interview"

            builder.add_edge(START, "ask_question")
            builder.add_edge("ask_question", "search_web")
            builder.add_edge("search_web", "generate_answer")
            builder.add_conditional_edges(
                "generate_answer",
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
