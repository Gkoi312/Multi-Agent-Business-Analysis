# AI Harness Evaluation Reliability Report -- Real LLM Runs

**Total cases:** 6  |  **Total runs:** 6  |  **Total eligible runs:** 6

> **Performance** = quality thresholds met. **Repeatability** = consistency across repeats.

## compression_fidelity
**Performance:** FAIL  |  **Repeatability:** N/A  |  **Scorer type:** llm_judged
  *No case has at least two eligible repeats*

**Cases:** 5 | **Runs:** 5 | **Eligible:** 5 | **Skipped:** 0

| Metric | Value |
|--------|-------|
| Macro Mean | 0.7455 |
| Micro Mean | 0.7455 |
| Between-Case Std | 0.4203 |
| Within-Case Std | N/A |
| Mean Within-Case CV | N/A |
| Stable Case Rate | N/A |
| Pass Rate | 0% |

### Per-Case

| Case | Runs | Mean | Std | CV | Range | Status Consist | Status Dist |
|------|------|------|-----|-----|-------|----------------|-------------|
| comp_001_tesla_q3 | 1 (single run) | 0.9600 | 0.0000 | 0.0000 | 0.0000 | 100% | partial:1 |
| comp_002_apple_ai | 1 (single run) | 0.9750 | 0.0000 | 0.0000 | 0.0000 | 100% | partial:1 |
| comp_003_bytedance | 1 (single run) | 0.9546 | 0.0000 | 0.0000 | 0.0000 | 100% | partial:1 |
| comp_004_tesla_risk | 1 (single run) | 0.8381 | 0.0000 | 0.0000 | 0.0000 | 100% | partial:1 |
| comp_005_no_results | 1 (single run) | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 100% | fail:1 |

### Limitations
- ⚠️ No repeatable cases — run with --repeats >= 2 for stability analysis

## pipeline_quality
**Performance:** FAIL  |  **Repeatability:** DETERMINISTIC  |  **Scorer type:** deterministic
  *Deterministic scorer — CV=0 is expected*

**Cases:** 1 | **Runs:** 1 | **Eligible:** 1 | **Skipped:** 0

| Metric | Value |
|--------|-------|
| Macro Mean | 0.6667 |
| Micro Mean | 0.6667 |
| Between-Case Std | 0.0000 |
| Within-Case Std | N/A |
| Mean Within-Case CV | N/A |
| Stable Case Rate | N/A |
| Pass Rate | 0% |

### Per-Case

| Case | Runs | Mean | Std | CV | Range | Status Consist | Status Dist |
|------|------|------|-----|-----|-------|----------------|-------------|
| pipe_mixed_001 | 1 (single run) | 0.6667 | 0.0000 | 0.0000 | 0.0000 | 100% | fail:1 |

### Limitations
- ⚠️ Small sample: 1 eligible run(s) across 1 case(s)
- ⚠️ No repeatable cases — run with --repeats >= 2 for stability analysis

## Interpretation Guide

- **CV < 0.10 within a case**: stable repeated results (requires ≥2 repeats).
- **Stable Case Rate**: fraction of repeatable cases (≥2 repeats) that are stable.
- **Pass Rate**: fraction of eligible runs where the scorer returned `pass`.
- **Within-Case Std**: same-case variation. Between-Case Std: fixture difficulty variation.
- **N/A repeatability**: insufficient repeats — run with `--repeats 2` or more.