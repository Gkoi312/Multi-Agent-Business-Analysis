"""
Unit tests for harness.tools — registry, pipeline, search adapters, cleaner stages.
"""
import pytest

from harness.tools.registry import ToolRegistry
from harness.tools.pipeline import ToolPipeline, ToolContext
from harness.tools.search.base import SearchQuery, SearchResult, SearchTool
from harness.tools.search.cleaner import (
    DeduplicateStage,
    CleanTextStage,
    RelevanceFilterStage,
    QualityFilterStage,
    StructureFactsStage,
    FormatDocumentStage,
    SEARCH_PIPELINE_BASIC,
    SEARCH_PIPELINE_FULL,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def registry():
    return ToolRegistry()


@pytest.fixture
def mock_tool():
    """A fake search tool that returns canned results."""

    class MockTool(SearchTool):
        name = "mock"

        def search(self, query: SearchQuery, **kwargs) -> list[SearchResult]:
            return [
                SearchResult(
                    url="https://a.com/1",
                    title="OpenAI GPT-5 Launch",
                    content="OpenAI announced GPT-5 with breakthrough reasoning performance. "
                    "The model scored 92% on MMLU benchmarks and cut inference costs by 40%. "
                    "Revenue grew to $5 billion in fiscal year 2025, driven by enterprise API adoption.",
                ),
                SearchResult(
                    url="https://a.com/1",  # duplicate URL
                    title="OpenAI GPT-5 Launch",
                    content="same url different content",  # shorter → should be dropped
                ),
                SearchResult(
                    url="https://b.com/2",
                    title="AI Market Growth 2025",
                    content="The AI market is projected to reach $500 billion by 2028. "
                    "Multiple companies including OpenAI, Google, and Anthropic are competing. "
                    "OpenAI maintains a strong lead in enterprise adoption and developer ecosystem.",
                ),
                SearchResult(
                    url="https://c.com/3",
                    title="Random Cat Video Goes Viral",
                    content="A video of a cat playing piano has garnered over 10 million views. "
                    "The feline sensation has captured hearts worldwide with its musical talent. "
                    "Social media users cannot get enough of this adorable performance.",
                ),
            ]

    return MockTool()


@pytest.fixture
def pipeline_context():
    return ToolContext(target_entity="OpenAI", source_type="web")


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------

class TestToolRegistry:
    def test_register_and_get_search(self, registry, mock_tool):
        registry.register_search(mock_tool)
        assert "mock" in registry.list_search()
        assert registry.get_search("mock") is mock_tool

    def test_get_missing_tool_returns_none(self, registry):
        assert registry.get_search("nonexistent") is None

    def test_get_best_search_fallback(self, registry, mock_tool):
        registry.register_search(mock_tool)
        assert registry.get_best_search() is mock_tool

    def test_list_search_sorted(self, registry):
        class ToolA(SearchTool):
            name = "alpha"
            def search(self, q, **kw): return []

        class ToolB(SearchTool):
            name = "beta"
            def search(self, q, **kw): return []

        registry.register_search(ToolB())
        registry.register_search(ToolA())
        assert registry.list_search() == ["alpha", "beta"]


# ---------------------------------------------------------------------------
# Cleaner stages
# ---------------------------------------------------------------------------

class TestDeduplicateStage:
    def test_removes_exact_url_duplicate(self):
        stage = DeduplicateStage()
        data = [
            {"url": "https://x.com/1", "title": "A", "content": "first"},
            {"url": "https://x.com/1", "title": "A", "content": "second"},
        ]
        result = stage(data, ToolContext())
        assert len(result) == 1

    def test_keeps_longer_on_near_duplicate_title(self):
        stage = DeduplicateStage()
        # Title2 is a near-subset of Title1 — only 1 extra word differs (Jaccard ≈ 10/11 ≈ 0.909)
        data = [
            {"url": "https://x.com/1", "title": "OpenAI announces massive new funding round led by Thrive Capital", "content": "long content " * 30},
            {"url": "https://x.com/2", "title": "OpenAI announces massive new funding round led by Thrive Capital news", "content": "short"},
        ]
        result = stage(data, ToolContext())
        assert len(result) == 1
        assert "long content" in result[0]["content"]

    def test_passes_unique_docs(self):
        stage = DeduplicateStage()
        data = [
            {"url": "https://a.com", "title": "Topic A", "content": "..."},
            {"url": "https://b.com", "title": "Topic B", "content": "..."},
        ]
        result = stage(data, ToolContext())
        assert len(result) == 2


class TestCleanTextStage:
    def test_strips_html(self):
        stage = CleanTextStage(min_content_length=1)
        data = [{"title": "<b>Test</b>", "content": "<p>Hello <em>world</em></p>" * 20}]
        result = stage(data, ToolContext())
        assert "<b>" not in result[0]["title"]
        assert "<p>" not in result[0]["content"]
        assert "Hello world" in result[0]["content"]

    def test_drops_short_content(self):
        stage = CleanTextStage(min_content_length=200)
        data = [{"title": "T", "content": "too short"}]
        result = stage(data, ToolContext())
        assert len(result) == 0


class TestRelevanceFilterStage:
    def test_keeps_docs_mentioning_target(self):
        stage = RelevanceFilterStage()
        data = [
            {"title": "OpenAI news", "content": "OpenAI is a leading AI company " * 10},
            {"title": "Unrelated", "content": "completely unrelated content " * 10},
        ]
        result = stage(data, ToolContext(target_entity="OpenAI"))
        assert len(result) == 1
        assert "OpenAI" in result[0]["title"]

    def test_passes_all_when_no_target(self):
        stage = RelevanceFilterStage()
        data = [
            {"title": "A", "content": "content " * 20},
            {"title": "B", "content": "content " * 20},
        ]
        result = stage(data, ToolContext())
        assert len(result) == 2


class TestQualityFilterStage:
    def test_keeps_content_with_facts(self):
        stage = QualityFilterStage()
        data = [{"url": "https://real.com", "title": "T", "content": "Revenue was $5 billion in 2025, up 40 percent."}]
        result = stage(data, ToolContext())
        assert len(result) == 1

    def test_drops_seo_fluff_without_facts(self):
        stage = QualityFilterStage()
        data = [{"url": "https://spam.io", "title": "T",
                 "content": "In recent years this company has grown. It is worth noting that more and more users like it. With the development of technology it continues to improve."}]
        result = stage(data, ToolContext())
        assert len(result) == 0

    def test_drops_known_spam_domain(self):
        # Temporarily add a pattern for testing
        import harness.tools.search.cleaner as cleaner_mod
        cleaner_mod._SPAM_DOMAIN_PATTERNS.append("bad-domain.com")
        try:
            stage = QualityFilterStage()
            data = [{"url": "https://bad-domain.com/page", "title": "T", "content": "Revenue $5 billion in 2025" * 5}]
            result = stage(data, ToolContext())
            assert len(result) == 0
        finally:
            cleaner_mod._SPAM_DOMAIN_PATTERNS.remove("bad-domain.com")


class TestStructureFactsStage:
    def test_extracts_numbers(self):
        stage = StructureFactsStage()
        data = [{"title": "T", "content": "Revenue was $5 billion in 2025, up 40%."}]
        result = stage(data, ToolContext())
        assert "structured" in result[0]
        assert any("$5 billion" in n for n in result[0]["structured"]["numbers"])

    def test_classifies_sentiment(self):
        stage = StructureFactsStage()
        pos = [{"title": "T", "content": "growth profit increase leader breakthrough opportunity " * 10}]
        neg = [{"title": "T", "content": "risk decline loss threat lawsuit investigation " * 10}]
        assert stage(pos, ToolContext())[0]["structured"]["sentiment"] == "positive"
        assert stage(neg, ToolContext())[0]["structured"]["sentiment"] == "negative"


class TestFormatDocumentStage:
    def test_produces_xml(self):
        stage = FormatDocumentStage()
        data = [{"url": "https://x.com", "title": "Test", "content": "Hello world", "structured": {"numbers": []}}]
        result = stage(data, ToolContext())
        assert "formatted" in result[0]
        assert "<Document" in result[0]["formatted"]
        assert "Hello world" in result[0]["formatted"]


# ---------------------------------------------------------------------------
# Full pipeline integration
# ---------------------------------------------------------------------------

class TestToolPipeline:
    def test_full_pipeline_end_to_end(self, mock_tool, pipeline_context):
        """Simulate a real search → pipeline flow."""
        query = SearchQuery(query="OpenAI strategy", source_type="web")
        raw = mock_tool.search(query)
        raw_dicts = [{"url": r.url, "title": r.title, "content": r.content} for r in raw]

        pipeline = ToolPipeline(SEARCH_PIPELINE_FULL)
        cleaned, trace = pipeline.run_with_trace(raw_dicts, pipeline_context)

        # Should keep 2: the OpenAI article + the AI market growth article
        # Should drop: duplicate URL + cat video (irrelevant)
        assert len(cleaned) == 2, f"Expected 2 after pipeline, got {len(cleaned)}"

        # Every result should have formatted output
        for doc in cleaned:
            assert "formatted" in doc
            assert "OpenAI" in doc["formatted"].lower() or "AI" in doc["title"]

        # Verify trace has the right stages
        stage_names = [t.stage for t in trace]
        assert stage_names == ["dedup", "clean_text", "relevance", "quality", "structure", "format"]

        # Dedup should have removed 1 (4→3)
        assert trace[0].input_count == 4
        assert trace[0].output_count == 3

    def test_basic_pipeline_no_filtering(self, mock_tool):
        """Basic pipeline skips relevance filter."""
        query = SearchQuery(query="OpenAI")
        raw = mock_tool.search(query)
        raw_dicts = [{"url": r.url, "title": r.title, "content": r.content} for r in raw]

        pipeline = ToolPipeline(SEARCH_PIPELINE_BASIC)
        cleaned, _ = pipeline.run_with_trace(raw_dicts, ToolContext())

        # BASIC = dedup + clean + format (no relevance filter)
        # dedup: 4→3, clean: all 3 survive (content > 100 chars)
        assert len(cleaned) == 3
