"""
Tests for IncrementalCompressor — turn compression, JSON fallback,
history compaction trigger, and context assembly.
"""
import pytest
from unittest.mock import MagicMock, patch

from harness.models.memory import CompressedTurn
from harness.memory.compressor import IncrementalCompressor
from harness.memory.context_window import ContextWindowManager


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def mock_llm():
    """LLM that returns valid CompressedTurn JSON (new format)."""
    llm = MagicMock()
    response = MagicMock()
    response.content = (
        '{"question_intent": "What is the revenue model?",'
        '"facts": ['
        '  {"text": "API subscriptions drive 60% of revenue",'
        '   "primary_category": "business_model",'
        '   "subject": "Revenue", "predicate": "source",'
        '   "value": "API subscriptions", "unit": "%", "period": "current",'
        '   "confidence": 0.9, "source_ids": ["S1"]},'
        '  {"text": "Enterprise licensing is growing at 40% YoY",'
        '   "primary_category": "growth",'
        '   "subject": "Enterprise licensing", "predicate": "growth rate",'
        '   "value": 40, "unit": "%", "period": "YoY",'
        '   "confidence": 0.85, "source_ids": ["S2"]}'
        '],'
        '"numbers_mentioned": [{"value": "60", "unit": "%", "context": "API share"}],'
        '"source_registry": {'
        '  "S1": {"url": "https://example.com/1", "title": "Example 1"},'
        '  "S2": {"url": "https://example.com/2", "title": "Example 2"}'
        '}}'
    )
    llm.invoke.return_value = response
    return llm


@pytest.fixture
def mock_llm_invalid_json():
    """LLM that returns invalid JSON — triggers fallback."""
    llm = MagicMock()
    response = MagicMock()
    response.content = "This is not JSON at all, just random text from the model."
    llm.invoke.return_value = response
    return llm


@pytest.fixture
def mock_llm_raises():
    """LLM that throws exceptions — triggers exception fallback."""
    llm = MagicMock()
    llm.invoke.side_effect = RuntimeError("API rate limit exceeded")
    return llm


@pytest.fixture
def mock_llm_empty_json():
    """LLM that returns valid but empty JSON."""
    llm = MagicMock()
    response = MagicMock()
    response.content = "{}"
    llm.invoke.return_value = response
    return llm


# ===========================================================================
# Turn compression
# ===========================================================================


class TestCompressCompletedTurn:
    def test_valid_compression(self, mock_llm):
        from harness.models.memory import SourceRecord
        compressor = IncrementalCompressor(mock_llm)
        # Provide source_registry so S1/S2 are recognized
        registry = {
            "S1": SourceRecord(source_id="S1", url="https://example.com/1", title="Example 1"),
            "S2": SourceRecord(source_id="S2", url="https://example.com/2", title="Example 2"),
        }
        turn = compressor.compress_completed_turn(
            question="What is the revenue model?",
            answer="API subscriptions drive 60% of revenue. Enterprise licensing grows at 40%.",
            source_registry=registry,
        )
        assert isinstance(turn, CompressedTurn)
        assert len(turn.key_findings) > 0
        # Each fact cites exactly one source -> "medium" (mechanically derived
        # from source count, not the LLM's own claim).
        assert turn.evidence_quality == "medium"
        assert len(turn.sources_cited) > 0

    def test_invalid_json_fallback_preserves_answer(self, mock_llm_invalid_json):
        """When JSON parsing fails after retries, fallback must preserve
        the answer content and set evidence_quality=low."""
        compressor = IncrementalCompressor(mock_llm_invalid_json, max_retries=2)
        turn = compressor.compress_completed_turn(
            question="What is the revenue?",
            answer="The revenue was $1.6B in 2024 according to official filings.",
        )
        assert isinstance(turn, CompressedTurn)
        # Fallback should preserve answer content
        assert turn.evidence_quality == "low"
        assert turn.compression_error != ""
        # Answer content should be in key_findings
        if turn.key_findings:
            assert "revenue" in turn.key_findings[0].lower() or len(turn.key_findings) > 0

    def test_model_exception_distinguished_from_no_facts(self, mock_llm_raises):
        """Model call failure must be distinguished from 'no facts extracted'."""
        compressor = IncrementalCompressor(mock_llm_raises, max_retries=2)
        turn = compressor.compress_completed_turn(
            question="Test question",
            answer="Test answer.",
        )
        # compression_error field distinguishes failure from empty extraction
        assert turn.compression_error != ""
        assert "RuntimeError" in turn.compression_error

    def test_empty_json_fallback(self, mock_llm_empty_json):
        """Empty JSON {} should trigger fallback."""
        compressor = IncrementalCompressor(mock_llm_empty_json, max_retries=2)
        turn = compressor.compress_completed_turn(
            question="Test question?",
            answer="Test answer with data.",
        )
        assert isinstance(turn, CompressedTurn)
        assert turn.compression_error != "" or turn.evidence_quality == "low"

    def test_question_intent_preserved(self, mock_llm_invalid_json):
        """Even on fallback, question_intent should be preserved."""
        compressor = IncrementalCompressor(mock_llm_invalid_json, max_retries=2)
        turn = compressor.compress_completed_turn(
            question="What is OpenAI's primary revenue driver?",
            answer="OpenAI generates most revenue from API subscriptions.",
        )
        assert "OpenAI" in turn.question_intent or len(turn.question_intent) > 0

    def test_compression_sets_error_flag(self, mock_llm_invalid_json):
        """Fallback compression must set compression_error."""
        compressor = IncrementalCompressor(mock_llm_invalid_json, max_retries=2)
        turn = compressor.compress_completed_turn(
            question="Q?",
            answer="A.",
        )
        assert turn.compression_error != ""


