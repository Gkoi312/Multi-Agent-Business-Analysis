from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.messages import get_buffer_string
import time

from app.schemas.models import InterviewState, SearchQuery
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
        messages = state["messages"]

        try:
            self.logger.info("Generating analyst question", analyst=analyst.name)
            system_prompt = ANALYST_ASK_QUESTIONS.render(goals=analyst.persona)
            started_at = time.perf_counter()
            question = self.llm.invoke([SystemMessage(content=system_prompt)] + messages)
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
            structure_llm = self.llm.with_structured_output(SearchQuery)
            search_prompt = GENERATE_SEARCH_QUERY.render()
            started_at = time.perf_counter()
            search_query = structure_llm.invoke([SystemMessage(content=search_prompt)] + state["messages"])
            latency_ms = int((time.perf_counter() - started_at) * 1000)
            usage = self._extract_usage(search_query)

            self.logger.info("Performing Tavily web search", query=search_query.search_query)
            search_docs = self.tavily_search.invoke(search_query.search_query)

            if not search_docs:
                self.logger.warning("No search results found")
                return {
                    "context": ["[No search results found.]"],
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
            for doc in search_docs:
                if isinstance(doc, dict):
                    href = doc.get("url", "#")
                    content = doc.get("content", "")
                else:
                    # Tavily can return plain text entries in some modes.
                    href = "#"
                    content = str(doc)
                formatted_docs.append(f'<Document href="{href}"/>\n{content}\n</Document>')
            formatted = "\n\n---\n\n".join(formatted_docs)
            self.logger.info("Web search completed", result_count=len(search_docs))
            return {
                "context": [formatted],
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
        messages = state["messages"]
        context = state.get("context", ["[No context available.]"])

        try:
            self.logger.info("Generating expert answer", analyst=analyst.name)
            system_prompt = GENERATE_ANSWERS.render(goals=analyst.persona, context=context)
            started_at = time.perf_counter()
            answer = self.llm.invoke([SystemMessage(content=system_prompt)] + messages)
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

        try:
            self.logger.info("Generating report section", analyst=analyst.name)
            system_prompt = WRITE_SECTION.render(focus=analyst.description)
            started_at = time.perf_counter()
            section = self.llm.invoke(
                [SystemMessage(content=system_prompt)]
                + [HumanMessage(content=f"Use this source to write your section: {context}")]
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
            builder.add_edge("write_section", END)

            graph = builder.compile(checkpointer=self.memory)
            self.logger.info("Interview Graph compiled successfully")
            return graph

        except Exception as e:
            self.logger.error("Error building interview graph", error=str(e))
            raise ResearchAnalystException("Failed to build interview graph workflow", e)
