# Multi-Agent Business Analysis

一个基于 `LangGraph + FastAPI` 的多智能体商业分析与企业尽调系统。  
用户可以通过 Web 页面提交公司信息，生成分析师角色，执行联网研究与访谈工作流，整合分析结果，并最终导出 `DOCX` 和 `PDF` 报告。

## 项目亮点

- 基于 `LangGraph` 构建的多智能体尽调工作流
- 支持 Human-in-the-loop 的分析师审核与重生成
- 基于 `FastAPI` 和服务端模板的 Web 任务管理流程
- 支持并行访谈与并行报告撰写
- 支持导出 `DOCX` 与 `PDF` 双格式报告
- 支持异步任务运行时观测、任务状态持久化与事件日志记录

## 为什么做这个项目

这个项目关注的不是“单轮对话式回答”，而是如何让多智能体系统支持结构化的商业分析流程。
系统不会一次性直接生成最终答案，而是将整个过程拆分为分析师规划、人工审核、联网研究、访谈执行、章节撰写、报告整合与文件导出。

它更适合作为下面几个方向的实践项目：

- 使用 `LangGraph` 进行多智能体编排
- 在智能体工作流中加入人工审批环节
- 在 Web 应用中处理长耗时异步任务
- 结合 LLM 输出与传统文档导出的报告生成流程

## 当前状态

- 已实现完整 Web 流程：注册 / 登录 -> 提交公司信息 -> 生成分析师草案 -> 人工反馈 -> 多轮重生分析师 -> 继续检索与报告生成 -> 导出文件
- 已实现异步任务运行时，支持任务状态、事件流、失败重试和依赖阻塞
- 已支持多模型提供方切换：`openai`、`google`、`groq`
- 报告结果包含风险词频统计（`high` / `medium` / `low`）以及 Final Recommendation 摘要

## 核心能力

1. **多智能体协作**
   - 主图负责分析师生成、并行访谈与报告整合
   - 子图负责提问、检索、回答、访谈保存与章节撰写
2. **Human in the loop**
   - 在 `human_feedback` 节点暂停执行，支持“反馈 -> 重生分析师 -> 再次确认”的循环，直到人工满意后才进入访谈阶段
   - 前端显示分析师版本号（`v1`、`v2`、`v3` ...），用于区分每轮反馈后的方案
3. **异步任务可观测**
   - 任务状态持久化在 `.runtime/tasks.json`
   - 任务事件写入 `.runtime/task_events.jsonl`
4. **双格式报告导出**
   - 输出目录：`generated_report/<report_name>_<timestamp>/`
   - 输出格式：`.docx` 和 `.pdf`
5. **指标兼容兜底**
   - Token usage 会尽量从模型返回的 usage 元数据中读取
   - 如果提供方未返回 usage，前端显示 `N/A`，不会因此导致任务失败或重试

## 技术栈

- Python 3.11+
- FastAPI / Uvicorn / Jinja2
- LangGraph / LangChain
- Tavily Search
- SQLAlchemy + SQLite 用户账户存储
- `python-docx` + `reportlab` 报告导出
- `structlog` 结构化日志

## 项目结构

```text
.
├── research_and_analyst/
│   ├── api/
│   │   ├── main.py
│   │   ├── routes/report_routes.py
│   │   ├── services/
│   │   │   ├── report_service.py
│   │   │   └── task_runtime.py
│   │   ├── models/request_models.py
│   │   └── templates/
│   ├── workflows/
│   │   ├── report_generator_workflow.py
│   │   └── interview_workflow.py
│   ├── schemas/models.py
│   ├── utils/model_loader.py
│   ├── database/db_config.py
│   ├── prompt_lib/
│   ├── logger/
│   └── exception/custom_exception.py
├── static/
├── generated_report/
├── .runtime/
├── logs/
├── requirements.txt
└── pyproject.toml
```

## 快速开始

### 1. 安装依赖

```bash
python -m venv .venv

# Windows PowerShell
.venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

### 2. 配置环境变量

在项目根目录创建 `.env`，或复制 `.env.example` 后再填写实际值：

```env
# 必填：LLM 提供方
LLM_PROVIDER=openai