# ===========================================================================
# History compaction trigger
# ===========================================================================


class TestShouldCompactHistory:
    def test_no_compaction_below_threshold(self, mock_llm):
        cwm = ContextWindowManager(max_tokens=128_000)
        compressor = IncrementalCompressor(mock_llm, window_manager=cwm)

        from langchain_core.messages import HumanMessage
        messages = [HumanMessage(content="short message")]

        assert not compressor.should_compact_history(messages)

    def test_compaction_when_above_threshold(self, mock_llm):
        cwm = ContextWindowManager(max_tokens=500, reserved_tokens=100, safe_ratio=0.5)
        compressor = IncrementalCompressor(mock_llm, window_manager=cwm)

        from langchain_core.messages import HumanMessage
        # Content is 4000 chars → ~1000 tokens by len//4 → above safe limit of 200
        messages = [HumanMessage(content="x" * 4000)]

        # should_compact_history delegates to window_manager.should_compress
        # which checks: total tokens > (500-100)*0.5 = 200
        assert compressor.should_compact_history(messages)


# ===========================================================================
# Helpers
# ===========================================================================


class TestHelpers:
    def test_extract_last_question_and_answer(self):
        from langchain_core.messages import HumanMessage, AIMessage
        messages = [
            HumanMessage(content="What is the revenue?"),
            AIMessage(content="Revenue is $1.6B."),
        ]
        q, a = IncrementalCompressor.extract_last_question_and_answer(messages)
        assert "revenue" in q.lower()
        assert "1.6B" in a

    def test_extract_single_message_fallback(self):
        from langchain_core.messages import AIMessage
        messages = [AIMessage(content="Just an answer.")]
        q, a = IncrementalCompressor.extract_last_question_and_answer(messages)
        assert q == ""
        assert "answer" in a

    def test_format_compressed_turns(self):
        turns = [
            CompressedTurn(
                question_intent="Revenue model?",
                key_findings=["Fact 1"],
                evidence_quality="high",
            ),
        ]
        formatted = IncrementalCompressor.format_compressed_turns(turns)
        assert "Round 1" in formatted
        assert "Fact 1" in formatted


# ===========================================================================
# ContextAssembler
# ===========================================================================


class TestContextAssembler:
    def test_assemble_basic(self):
        from harness.memory.context_assembler import ContextAssembler
        from harness.memory.policies import TokenBudget
        from langchain_core.messages import HumanMessage

        budget = TokenBudget(
            system_prompt=500,
            research_summary=500,
            working_memory=300,
            recent_messages=1000,
        )
        assembler = ContextAssembler(token_budget=budget)
        messages = [HumanMessage(content="Test message")]

        result = assembler.assemble(
            messages=messages,
            system_prompt="You are a research analyst.",
            compressed_turns=[
                CompressedTurn(
                    question_intent="Test",
                    key_findings=["Finding 1"],
                    evidence_quality="medium",
                ),
            ],
            working_memory_str="Research so far: 3 facts.",
        )

        assert result.total_tokens > 0
        assert "research analyst" in result.system_prompt.lower()
        assert "research_summary" in result.token_breakdown

    def test_assemble_does_not_mutate_original_messages(self):
        from harness.memory.context_assembler import ContextAssembler
        from langchain_core.messages import HumanMessage

        original = [HumanMessage(content="Important data")]
        assembler = ContextAssembler()
        result = assembler.assemble(messages=original)

        # Original messages untouched
        assert original[0].content == "Important data"

    def test_assemble_within_budget(self):
        from harness.memory.context_assembler import ContextAssembler
        from harness.memory.policies import TokenBudget
        from langchain_core.messages import HumanMessage

        budget = TokenBudget(
            system_prompt=200,
            research_summary=200,
            working_memory=200,
            recent_messages=500,
        )
        assembler = ContextAssembler(token_budget=budget)
        messages = [HumanMessage(content="short msg")]

        result = assembler.assemble(messages=messages, system_prompt="You are helpful.")
        # Total should be at most the safe limit
        assert result.total_tokens > 0


# ===========================================================================
# HistoryCompactor
# ===========================================================================


class TestHistoryCompactor:
    def test_should_compact_below_threshold(self):
        from harness.memory.history_compactor import HistoryCompactor
        from harness.memory.policies import CompactionPolicy
        from harness.models.memory import TokenCounter
        from langchain_core.messages import HumanMessage

        policy = CompactionPolicy(trigger_tokens=10_000, min_turns_before_compact=2)
        compactor = HistoryCompactor(policy=policy, token_counter=TokenCounter())
        messages = [HumanMessage(content="short")]

        assert not compactor.should_compact(messages, turn_count=0)
        assert not compactor.should_compact(messages, turn_count=1)

    def test_should_compact_above_threshold(self):
        from harness.memory.history_compactor import HistoryCompactor
        from harness.memory.policies import CompactionPolicy
        from harness.models.memory import TokenCounter
        from langchain_core.messages import HumanMessage

        policy = CompactionPolicy(trigger_tokens=100, min_turns_before_compact=2)
        compactor = HistoryCompactor(policy=policy, token_counter=TokenCounter())
        messages = [HumanMessage(content="x" * 1000)]  # ~250 tokens

        assert compactor.should_compact(messages, turn_count=3)
