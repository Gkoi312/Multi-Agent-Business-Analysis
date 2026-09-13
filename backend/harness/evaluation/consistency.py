"""
State Consistency Checks — pure-code rules validating Harness state integrity.

Runs against any InterviewState-like dict to verify that memory,
compression, and source tracking are internally consistent.

NO LLM needed. Designed for CI — every PR can run these.

Key fixes vs original:
- ``_check_knowledge_gaps_convergence`` now has real logic (was always-pass)
- ``_check_llm_metrics_present`` detects LLM events with empty metrics
- Result semantics: error-level violations → overall failed; warning-level → pass with warnings
- Output includes error_count, warning_count, passed_rules, failed_rules, warned_rules
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

# Known LLM-calling event types (used by llm_metrics check)
_LLM_CALLING_EVENTS = {
    "compress.completed",
    "interview.ask_question",
    "interview.generate_answer",
    "interview.search",
    "memory.update",
    "memory.reconcile",
    "llm.call",
    "llm.invoke",
    "search.summarize",
}


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ConsistencyResult:
    """Aggregated result from running all consistency checks."""
    passed: bool
    total_rules: int
    passed_rules: int
    failed_rules: int
    warned_rules: int = 0
    error_count: int = 0
    warning_count: int = 0
    violations: list[dict[str, Any]] = field(default_factory=list)
    # Each violation: {"rule": str, "severity": str, "detail": str}

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "total_rules": self.total_rules,
            "passed_rules": self.passed_rules,
            "failed_rules": self.failed_rules,
            "warned_rules": self.warned_rules,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "violations": self.violations,
        }


# ---------------------------------------------------------------------------
# Check function type and definition
# ---------------------------------------------------------------------------

CheckFn = Callable[[dict[str, Any]], tuple[bool, str]]
# Returns (passed: bool, detail: str)


def _check_turns_completed_matches_compressed(state: dict[str, Any]) -> tuple[bool, str]:
    """turns_completed must equal len(compressed_turns)."""
    wm = state.get("working_memory") or {}
    turns_completed = int(wm.get("turns_completed", 0) or 0)
    compressed = state.get("compressed_turns") or []
    actual = len(compressed)
    if turns_completed != actual:
        return False, (
            f"turns_completed={turns_completed} != len(compressed_turns)={actual}"
        )
    return True, ""


def _check_source_ids_sequential(state: dict[str, Any]) -> tuple[bool, str]:
    """All source_registry S-n keys must be sequential with no gaps."""
    registry = state.get("source_registry") or {}
    s_keys = sorted(
        [k for k in registry if str(k).startswith("S") and str(k)[1:].isdigit()],
        key=lambda k: int(str(k)[1:]),
    )
    if not s_keys:
        return True, ""
    for i, key in enumerate(s_keys, 1):
        expected = f"S{i}"
        if str(key) != expected:
            return False, (
                f"Source ID gap at position {i}: expected {expected}, got {key}"
            )
    return True, ""


def _check_fact_sources_in_registry(state: dict[str, Any]) -> tuple[bool, str]:
    """Every fact.source_ids entry must reference a key in source_registry."""
    registry = state.get("source_registry") or {}
    # Check from WorkingMemory active facts
    wm = state.get("working_memory") or {}
    facts = wm.get("facts") or []
    for f in facts:
        source_ids = f.get("source_ids") or []
        for sid in source_ids:
            if str(sid).startswith("http"):
                return False, (
                    f"Fact {f.get('fact_id', '?')} has URL as source_id: {sid}"
                )
            if str(sid) not in registry and not str(sid).startswith("http"):
                return False, (
                    f"Fact {f.get('fact_id', '?')} references source {sid} "
                    f"not in source_registry"
                )
    return True, ""


def _check_llm_metrics_present(state: dict[str, Any]) -> tuple[bool, str]:
    """LLM metrics must be present if LLM-calling events exist.

    If state has LLM-calling events but llm_metrics is empty or missing,
    that's a violation (warning severity).  If llm_metrics entries have
    zero tokens, that's also flagged.
    """
    metrics = state.get("llm_metrics") or []
    events = state.get("workflow_events") or []

    # Check if any LLM-calling events exist
    has_llm_events = any(
        str(e.get("event", "")).strip() in _LLM_CALLING_EVENTS
        for e in events
    )

    if has_llm_events and not metrics:
        return False, (
            "LLM-calling events detected in workflow_events "
            f"({[e.get('event') for e in events if e.get('event') in _LLM_CALLING_EVENTS]}) "
            "but llm_metrics is empty"
        )

    # If metrics exist, check for zero-token entries
    if not metrics:
        return True, "No LLM metrics recorded (may be early in graph)"

    for m in metrics:
        if int(m.get("total_tokens", 0) or 0) == 0:
            return False, (
                f"LLM metric for node '{m.get('node', '?')}' has zero tokens"
            )
    return True, ""


def _check_knowledge_gaps_convergence(state: dict[str, Any]) -> tuple[bool, str]:
    """Knowledge gaps should trend downward across turns (non-strict).

    Uses ``knowledge_gap_history`` if present (list of {turn, count}),
    otherwise falls back to ``working_memory.knowledge_gaps`` length.

    Checks:
    - Final count should not exceed initial count (trend check)
    - Consecutive large increases (>2x) trigger a warning
    - Occasional small increases are tolerated
    """
    gap_history = state.get("knowledge_gap_history") or []
    wm = state.get("working_memory") or {}
    gaps = wm.get("knowledge_gaps") or []

    # If no history, fall back to simple check on current gaps
    if not gap_history:
        if len(gaps) > 0:
            return True, f"{len(gaps)} knowledge gaps present (no history to check trend)"
        return True, "No knowledge gaps"

    # Parse history: expect list of {turn: int, count: int}
    counts: list[int] = []
    for entry in gap_history:
        if isinstance(entry, dict):
            cnt = entry.get("count", entry.get("gap_count", 0))
            try:
                counts.append(int(cnt))
            except (ValueError, TypeError):
                pass

    if len(counts) < 2:
        return True, f"Knowledge gap history: {counts} (too short for trend)"

    initial = counts[0]
    final = counts[-1]

    # Check: final should not be higher than initial
    if final > initial:
        # Check for consecutive large spikes
        for i in range(1, len(counts)):
            if counts[i] > counts[i - 1] * 2 and counts[i] > 2:
                return False, (
                    f"Knowledge gaps spiked from {counts[i-1]} to {counts[i]} "
                    f"at entry {i}. Trend: {counts}"
                )

        # Modest increase
        return True, (
            f"Knowledge gaps increased from {initial} to {final}. "
            f"Trend: {counts} — monitor for divergence"
        )

    # Decreasing or stable — good
    if final < initial:
        return True, (
            f"Knowledge gaps converging: {initial} → {final}. Trend: {counts}"
        )

    return True, f"Knowledge gaps stable at {final}. Trend: {counts}"


def _check_workflow_events_no_duplicates(state: dict[str, Any]) -> tuple[bool, str]:
    """Workflow events should not have duplicate (event_type, turn) pairs."""
    events = state.get("workflow_events") or []
    seen: set[tuple[str, int]] = set()
    for e in events:
        event_type = str(e.get("event", ""))
        payload = e.get("payload") or {}
        turn = int(payload.get("turn", 0) or 0)
        key = (event_type, turn)
        if key in seen:
            return False, f"Duplicate event: {event_type} at turn {turn}"
        seen.add(key)
    return True, ""


def _check_no_empty_compressed_turns(state: dict[str, Any]) -> tuple[bool, str]:
    """CompressedTurn entries should have at least one fact or an error message."""
    compressed = state.get("compressed_turns") or []
    for i, ct in enumerate(compressed):
        facts = ct.get("facts") or []
        key_findings = ct.get("key_findings") or []
        error = ct.get("compression_error") or ""
        if not facts and not key_findings and not error:
            return False, f"CompressedTurn[{i}] has no facts and no error"
    return True, ""


# ---------------------------------------------------------------------------
# Check registry
# ---------------------------------------------------------------------------

CONSISTENCY_CHECKS: list[dict[str, Any]] = [
    {
        "name": "turns_completed_matches_compressed",
        "description": "WorkingMemory.turns_completed equals len(compressed_turns)",
        "check": _check_turns_completed_matches_compressed,
        "severity": "error",
    },
    {
        "name": "source_ids_sequential",
        "description": "Source registry S-n keys are sequential with no gaps",
        "check": _check_source_ids_sequential,
        "severity": "error",
    },
    {
        "name": "fact_sources_in_registry",
        "description": "Every fact.source_ids entry exists in source_registry",
        "check": _check_fact_sources_in_registry,
        "severity": "error",
    },
    {
        "name": "llm_metrics_present",
        "description": "LLM metrics exist when LLM-calling events are present, and have non-zero tokens",
        "check": _check_llm_metrics_present,
        "severity": "warning",
    },
    {
        "name": "knowledge_gaps_convergence",
        "description": "Knowledge gaps should trend downward across research turns",
        "check": _check_knowledge_gaps_convergence,
        "severity": "warning",
    },
    {
        "name": "workflow_events_no_duplicates",
        "description": "Workflow events should not have duplicate (event_type, turn) pairs",
        "check": _check_workflow_events_no_duplicates,
        "severity": "warning",
    },
    {
        "name": "no_empty_compressed_turns",
        "description": "CompressedTurn entries must have facts or an error message",
        "check": _check_no_empty_compressed_turns,
        "severity": "error",
    },
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_consistency_checks(
    state: dict[str, Any],
    checks: list[dict[str, Any]] | None = None,
) -> ConsistencyResult:
    """Run all consistency checks against a state dict.

    Args:
        state: An InterviewState-like dict with working_memory, compressed_turns,
               source_registry, llm_metrics, workflow_events fields.
        checks: Optional override list of checks (defaults to CONSISTENCY_CHECKS).

    Returns:
        ConsistencyResult with pass/fail counts and violation details.

    Semantics:
    - **error**-severity violations → overall ``passed=False``
    - **warning**-severity violations → overall ``passed=True`` but warnings counted
    """
    rules = checks if checks is not None else CONSISTENCY_CHECKS
    violations: list[dict[str, Any]] = []
    passed_count = 0
    warned_count = 0
    failed_count = 0

    for rule in rules:
        try:
            ok, detail = rule["check"](state)
            severity = rule.get("severity", "error")

            if ok and not detail:
                passed_count += 1
            elif ok and detail:
                # Passed but with an informational detail message
                passed_count += 1
            else:
                violations.append({
                    "rule": rule["name"],
                    "severity": severity,
                    "detail": detail,
                })
                if severity == "error":
                    failed_count += 1
                else:
                    warned_count += 1
        except Exception as exc:
            violations.append({
                "rule": rule["name"],
                "severity": rule.get("severity", "error"),
                "detail": f"Check raised exception: {exc}",
            })
            if rule.get("severity", "error") == "error":
                failed_count += 1
            else:
                warned_count += 1

    total = len(rules)
    error_violations = [v for v in violations if v["severity"] == "error"]
    warning_violations = [v for v in violations if v["severity"] == "warning"]

    return ConsistencyResult(
        passed=len(error_violations) == 0,  # overall passed = no error-severity violations
        total_rules=total,
        passed_rules=passed_count,
        failed_rules=failed_count,
        warned_rules=warned_count,
        error_count=len(error_violations),
        warning_count=len(warning_violations),
        violations=violations,
    )
