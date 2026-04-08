# Multi-Agent Business Analysis 项目阅读指南（面向 Agent 开发实习）

这份文档的目标是帮你在面试前快速建立「可讲清楚、可深入追问」的技术认知。  
你可以把它当作项目讲解稿 + 技术备忘清单。

---

## 1. 项目一句话说明（面试开场版）

这是一个基于 `FastAPI + LangGraph` 的多智能体企业尽调系统。  
用户在 Web 页面输入公司信息后，系统会：

1. 先生成多位分析师（agent personas）  
2. 在 `human_feedback` 节点暂停，让人类反馈并可多轮重生分析师  
3. 人工确认后并行执行访谈子图（问问题 -> 联网检索 -> 生成回答 -> 写章节）  
4. 汇总为完整尽调报告并导出 `DOCX/PDF`

---

## 2. 你需要先掌握的核心模块

推荐按照以下顺序读代码（从外到内）：

1. `README.md`：先拿到整体流程和接口地图  
2. `research_and_analyst/api/main.py`：应用启动、路由挂载、任务恢复  
3. `research_and_analyst/api/routes/report_routes.py`：端到端业务入口（登录、创建任务、提交反馈、下载报告）  
4. `research_and_analyst/api/services/task_runtime.py`：异步任务状态机与持久化  
5. `research_and_analyst/api/services/report_service.py`：工作流编排与结果抽取  
6. `research_and_analyst/workflows/report_generator_workflow.py`：主图（StateGraph）  
7. `research_and_analyst/workflows/interview_workflow.py`：访谈子图  
8. `research_and_analyst/schemas/models.py`：图状态定义与 reducer  
9. `research_and_analyst/utils/model_loader.py`：多模型加载（OpenAI/Google/Groq）  
10. `research_and_analyst/prompt_lib/prompt_locator.py`：提示词模板（Jinja2）

---

## 3. 系统架构（你要能口头画出来）

### 3.1 分层结构

- **表现层**：FastAPI + Jinja2 模板（登录页、任务页、进度页）
- **接口层**：`report_routes.py` 暴露 HTML + JSON 接口
- **任务运行时层**：`TaskRuntime` 负责后台线程执行、任务状态流转、事件日志
- **编排层**：`ReportService` 负责启动 LangGraph、提交反馈、读取结果
- **智能体层**：主图 + 子图（LangGraph StateGraph）
- **基础能力层**：模型加载、Tavily 搜索、日志、异常封装、SQLite 用户库

### 3.2 关键数据落盘

- `.runtime/tasks.json`：任务当前状态（可重启恢复）
- `.runtime/task_events.jsonl`：事件流（可追踪任务生命周期）
- `generated_report/<name_timestamp>/`：DOCX/PDF 产物
- `users.db`：用户账号

---

## 4. 端到端执行链路（最重要）

以用户点击“生成报告”为例：

1. `POST /generate_report`（`report_routes.py`）  
   - 组装 `research_query`
   - `TASK_RUNTIME.create_task(...)` 写入 `pending`
   - 调 `_start_generation_job(...)` 异步启动

2. `_start_generation_job -> TASK_RUNTIME.run_in_background(...)`  
   - 状态改为 `running_generation`
   - `ReportService.start_report_generation(...)` 执行主图到中断点

3. 主图执行到 `human_feedback`（interrupt）  
   - 任务状态变 `awaiting_feedback`
   - 前端展示 `analysts_preview`

4. 用户 `POST /submit_feedback`  
   - 若反馈非空：重生分析师，再次回 `awaiting_feedback`
   - 若反馈为空：视为满意，继续跑访谈与写作，最终 `completed`

5. `ReportService.get_report_status(...)`  
   - 从图状态读取 `final_report`
   - 调 `save_report(..., docx/pdf)` 导出文件
   - 回写 `risk_summary`、`final_recommendation`、`llm_usage`

---

## 5. LangGraph 技术细节（面试高频追问）

