# 材料清单

本目录用于“材料驱动蒸馏”AI 技术护城河分析师。

把原始材料放入 `sources/` 后，在这里登记。材料可以是公开报告、投资 memo、技术尽调 checklist、benchmark 方法学文章、专家访谈、技术博客、论文、模型评测报告，或你认可的分析样本。

## 材料登记表

| ID | 文件/链接 | 类型 | 来源 | 一手/二手 | 可信度 | 覆盖维度 | 备注 |
|---|---|---|---|---|---|---|---|
| M1 | https://www.bato-labs.com/insights/ai-technical-due-diligence-checklist/ | 技术尽调 checklist | Bato Labs / Christopher Petrino | 方法论/经验材料 | 高 | 角色边界、证据层级、判断框架、失败模式、输出契约 | 强调把 AI claim 分成 demonstrated/documented/inferred/vendor-dependent/manually assisted/roadmap-dependent/unverified，并要求 evidence artifacts。 |
| M2 | https://corteus.com/blog/technical-due-diligence-checklist-for-ai-startups/ | 技术尽调 checklist | Corteus | 方法论材料 | 中 | 关键问题、成本、eval、数据权利、坏答案模式 | 强调 demo 不够，要看 eval、模型所有权、unit economics 和可展示的流程/数字。 |
| M3 | https://sequoiacap.com/article/generative-ai-act-two/ | 投资观点/行业 thesis | Sequoia Capital | 机构观点 | 高 | 护城河、数据壁垒、产品价值、开源/大厂竞争 | 强调从 technology-out 转向 customer-back，应用层数据护城河不稳，workflow 和 user network 更可能形成持久优势。 |
| M4 | https://crfm.stanford.edu/2022/11/17/helm.html | 模型评测方法学 | Stanford CRFM HELM | 一手方法学 | 高 | benchmark 可信度、评测框架、证据层级 | 强调评测要覆盖广泛场景、多指标、标准化，并承认评测不完整。 |
| M5 | https://huggingface.co/docs/hub/main/model-cards | 模型文档标准 | Hugging Face | 一手文档 | 高 | 模型卡、训练数据、限制、评估结果、可复现性 | 模型卡应描述模型、用途、限制、训练参数、实验信息、训练数据和评估结果。 |
| M6 | https://a16z.com/cloud-lessons-for-the-ai-era/ | 投资观点/价值捕获 | Andreessen Horowitz | 机构观点 | 高 | 防御性、价值捕获、基础设施与应用关系 | 强调长期价值捕获取决于 defensibility，AI 时代仍需关注技术、系统记录、网络效应和 GTM 飞轮。 |
| M7 | https://regcore.ai/whitepapers/foundation-model-due-diligence | 基础模型尽调白皮书 | RegCore.AI | 方法论材料 | 中 | 模型卡局限、训练数据、供应商依赖、监管/安全 | 强调 model cards 必要但不充分，需要额外问卷和持续监控。 |
| M8 | https://www.theteardown.co/perplexity | 真实公司 teardown | The Teardown | 二手研究/案例 | 高 | 成品报告样式、wrapper 争议、模型供应商依赖、分发护城河、估值与风险 | 74 个来源的 Perplexity 案例，展示 case for/case against、证据权重、tripwires 和方法局限。 |
| M9 | https://preuve.ai/blog/are-ai-wrapper-startups-worth-building-2026 | AI wrapper 护城河案例 | Preuve AI | 方法论/案例 | 中 | API wrapper、数据/工作流/分发护城河 | 判断 wrapper 是否值得做，关键看模型提供商短期不能复制的数据、工作流锁定或分发渠道。 |
| M10 | https://optyxstack.com/llm-evaluation/llm-evaluation-framework-production | 生产 LLM 评估框架 | OptyxStack | 方法论材料 | 中 | 生产评估、成本、延迟、工具行为、回归 | 强调不能只看单一质量分，要看任务成功、groundedness、安全、格式、工具、成本、延迟、cohort drift。 |
| M11 | https://www.deepinspect.ai/blog/ai-vendor-due-diligence-checklist | AI vendor 安全尽调 | DeepInspect | 方法论材料 | 中 | 模型来源、运行区域、训练数据、API 合同、failover、审计 | 强调传统 SaaS 安全审查不够，AI vendor 要看 model catalog、region map、training-data policy、routing posture。 |
| M12 | https://proceedings.mlr.press/v267/choi25b.html | benchmark 污染论文 | ICML / PMLR | 一手论文 | 高 | 数据污染、benchmark 可靠性 | 指出评测数据进入预训练语料会虚高指标，提出检测污染的方法。 |
| M13 | https://aclanthology.org/2025.findings-naacl.291/ | 数据污染检测综述 | ACL Anthology | 一手论文 | 高 | benchmark 污染检测局限 | 综述 50 篇污染检测论文，指出检测假设不一定稳健，分布漂移下方法可能失效。 |
| M14 | https://openai.com/index/introducing-simpleqa/ | factuality benchmark | OpenAI | 一手方法学 | 高 | factuality、benchmark 范围边界 | SimpleQA 强调短事实问题易评估，但也承认无法代表长文本多事实回答能力。 |
| M15 | https://openai.com/index/updating-our-preparedness-framework/ | frontier model 风险评估框架 | OpenAI | 一手治理框架 | 高 | 高风险能力、自动化 eval、外部专家、safeguards report | 强调风险应具备 plausible/measurable/severe/net-new/instantaneous-or-irremediable，并结合自动化 eval 与专家 deep dive。 |
| M16 | https://www.anthropic.com/system-cards | 模型 system card 列表 | Anthropic | 一手文档 | 高 | system card、能力、安全评估、部署决策 | System card 用于记录 Claude 模型能力、安全评估和负责部署决策。 |
| M17 | https://www.nist.gov/itl/ai-risk-management-framework | AI 风险管理框架 | NIST | 一手框架 | 高 | TEVV、风险管理、生成式 AI profile | NIST AI RMF 和 GenAI Profile 支持测试、评估、验证、确认和风险治理。 |

## 类型建议

- `优秀报告`：投研报告、咨询报告、行业深度、技术 DD memo。
- `方法论`：尽调 checklist、评估框架、benchmark 方法学。
- `案例`：API wrapper、数据壁垒、开源替代、推理成本、技术误判案例。
- `一手技术材料`：论文、技术博客、模型卡、GitHub、Hugging Face、产品文档。
- `专家表达`：访谈、演讲、播客 transcript。

## 可信度标注

- 高：一手技术材料、带方法学的报告、监管/官方/审计材料。
- 中：权威媒体访谈、有署名机构报告、可交叉验证的专家观点。
- 低：PR 稿、融资新闻、无来源榜单、营销号总结。
