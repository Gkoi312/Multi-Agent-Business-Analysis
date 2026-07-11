"""
LLM JSON helpers — structured output without requiring ``response_format`` support.

DeepSeek and some other providers don't support OpenAI's ``response_format``
(used by LangChain's ``with_structured_output``).  This module provides a
fallback:  ask the model to return JSON, then parse into a Pydantic model.
"""
from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import BaseMessage, SystemMessage
from pydantic import BaseModel


def invoke_as_json(
    llm: Any,
    messages: list[BaseMessage],
    model_cls: type[BaseModel],
) -> tuple[BaseModel, dict[str, int]]:
    """Invoke the LLM and parse its response as JSON into *model_cls*.

    Adds a ``SystemMessage`` instructing the model to return a JSON object
    matching the schema of *model_cls*.

    Parameters
    ----------
    llm:
        A LangChain chat model.
    messages:
        The message list to send (without the JSON instruction).
    model_cls:
        The Pydantic model to parse into.

    Returns
    -------
    tuple[model_cls, dict[str, int]]
        The parsed model instance AND a usage dict with keys:
        ``prompt_tokens``, ``completion_tokens``, ``total_tokens``.

    Raises
    ------
    ValueError
        If the response cannot be parsed as JSON or validated.
    """
    schema = model_cls.model_json_schema()
    schema_json = json.dumps(schema, ensure_ascii=False, indent=2)

    instruction = SystemMessage(content=(
        "You MUST respond with ONLY a valid JSON object. No markdown, no explanation.\n"
        "The JSON must conform to this schema:\n"
        f"```json\n{schema_json}\n```\n"
        "Output ONLY the JSON object, nothing else."
    ))

    response = llm.invoke([instruction] + list(messages))
    text = _extract_json(response.content if hasattr(response, "content") else str(response))

    # Extract token usage from response metadata
    usage = _extract_usage(response)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        text = _repair_json(text)
        data = json.loads(text)

    return model_cls.model_validate(data), usage


def _extract_usage(message: Any) -> dict[str, int]:
    """Extract token usage from a LangChain AIMessage."""
    usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    response_meta = getattr(message, "response_metadata", {}) or {}
    usage_meta = getattr(message, "usage_metadata", {}) or {}
    token_usage = response_meta.get("token_usage", {}) if isinstance(response_meta, dict) else {}

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


def _extract_json(text: str) -> str:
    """Extract JSON from LLM response, stripping markdown fences."""
    text = text.strip()
    # Remove markdown code fences
    if text.startswith("```"):
        # Find the first newline after opening fence
        nl = text.find("\n")
        if nl >= 0:
            text = text[nl + 1:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    # Remove any leading "json" tag
    if text.startswith("json\n"):
        text = text[5:]
    return text


def _repair_json(text: str) -> str:
    """Attempt to repair common JSON syntax errors from LLM output."""
    # Remove trailing commas before } or ]
    text = re.sub(r",\s*([}\]])", r"\1", text)
    # Remove comment lines (// ...)
    text = re.sub(r"//[^\n]*", "", text)
    return text