## 5.1 主图：`AutonomousReportGenerator.build_graph`

主图节点：

- `create_analyst`：结构化输出分析师角色（`Perspectives`）
- `human_feedback`：中断点（`interrupt_before=["human_feedback"]`）
- `regenerate_analyst`：反馈驱动重生分析师
- `conduct_interview`：并行调用访谈子图（每个 analyst 一个分支）
- `write_report` / `write_introduction` / `write_conclusion`：并行写作
- `finalize_report`：拼接最终报告文本

关键分支逻辑：

- `route_after_feedback`  
  - `feedback.strip()` 非空 -> `regenerate_analyst`
  - 空 -> `start_interviews`

- `initiate_all_interviews`  
  - 基于 `analysts` 生成多个 `Send("conduct_interview", state)`  
  - 体现 LangGraph 的 fan-out 并行执行模式

## 5.2 子图：`InterviewGraphBuilder.build`

链路：

`ask_question -> search_web -> generate_answer -> (loop or save_interview) -> write_section`

循环条件：

- `turn_count < max_num_turns` 时继续问答
- 否则进入保存访谈并写章节

这个设计适合讲“可控探索深度”：通过 `max_num_turns` 限制 token 成本和耗时。

## 5.3 State 设计与 reducer

在 `schemas/models.py` 中：

- `sections: Annotated[list, operator.add]`：并行分支章节聚合
- `llm_metrics: Annotated[list, operator.add]`：节点级指标聚合
- `max_num_turns: Annotated[int, keep_latest]`：并发更新时只保留最新值

这是 LangGraph 状态并发合并的关键实现点，面试里能体现你对“并行状态一致性”的理解。

---

## 6. 任务运行时机制（工程化亮点）

`TaskRuntime` 是该项目除 agent 编排外最工程化的模块。

### 6.1 状态机设计

常见状态：

- `pending`
- `running_generation`
- `awaiting_feedback`
- `running_feedback`
- `completed`
- `failed`
- `blocked`（依赖阻塞）

### 6.2 自动重试 + 手动重试

在 `run_in_background(...)` 中：

- 每个阶段支持 `max_auto_retry`（默认 1）
- 失败会记录：
  - `failed_stage`
  - `retry_count`
  - `auto_retry` 各阶段尝试次数
- 超过自动重试后标记 `failed`，可由 `/tasks/{task_id}/retry` 手动触发

### 6.3 服务重启恢复

`main.py` 启动时会调用 `TASK_RUNTIME.recover_interrupted_tasks()`：

- 任何中断在 `running_*` 的任务会被标记为 `failed`
- 并写入 `task.interrupted` 事件

这是典型的“幂等恢复 + 可观测任务系统”思路。

### 6.4 事件流可观测

事件写入 `.runtime/task_events.jsonl`，如：

- `task.created`
- `task.started`
- `task.retrying`
- `task.completed`
- `task.failed`
- `feedback.submitted`

你可以在面试里强调：任务监控不依赖内存态，具备最小持久化能力。

---

## 7. 模型与工具接入细节

### 7.1 多模型抽象

`ModelLoader.load_llm()` 按环境变量切换：

- `LLM_PROVIDER=openai|google|groq`
- `LLM_MODEL_NAME`
- `LLM_TEMPERATURE`
- `LLM_MAX_OUTPUT_TOKENS`
- `OPENAI_BASE_URL`（兼容 OpenAI 协议服务）

这让同一编排流程可复用到不同提供商。

### 7.2 搜索工具

`AutonomousReportGenerator.__init__` 中初始化 `TavilySearchResults`。  
若缺失 `TAVILY_API_KEY`，直接抛业务异常，避免流程后期才失败。

### 7.3 Token 统计兼容

`_extract_usage` 同时兼容不同 SDK 字段：

- `usage_metadata`
- `response_metadata.token_usage`
- fallback 到 0

