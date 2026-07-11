"""Quick test for llm_json helper."""
import json
from harness.utils.llm_json import _extract_json, _repair_json

# Test _extract_json
r1 = _extract_json('```json\n{"a": 1}\n```')
assert r1 == '{"a": 1}', f"got: {r1!r}"

r2 = _extract_json('{"a": 1}')
assert r2 == '{"a": 1}', f"got: {r2!r}"

r3 = _extract_json('```\n{"a": 1}\n```')
assert r3 == '{"a": 1}', f"got: {r3!r}"

# Test _repair_json
r4 = _repair_json('{"a": 1,}')
assert json.loads(r4) == {"a": 1}

r5 = _repair_json('{"a": 1, "b": [2,3,],}')
assert json.loads(r5) == {"a": 1, "b": [2, 3]}

from harness.models.agent import SearchQuery, Perspectives
print("SearchQuery schema keys:", list(SearchQuery.model_json_schema().get("properties", {}).keys()))
print("Perspectives schema keys:", list(Perspectives.model_json_schema().get("properties", {}).keys()))

print("OK - llm_json helper works")
