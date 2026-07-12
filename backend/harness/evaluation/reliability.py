"""
Reliability Report — two-level CV/σ analysis across repeated eval runs.

Key fixes:
- Single-run cases are NOT marked stable; they are N/A
- within_case_std computed whenever total_runs > n_cases (not just n_cases>1)
- pass_consistency renamed to status_consistency (old name kept as deprecated alias)
- repeatability_status uses only cases with n_repeats>=2
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Any

from harness.evaluation.runner import EvalRunResult

_REPEAT_SUFFIX_RE = re.compile(r"_(?:r|run)\d+$", re.IGNORECASE)


def _base_case_id(case_id: str) -> str:
    return _REPEAT_SUFFIX_RE.sub("", case_id)


@dataclass
class CaseStats:
    """Repeat reliability for a single case within one dimension."""
    dimension: str
    base_case_id: str
    n_repeats: int
    mean: float
    std: float
    cv: float
    min_score: float
    max_score: float
    range_score: float
    status_consistency: float       # fraction of runs with same status (renamed)
    pass_consistency: float         # DEPRECATED alias for status_consistency
    status_distribution: dict[str, int]
    repeatability_eligible: bool = True  # n_repeats >= 2

    @property
    def stable(self) -> bool:
        return self.repeatability_eligible and self.cv < 0.10

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension, "base_case_id": self.base_case_id,
            "n_repeats": self.n_repeats, "mean": self.mean, "std": self.std,
            "cv": self.cv, "min_score": self.min_score, "max_score": self.max_score,
            "range": self.range_score,
            "status_consistency": self.status_consistency,
            "pass_consistency": self.pass_consistency,  # deprecated
            "status_distribution": self.status_distribution,
            "stable": self.stable,
            "repeatability_eligible": self.repeatability_eligible,
        }


@dataclass
class DimensionStats:
    dimension: str
    scorer_type: str = "unknown"
    n_cases: int = 0
    n_total_runs: int = 0
    n_eligible_runs: int = 0
    n_skipped_runs: int = 0
    macro_mean: float = 0.0
    micro_mean: float = 0.0
    between_case_std: float = 0.0
    within_case_std: float | None = None  # None when not computable
    mean_within_case_cv: float | None = None
    max_within_case_range: float = 0.0
    stable_case_rate: float | None = None
    pass_rate: float = 0.0
    partial_rate: float = 0.0
    fail_rate: float = 0.0
    performance_status: str = "N/A"
    repeatability_status: str = "N/A"
    repeatability_reason: str = ""
    case_stats: list[CaseStats] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension, "scorer_type": self.scorer_type,
            "n_cases": self.n_cases, "n_total_runs": self.n_total_runs,
            "n_eligible_runs": self.n_eligible_runs, "n_skipped_runs": self.n_skipped_runs,
            "macro_mean": self.macro_mean, "micro_mean": self.micro_mean,
            "between_case_std": self.between_case_std,
            "within_case_std": self.within_case_std,
            "mean_within_case_cv": self.mean_within_case_cv,
            "max_within_case_range": self.max_within_case_range,
            "stable_case_rate": self.stable_case_rate,
            "pass_rate": self.pass_rate, "partial_rate": self.partial_rate,
            "fail_rate": self.fail_rate,
            "performance_status": self.performance_status,
            "repeatability_status": self.repeatability_status,
            "repeatability_reason": self.repeatability_reason,
            "case_stats": [cs.to_dict() for cs in self.case_stats],
            "limitations": self.limitations,
        }


@dataclass
class ReliabilityReport:
    dimension_stats: list[DimensionStats] = field(default_factory=list)
    total_runs: int = 0
    total_cases: int = 0
    total_eligible_runs: int = 0

    @classmethod
    def from_runs(cls, results: list[EvalRunResult],
                  scorer_types: dict[str, str] | None = None) -> "ReliabilityReport":
        scorer_types = scorer_types or {}
        by_case: dict[str, dict[str, list[dict[str, Any]]]] = {}

        for run in results:
            base_cid = _base_case_id(run.case_id)
            for score in run.scores:
                dim = score.dimension
                eligible = score.eligible if hasattr(score, 'eligible') else (score.status != "skipped")
                by_case.setdefault(dim, {}).setdefault(base_cid, []).append({
                    "normalized": score.normalized, "status": score.status, "eligible": eligible,
                })

        all_dim_stats: list[DimensionStats] = []
        for dim in sorted(by_case.keys()):
            dim_cases = by_case[dim]
            case_stats_list: list[CaseStats] = []
            all_eligible_values: list[float] = []
            case_means: list[float] = []
            repeatable_case_cvs: list[float] = []
            repeatable_case_ranges: list[float] = []
            stable_count = 0; repeatable_case_count = 0

            total_runs_for_dim = 0; eligible_runs_for_dim = 0; skipped_runs_for_dim = 0
            pass_count = 0; partial_count = 0; fail_count = 0

            for base_cid in sorted(dim_cases.keys()):
                runs = dim_cases[base_cid]
                eligible_runs = [r for r in runs if r["eligible"]]
                n_total = len(runs); n_eligible = len(eligible_runs); n_skipped = n_total - n_eligible
                total_runs_for_dim += n_total; eligible_runs_for_dim += n_eligible
                skipped_runs_for_dim += n_skipped

                status_dist: dict[str, int] = {}
                for r in runs:
                    st = r["status"]; status_dist[st] = status_dist.get(st, 0) + 1
                    if r["eligible"]:
                        if st == "pass": pass_count += 1
                        elif st == "partial": partial_count += 1
                        elif st == "fail": fail_count += 1

                if n_eligible < 1: continue

                values = [r["normalized"] for r in eligible_runs]
                all_eligible_values.extend(values)

                if n_eligible == 1:
                    mean_val, std_val, cv_val, range_val = values[0], 0.0, 0.0, 0.0
                    repeat_eligible = False
                else:
                    mean_val = statistics.mean(values)
                    std_val = statistics.stdev(values)
                    cv_val = round(std_val / mean_val, 4) if mean_val > 0 else 0.0
                    range_val = max(values) - min(values)
                    repeat_eligible = True
                    repeatable_case_count += 1
                    repeatable_case_cvs.append(cv_val)
                    repeatable_case_ranges.append(range_val)
                    if cv_val < 0.10: stable_count += 1
                    case_cvs = repeatable_case_cvs  # keep reference for later

                max_status_count = max(status_dist.values()) if status_dist else 0
                consistency = max_status_count / n_total if n_total > 0 else 1.0

                cs = CaseStats(
                    dimension=dim, base_case_id=base_cid, n_repeats=n_eligible,
                    mean=round(mean_val, 4), std=round(std_val, 4), cv=cv_val,
                    min_score=round(min(values), 4), max_score=round(max(values), 4),
                    range_score=round(range_val, 4),
                    status_consistency=round(consistency, 4),
                    pass_consistency=round(consistency, 4),  # deprecated alias
                    status_distribution=status_dist,
                    repeatability_eligible=repeat_eligible,
                )
                case_stats_list.append(cs)
                case_means.append(mean_val)

            n_cases_with_data = len(case_means)
            if n_cases_with_data == 0:
                all_dim_stats.append(DimensionStats(
                    dimension=dim, scorer_type=scorer_types.get(dim, "unknown"),
                    n_cases=len(dim_cases), n_total_runs=total_runs_for_dim,
                    n_eligible_runs=0, n_skipped_runs=skipped_runs_for_dim,
                    performance_status="N/A", repeatability_status="N/A",
                    case_stats=case_stats_list))
                continue

            macro_mean = round(statistics.mean(case_means), 4) if case_means else 0.0
            micro_mean = round(statistics.mean(all_eligible_values), 4) if all_eligible_values else 0.0
            between_std = round(statistics.stdev(case_means), 4) if len(case_means) > 1 else 0.0

            # within_case_std: compute whenever total runs > number of cases
            # (fixed: was only computing when n_cases>1, now works for 1 case with 3 repeats)
            if eligible_runs_for_dim > n_cases_with_data:
                pooled_var = 0.0; dof = 0
                for cs in case_stats_list:
                    if cs.n_repeats > 1:
                        pooled_var += (cs.n_repeats - 1) * (cs.std ** 2)
                        dof += cs.n_repeats - 1
                within_std = round((pooled_var / dof) ** 0.5, 4) if dof > 0 and pooled_var > 0 else 0.0
            else:
                within_std = None

            # Repeatability stats (only from cases with n_repeats>=2)
            if repeatable_case_count > 0:
                mean_cv = round(statistics.mean(repeatable_case_cvs), 4)
                max_range = round(max(repeatable_case_ranges), 4)
                stable_rate = round(stable_count / repeatable_case_count, 4)
            else:
                mean_cv = None; max_range = 0.0; stable_rate = None

            pass_rate = round(pass_count / eligible_runs_for_dim, 4) if eligible_runs_for_dim > 0 else 0.0
            partial_rate = round(partial_count / eligible_runs_for_dim, 4) if eligible_runs_for_dim > 0 else 0.0
            fail_rate = round(fail_count / eligible_runs_for_dim, 4) if eligible_runs_for_dim > 0 else 0.0

            # Performance status
            if pass_rate >= 0.90: perf_status = "PASS"
            elif pass_rate >= 0.50: perf_status = "MIXED"
            else: perf_status = "FAIL"

            # Repeatability status
            st = scorer_types.get(dim, "unknown")
            if st == "deterministic":
                rep_status = "DETERMINISTIC"; rep_reason = "Deterministic scorer — CV=0 is expected"
            elif repeatable_case_count == 0:
                rep_status = "N/A"; rep_reason = "No case has at least two eligible repeats"
            elif stable_rate is not None and stable_rate >= 0.80:
                rep_status = "STABLE"; rep_reason = f"{stable_count}/{repeatable_case_count} repeatable cases stable"
            elif stable_rate is not None and stable_rate >= 0.50:
                rep_status = "MIXED"; rep_reason = f"Only {stable_count}/{repeatable_case_count} cases stable"
            else:
                rep_status = "VOLATILE"; rep_reason = "Most repeated cases show high variance"

            limitations: list[str] = []
            if eligible_runs_for_dim < 5:
                limitations.append(f"Small sample: {eligible_runs_for_dim} eligible run(s) across {n_cases_with_data} case(s)")
            if repeatable_case_count == 0:
                limitations.append("No repeatable cases — run with --repeats >= 2 for stability analysis")
            if st == "llm_judged" and rep_status == "STABLE":
                limitations.append("LLM-judged scorer stable — positive but may not generalize")

            all_dim_stats.append(DimensionStats(
                dimension=dim, scorer_type=st, n_cases=n_cases_with_data,
                n_total_runs=total_runs_for_dim, n_eligible_runs=eligible_runs_for_dim,
                n_skipped_runs=skipped_runs_for_dim,
                macro_mean=macro_mean, micro_mean=micro_mean,
                between_case_std=between_std, within_case_std=within_std,
                mean_within_case_cv=mean_cv, max_within_case_range=max_range,
                stable_case_rate=stable_rate,
                pass_rate=pass_rate, partial_rate=partial_rate, fail_rate=fail_rate,
                performance_status=perf_status, repeatability_status=rep_status,
                repeatability_reason=rep_reason,
                case_stats=case_stats_list, limitations=limitations,
            ))

        all_case_ids = set(); total_runs_all = 0; total_eligible = 0
        for run in results:
            all_case_ids.add(_base_case_id(run.case_id)); total_runs_all += 1
            for score in run.scores:
                eligible = score.eligible if hasattr(score, 'eligible') else (score.status != "skipped")
                if eligible: total_eligible += 1; break

        return cls(dimension_stats=all_dim_stats, total_runs=total_runs_all,
                  total_cases=len(all_case_ids), total_eligible_runs=total_eligible)

    def format_markdown(self, title: str = "Harness Evaluation Reliability Report") -> str:
        lines = [f"# {title}", "",
                f"**Total cases:** {self.total_cases}  |  "
                f"**Total runs:** {self.total_runs}  |  "
                f"**Total eligible runs:** {self.total_eligible_runs}", "",
                "> **Performance** = quality thresholds met. **Repeatability** = consistency across repeats.", ""]
        for ds in self.dimension_stats:
            lines.append(f"## {ds.dimension}")
            lines.append(f"**Performance:** {ds.performance_status}  |  "
                        f"**Repeatability:** {ds.repeatability_status}  |  "
                        f"**Scorer type:** {ds.scorer_type}")
            if ds.repeatability_reason: lines.append(f"  *{ds.repeatability_reason}*")
            lines.append("")
            lines.append(f"**Cases:** {ds.n_cases} | **Runs:** {ds.n_total_runs} | "
                        f"**Eligible:** {ds.n_eligible_runs} | **Skipped:** {ds.n_skipped_runs}")
            lines.extend(["", "| Metric | Value |", "|--------|-------|",
                         f"| Macro Mean | {ds.macro_mean:.4f} |",
                         f"| Micro Mean | {ds.micro_mean:.4f} |",
                         f"| Between-Case Std | {ds.between_case_std:.4f} |",
                         f"| Within-Case Std | {ds.within_case_std if ds.within_case_std is not None else 'N/A'} |",
                         f"| Mean Within-Case CV | {ds.mean_within_case_cv if ds.mean_within_case_cv is not None else 'N/A'} |",
                         f"| Stable Case Rate | {ds.stable_case_rate if ds.stable_case_rate is not None else 'N/A'} |",
                         f"| Pass Rate | {ds.pass_rate:.0%} |", ""])
            if ds.case_stats:
                lines.extend(["### Per-Case", "",
                    "| Case | Runs | Mean | Std | CV | Range | Status Consist | Status Dist |",
                    "|------|------|------|-----|-----|-------|----------------|-------------|"])
                for cs in ds.case_stats:
                    status_str = ", ".join(f"{st}:{cnt}" for st, cnt in sorted(cs.status_distribution.items()))
                    repeat_note = "" if cs.repeatability_eligible else " (single run)"
                    lines.append(f"| {cs.base_case_id} | {cs.n_repeats}{repeat_note} | {cs.mean:.4f} "
                                f"| {cs.std:.4f} | {cs.cv:.4f} | {cs.range_score:.4f} "
                                f"| {cs.status_consistency:.0%} | {status_str} |")
                lines.append("")
            if ds.limitations:
                lines.append("### Limitations")
                for lim in ds.limitations: lines.append(f"- ⚠️ {lim}")
                lines.append("")
        lines.extend(["## Interpretation Guide", "",
            "- **CV < 0.10 within a case**: stable repeated results (requires ≥2 repeats).",
            "- **Stable Case Rate**: fraction of repeatable cases (≥2 repeats) that are stable.",
            "- **Pass Rate**: fraction of eligible runs where the scorer returned `pass`.",
            "- **Within-Case Std**: same-case variation. Between-Case Std: fixture difficulty variation.",
            "- **N/A repeatability**: insufficient repeats — run with `--repeats 2` or more."])
        return "\n".join(lines)

    def summary_table(self) -> str:
        header = "| Dimension | Cases | Eligible | Mean | CV | Pass Rate | Perf | Repeat |"
        sep = "|-----------|-------|----------|------|-----|-----------|------|--------|"
        rows = [header, sep]
        for ds in self.dimension_stats:
            cv_str = f"{ds.mean_within_case_cv:.3f}" if ds.mean_within_case_cv is not None else "N/A"
            rows.append(f"| {ds.dimension} | {ds.n_cases} | {ds.n_eligible_runs} "
                       f"| {ds.macro_mean:.3f} | {cv_str} | {ds.pass_rate:.0%} "
                       f"| {ds.performance_status} | {ds.repeatability_status} |")
        return "\n".join(rows)

    def to_dict(self) -> dict[str, Any]:
        return {"dimension_stats": [ds.to_dict() for ds in self.dimension_stats],
                "total_runs": self.total_runs, "total_cases": self.total_cases,
                "total_eligible_runs": self.total_eligible_runs}
