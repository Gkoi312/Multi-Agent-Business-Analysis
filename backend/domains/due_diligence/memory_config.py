"""
Due Diligence domain-specific memory configuration.

Injected into Harness components so they remain domain-agnostic.
"""
from harness.memory.policies import MemoryDomainConfig
from harness.models.memory import CoveragePolicy


DUE_DILIGENCE_MEMORY_CONFIG = MemoryDomainConfig(
    categories=("business_model", "growth", "risk", "competition", "financials"),
    category_descriptions={
        "business_model": "Revenue model, pricing, monetization, customer segments",
        "growth": "Growth rate, market share, expansion, traction",
        "risk": "Regulatory, compliance, legal, operational, financial risks",
        "competition": "Competitive landscape, moat, differentiation, positioning",
        "financials": "Revenue, profit, margins, cash flow, valuation",
    },
    coverage_policy=CoveragePolicy(
        required_for_full_report={
            "business_model": 3,
            "growth": 3,
            "risk": 3,
            "competition": 2,
            "financials": 2,
        },
        required_for_early_stop={
            "business_model": 2,
            "growth": 2,
            "risk": 2,
            "competition": 1,
            "financials": 1,
        },
        minimum_evidence_quality="medium",
        min_independent_sources=2,
    ),
    predicate_aliases={
        "revenue": {"revenue", "revenue amount", "annual revenue", "营收", "收入"},
        "employee_count": {"employees", "headcount", "staff count", "员工数量", "员工"},
        "growth_rate": {"growth", "growth rate", "yoy growth", "增速", "增长率"},
        "market_share": {"market share", "市场份额", "share", "份额"},
        "profit": {"profit", "net income", "net profit", "利润", "净利润"},
        "valuation": {"valuation", "market cap", "估值", "市值"},
        "founded": {"founded", "established", "成立", "创立"},
    },
    fallback_category="other",
)