再由 `ReportService._aggregate_llm_metrics` 汇总总量和分节点统计。  
这在多供应商场景非常实用。

---

## 8. 报告生成与后处理

### 8.1 文本结构拼装

`finalize_report` 负责把 `introduction + content + conclusion` 组合，并追加 `Sources`。

### 8.2 导出逻辑

`save_report` 会：

- 使用 `company_name + timestamp` 生成独立目录
- 分别导出 `.docx` 和 `.pdf`
- 返回绝对路径写回任务

### 8.3 业务增强

`ReportService` 在最终结果里额外输出：

- `risk_summary`：对 `high/medium/low` 词频计数（正则）
- `final_recommendation`：提取 `## Final Recommendation` 段落并截断

---

## 9. 安全与边界（面试可主动提）

### 已做

- 下载接口按 `task_id + owner` 校验，防止跨任务下载
- 用户密码哈希（`pbkdf2_sha256`，兼容 `bcrypt`）
- 敏感 key 来自环境变量

### 仍可改进（你可以说“下一步计划”）

1. `SESSIONS` 是进程内字典，不适合多实例部署（可迁移 Redis + 签名 Cookie/JWT）  
2. `tasks.json` 文件锁 + 单机线程模型，规模化后应迁移 DB/消息队列（如 Postgres + Celery/Arq）  
3. 前端轮询可升级为 SSE/WebSocket 降低延迟  
4. 缺少系统化自动化测试（尤其工作流分支与失败恢复）

---

## 10. 面试讲解模板（可直接背）

> 我这个项目核心是把多智能体流程工程化，而不只是 Prompt 调用。  
> 架构上是 FastAPI 接入，TaskRuntime 管异步任务状态，LangGraph 负责编排主图+子图。  
> 主图先生成 analyst personas，并在 human_feedback 节点中断做人机协同；确认后并行跑多个访谈子图，最后并行写报告主体、引言、结论并汇总导出。  
> 我重点做了三件事：  
> 1) 任务可恢复：服务重启会把 running 任务安全转 failed 并可 retry；  
> 2) 可观测：任务事件写 jsonl，状态写 tasks.json，可追踪每个阶段；  
> 3) 跨模型兼容：OpenAI/Google/Groq 统一接入，token usage 做了字段兼容聚合。  
> 这让我在 demo 场景下既能展示 agent 能力，也能展示工程落地能力。

---

## 11. 建议你面试前实操一遍（30 分钟）

1. 启动服务并创建一个任务（到 `awaiting_feedback`）  
2. 提交一次“有内容反馈”（触发重生分析师）  
3. 再提交空反馈（继续到完成）  
4. 打开：
   - `.runtime/tasks.json`
   - `.runtime/task_events.jsonl`
   - `generated_report/*`
5. 记录一次失败重试流程（手动触发 `/tasks/{task_id}/retry`）

做到这一步，你就不仅能讲“流程”，还能讲“故障路径和恢复路径”。

---

## 12. 快速问答清单（高频）

- **Q：为什么要 human-in-the-loop？**  
  A：分析师角色设计决定后续检索方向，前置人工把关可显著降低后续偏航成本。

- **Q：并行分支怎么合并状态？**  
  A：通过 `Annotated[list, operator.add]` 聚合 `sections/llm_metrics`，标量字段用 reducer 规则控制。

- **Q：如何保证任务不会“卡死在 running”？**  
  A：启动恢复时将中断任务标记 failed，并提供自动/手动重试链路。

- **Q：多模型下 token 统计字段不一致怎么办？**  
  A：统一从 `usage_metadata` 和 `response_metadata.token_usage` 做兼容提取并汇总。

- **Q：如果要上生产，先改哪里？**  
  A：会话管理、任务存储、异步执行框架、鉴权体系和测试覆盖率。

---

如果你愿意，我下一步可以基于这份文档再给你补一个 `docs/面试讲稿_5分钟版.md`（更短、更口语化，专门用于面试开场自述）。
