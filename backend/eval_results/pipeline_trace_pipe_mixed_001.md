# Pipeline Trace -- pipe_mixed_001

Generated: 2026-07-12T09:03:02.307822+00:00

| Stage | 输入 | 输出 | 削减率 | 耗时 | 丢弃 |
|-------|------|------|--------|------|------|
| canonicalize_url | 5 | 5 | 0.0% | 0ms | 0 |
| clean_text | 5 | 4 | 20.0% | 0ms | 1 |
| exact_dedup | 4 | 4 | 0.0% | 0ms | 0 |
| near_dedup | 4 | 4 | 0.0% | 0ms | 0 |
| relevance | 4 | 1 | 75.0% | 0ms | 3 |
| quality | 1 | 1 | 0.0% | 0ms | 0 |
| structure | 1 | 1 | 0.0% | 0ms | 0 |
| output_guard | 1 | 1 | 0.0% | 0ms | 0 |
| format | 1 | 1 | 0.0% | 0ms | 0 |
| **总计** | 5 | 1 | 80.0% | 0ms | 4 |

- **canonicalize_url**: 5->5
- **clean_text**: 5->4
- **exact_dedup**: 4->4
- **near_dedup**: 4->4
- **relevance**: 4->1
- **quality**: 1->1
- **structure**: 1->1
- **output_guard**: 1->1
- **format**: 1->1