# 通用模型参数
LLM_MODEL_NAME=qwen-plus
LLM_TEMPERATURE=0
LLM_MAX_OUTPUT_TOKENS=2048

# 当 LLM_PROVIDER=openai 时使用
OPENAI_BASE_URL=
OPENAI_API_KEY=your-openai-key

# 当 LLM_PROVIDER=google 时使用
GOOGLE_API_KEY=

# 当 LLM_PROVIDER=groq 时使用
GROQ_API_KEY=

# 必填：联网搜索
TAVILY_API_KEY=your-tavily-key

# 可选：Embedding
EMBEDDING_MODEL_NAME=models/text-embedding-004
```

> 请不要把 `.env` 中的真实密钥提交到版本库。

### 3. 启动服务

```bash
uvicorn research_and_analyst.api.main:app --host 0.0.0.0 --port 8000 --reload
```

然后在浏览器中打开 `http://localhost:8000`。

## 使用流程

1. 注册并登录
2. 在 Dashboard 中填写：
   - `company_name`（必填）
   - `focus`（可选）
   - `target_role`（可选）
3. 系统进入 `running_generation`，先执行到 `human_feedback` 中断点并生成分析师草案
4. 任务状态变为 `awaiting_feedback`，你可以在进度页查看分析师方案并提交反馈
5. 如果提交了反馈，系统进入 `running_feedback`，重生分析师后回到 `awaiting_feedback`
6. 如果反馈为空，系统将其视为已确认，并继续执行研究、访谈和报告生成
7. 当任务状态变为 `completed` 后，可以下载生成的 `DOCX` / `PDF` 文件，并查看风险指标与建议摘要

## 端到端流程

```text
用户输入 -> 分析师草案 -> 人工反馈循环 -> 研究与访谈
-> 报告章节 -> 最终报告整合 -> DOCX/PDF 导出
```

## 工作流概览

### Agent 架构

```text
+------------------+      +------------------+      +------------------+
|  FastAPI Web UI  +----->+   report_routes  +----->+    TaskRuntime   |
+------------------+      +------------------+      +------------------+
                                                       |
                                                       v
                                             +------------------+
                                             |   ReportService  |
                                             +------------------+
                                                       |
                                                       v
                                             +------------------+
                                             | LangGraph Main   |
                                             | (StateGraph)     |
                                             +------------------+
                                                       |
                                                       v
                                             +------------------+
                                             |  create_analyst  |
                                             +------------------+
                                                       |
                                                       v
                                             +------------------+
                                             |  human_feedback  |
                                             | (interrupt point)|
                                             +------------------+
                                                       |
                          +----------------------------+---------------------------+
                          |                            |                           |
                          v                            v                           v
                +------------------+         +------------------+       +------------------+
                | conduct_interview|         | conduct_interview|       | conduct_interview|
                |   Analyst 1      |         |   Analyst 2      |       |   Analyst N      |
                +------------------+         +------------------+       +------------------+
                          |                            |                           |
                          +----------------------------+---------------------------+
                                                       |
                                                       v
                           每位分析师执行的访谈子图：
                           ask_question -> search_web -> generate_answer
                               -> save_interview -> write_section
                                                       |
                                                       v
                    +------------------+   +------------------+   +------------------+
                    |   write_report   |   |write_introduction|   | write_conclusion |
                    +------------------+   +------------------+   +------------------+
                              \                   |                    /
                               \                  |                   /
                                \                 |                  /
                                 +----------------------------------+
                                 |         finalize_report          |
                                 +----------------------------------+
                                                   |
                                                   v
                                 +----------------------------------+
                                 |            save_report           |
                                 +----------------------------------+
                                           |                 |
                                           v                 v
                                        +------+         +------+
                                        | DOCX |         | PDF  |
                                        +------+         +------+
```

### 主图：`report_generator_workflow.py`

- `create_analyst`：生成结构化分析师角色
- `human_feedback`：暂停执行并等待人工审批或反馈
- `regenerate_analyst`：根据反馈重生分析师
- `conduct_interview`：在确认后并行执行访谈子图
- `write_report` / `write_introduction` / `write_conclusion`：并行汇总报告内容
- `finalize_report`：拼装最终报告文本

### 子图：`interview_workflow.py`

