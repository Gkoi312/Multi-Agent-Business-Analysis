# 尽调项目 Demo 脚本（面试版，8-10 分钟）

## 1. 开场（30 秒）

一句话介绍：

> 这是一个基于多智能体的科技公司尽调系统，输入公司名后会自动输出业务、规模发展和风险分析，并支持人审反馈、任务追踪和失败重试。

## 2. 展示输入（1 分钟）

在 `Dashboard` 演示输入：

- company_name: `OpenAI`
- focus: `business model, enterprise growth, regulatory risks`
- target_role: `Product Manager Intern`

强调点：

- 已将通用 `topic` 改造成尽调输入模型
- 这不是泛报告，而是面向决策和求职尽调

## 3. 展示任务流转（2 分钟）

跳转到进度页，说明状态机：

- `running_generation`
- `awaiting_feedback`
- `running_feedback`
- `completed`

展示 `My Tasks`：

- 可以查看事件（Events）
- 失败时可重试（Retry）

## 4. 展示反馈闭环（1 分钟）

在 `awaiting_feedback` 阶段输入反馈，例如：

`Please strengthen risk assessment with concrete impact scenarios and make scale signals more specific.`

说明点：

- 有人审节点，不是黑盒一次性输出
- 支持二次引导，贴近真实业务流程

## 5. 展示结果亮点（2 分钟）

报告完成后展示：

- DOCX/PDF 下载
- 风险统计可视化：High / Medium / Low
- Final recommendation 摘要

强调点：

- 输出结构固定为尽调章节
- 风险结论可被快速消费

## 6. 总结价值（1 分钟）

总结三点：

1. **业务化**：从通用主题生成改成公司尽调场景
2. **工程化**：保留任务编排、状态机、失败重试、日志
3. **可解释**：结构化输出 + 风险等级 + 推荐结论

## 7. 面试追问准备（可选）

- 为什么不保留 topic 兼容？
  - 作品集场景，优先语义清晰和展示完整度。
- 如何继续升级？
  - 增加证据覆盖率统计、引用冲突检查、批量公司横向对比。
