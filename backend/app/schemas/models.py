# models.py
import operator
from typing import Annotated, List
from langgraph.graph import MessagesState
from pydantic import BaseModel, Field
from typing_extensions import TypedDict


def keep_latest(_, new):
    """Reducer for scalar state keys updated by parallel branches."""
    return new

# -------------------------------
# Analyst Models
# -------------------------------

class Analyst(BaseModel):
    affiliation: str = Field(description="分析师的主要所属机构或身份背景。")
    name: str = Field(description="分析师姓名。")
    role: str = Field(description="分析师在当前研究任务中的角色定位。")
    description: str = Field(
        description="分析师的关注重点、核心顾虑与研究动机说明。"
    )

    @property
    def persona(self) -> str:
        return (
            f"姓名：{self.name}\n"
            f"角色：{self.role}\n"
            f"所属：{self.affiliation}\n"
            f"描述：{self.description}\n"
        )

class Perspectives(BaseModel):
    analysts: List[Analyst] = Field(
        description="包含分析师姓名、角色、所属和描述的完整分析师列表。"
    )

# -------------------------------
# Search Query Output Parser
# -------------------------------

class SearchQuery(BaseModel):
    search_query: str = Field(None, description="用于检索或网页搜索的查询语句。")

# -------------------------------
# State Classes for Graphs
# -------------------------------

class GenerateAnalystsState(TypedDict):
    research_query: str  # Research brief/query for due diligence
    company_name: str  # Target company name for due diligence
    max_analysts: int  # Number of analysts to generate
    human_analyst_feedback: str  # Feedback from human
    analysts: List[Analyst]  # List of analysts generated

class InterviewState(MessagesState):
    max_num_turns: int  # Max interview turns allowed
    turn_count: int  # Current interview turns completed
    context: Annotated[list, operator.add]  # Retrieved or searched context
    analyst: Analyst  # Analyst conducting interview
    interview: str  # Full interview transcript
    sections: list  # Generated section from interview
    llm_metrics: Annotated[list, operator.add]  # Per-node llm usage metrics

class ResearchGraphState(TypedDict):
    research_query: str  # Research brief/query for due diligence
    company_name: str  # Target company name for due diligence
    max_analysts: int  # Number of analysts
    max_num_turns: Annotated[int, keep_latest]  # Interview turns per analyst
    human_analyst_feedback: str  # Optional human feedback
    analysts: List[Analyst]  # All analysts involved
    sections: Annotated[list, operator.add]  # All interview-generated sections
    introduction: str  # Introduction of final report
    content: str  # Main content of report
    conclusion: str  # Conclusion of final report
    final_report: str  # Compiled report string
    llm_metrics: Annotated[list, operator.add]  # All LLM call metrics across graph