- `ask_question` -> `search_web` -> `generate_answer` -> `save_interview` -> `write_section`

### 任务状态流转

```text
pending
  |
  v
running_generation
  |
  +--> failed  --(POST /tasks/{task_id}/retry)--> running_generation
  |
  v
awaiting_feedback
  |
  +--> (反馈非空) -> running_feedback -> awaiting_feedback
  |
  +--> (反馈为空) -> running_feedback -> completed
  |
  +--> failed  --(POST /tasks/{task_id}/retry)--> running_feedback
```

## API 概览

### 页面路由

- `GET /`：登录页
- `GET /signup`：注册页
- `GET /dashboard`：创建尽调任务页
- `GET /my_tasks`：任务列表页
- `GET /report_progress/{task_id}`：任务进度页

### 任务与报告接口

- `GET /health`：健康检查
- `POST /generate_report`：创建并启动报告任务
- `POST /submit_feedback`：提交反馈并继续工作流
- `GET /tasks`：当前用户任务列表（JSON）
- `GET /tasks/{task_id}`：任务详情（JSON）
- `GET /tasks/{task_id}/events`：任务事件流（JSON）
- `POST /tasks/{task_id}/claim`：设置任务负责人
- `POST /tasks/{task_id}/dependencies`：设置任务依赖
- `POST /tasks/{task_id}/retry`：重试失败任务
- `GET /download/{file_name}?task_id=...`：下载任务输出文件

## 数据与产物

- `users.db`：SQLite 用户账户数据库
- `.runtime/tasks.json`：任务状态持久化文件
- `.runtime/task_events.jsonl`：任务事件日志
- `generated_report/`：生成的报告文件
- `logs/`：应用日志

## 局限性

- 当前存储方案偏本地开发和演示场景
- 用户认证依赖本地 SQLite，而不是生产级身份系统
- 任务状态和事件采用文件持久化，而不是独立的任务队列或数据库后端
- 报告质量高度依赖模型选择、提示词质量和外部搜索结果
- 当前自动化测试覆盖还比较有限

## 适合的使用场景

- 学习如何使用 `LangGraph` 构建多智能体工作流
- 作为作品集项目展示商业分析 Agent Pipeline
- 原型化验证 Human-in-the-loop 报告生成系统
- 研究任务编排、重试机制与工作流可观测性

## 当前尚未重点优化的方向

- 高并发生产负载
- 多租户 SaaS 部署
- 企业级认证与权限控制
- 可持久化的分布式任务执行

## 异常处理

- `exception/custom_exception.py` 提供统一异常类型 `ResearchAnalystException`
- 它会为底层异常附加文件名、行号、traceback 等上下文信息
- 这让日志和接口错误更容易定位，也使各模块的异常格式更统一

## 常见问题

### 1. 提示 `TAVILY_API_KEY is missing`

`TAVILY_API_KEY` 是必填项，因为访谈生成依赖实时联网搜索。

### 2. 页面中任务失败

可以通过任务接口查看失败阶段：

- `running_generation` 失败通常是模型配置或搜索配置问题
- `running_feedback` 失败通常与线程状态或模型调用异常有关

你可以通过 `POST /tasks/{task_id}/retry` 重试失败任务。

### 3. Token usage 显示 `N/A`

这意味着当前模型提供方或调用路径没有返回 usage 元数据，但不会影响报告生成和下载。

### 4. 文件下载失败

下载接口会校验 `task_id`、`file_name` 与当前用户之间的关系，以避免跨任务下载。

## 开发说明

- 新增节点时，优先把状态字段集中维护在 `schemas/models.py`
- 所有任务状态变更尽量统一通过 `task_runtime.py` 处理，避免多处直接写状态
- 优先在 `prompt_lib/` 中调整提示词，再考虑修改工作流逻辑
- 如果要用于生产环境，建议替换当前本地文件与内存存储方案

## Roadmap 想法

- 为核心 API 路由和工作流状态流转补充自动化测试
- 将本地运行时持久化替换为数据库驱动的任务存储
- 继续完善认证、会话处理与部署能力
- 增加示例截图或简短演示说明
- 增加更多针对报告质量和 Agent 表现的评估钩子
