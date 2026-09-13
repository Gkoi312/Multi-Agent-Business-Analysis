# 分析师 Skill 保真度

**总分：93/100 | 等级：A | 日期：2026-08-06**

| 维度 | 得分 | 说明 |
|---|---:|---|
| 角色边界清晰度 | 15/15 | 已覆盖 AI claim、生产 readiness、供应商依赖、wrapper/moat、高风险模型治理，并明确和市场、财务、风险角色交接。 |
| 问题质量 | 15/15 | 问题能驱动模型来源、artifact、生产 eval、benchmark 污染、数据权利、wrapper 三轴和 system card/TEVV 等关键缺口。 |
| 搜索策略 | 19/20 | 搜索源从 GitHub/arXiv/Hugging Face 扩展到 model card、system card、生产 eval、vendor artifact、teardown、benchmark contamination 和治理框架。 |
| 判断纪律 | 19/20 | 引入 AI Claim 分级、Demo 到生产证据链、Benchmark 测量边界、Wrapper 正反论证和高风险治理证据链。 |
| 输出契约 | 15/15 | Memo 增加 AI Claim 分级、正反论证、投后/交易后优先事项、Tripwires，适合下游合并和复核。 |
| 多 Agent 差异度 | 7/10 | 技术角色已经很鲜明；市场和风险分析师尚未材料驱动升级，后续仍需做交叉去重。 |
| 诚实边界 | 3/5 | 明确不可替代代码审计、红队、生产日志审计和专家访谈；后续可加入动态真实公司测试结果。 |

## 材料依据

本版从合成原型升级为材料驱动 v1.5，主要吸收：

- M1-M2：AI technical due diligence checklist。
- M3-M6：Sequoia/a16z 关于 AI defensibility、workflow、user network、GTM flywheel 的判断。
- M4-M5：HELM 和 Hugging Face model cards 的评测/模型文档方法。
- M8-M9：Perplexity teardown 和 AI wrapper 护城河材料。
- M10-M11：生产 LLM eval 和 AI vendor due diligence。
- M12-M14：benchmark 污染、污染检测和 factuality benchmark 边界。
- M15-M17：OpenAI Preparedness、Anthropic system cards、NIST AI RMF/TEVV。

## 测试记录

### 测试 1：问题生成

预期行为：面对 AI 公司 brief，优先追问 AI claim 分级、模型/供应商来源、生产 eval、数据权利、wrapper 三轴、benchmark 污染、高风险治理材料。

静态结果：`backend/skills/ai/tech-analyst.md` 的“提问风格”和“必问问题”已覆盖。

### 测试 2：搜索 query

预期行为：搜索不再只使用融资新闻和通用网页，而会优先生成 GitHub、Hugging Face、arXiv、model card、system card、生产 eval、vendor artifact、teardown、benchmark contamination 等查询。

静态结果：已在 `GENERATE_SEARCH_QUERY` prompt 中注入 skill card，并在 skill card 内写入查询模式和来源偏好。

### 测试 3：证据解读

预期行为：对 PR、融资新闻、无方法学榜单降级；对 artifact、生产 eval、模型卡、system card、代码、第三方 benchmark 和治理文档提高置信；对 benchmark 外推保持边界。

静态结果：已写入“证据层级”“核心判断框架”“回答纪律”和“盲区与反模式”。

### 测试 4：章节撰写

预期行为：输出“技术护城河与可复制性”章节，包含 AI Claim 分级、证据强度、正反论证、Tripwires 和信息来源，而不是公司概览。

静态结果：已写入 Memo 输出契约，并调整 `WRITE_SECTION` prompt 允许 skill card 的输出契约扩展默认章节结构。

## 后续动态验证

建议选一个真实 AI 公司 brief 跑端到端任务，观察：

- 技术分析师生成的问题是否明显区别于市场/风险分析师。
- 搜索结果是否包含技术文档、GitHub、Hugging Face、论文、model card、system card、teardown 或 benchmark 方法学。
- 最终章节是否包含 AI Claim 分级、证据强度、正反论证、Tripwires 和未验证问题。
- review 阶段是否不再误报中文 `### 信息来源` 和 `### 风险提示`。
