# Multi-Agent Business Analysis

A `LangGraph + FastAPI` multi-agent business analysis and due diligence system.
The project now uses a separated architecture: a `FastAPI` backend that exposes JSON APIs and a `React + Vite` frontend that handles the browser experience.

## Highlights

- Multi-agent due diligence workflow built on `LangGraph`
- Human-in-the-loop analyst review and regeneration before research execution
- Separated frontend and backend architecture with JSON APIs
- Parallel interview and report-writing pipeline
- Exportable report outputs in both `DOCX` and `PDF`
- Observable async runtime with persisted task state and event logs

## Why This Project

This project explores how multi-agent systems can support structured business analysis rather than simple chat interactions.
Instead of generating a single answer in one pass, the system breaks the workflow into analyst planning, human review, web research, interview execution, section drafting, report assembly, and export.

It is designed as a practical demo and learning project for:

- multi-agent orchestration with `LangGraph`
- human approval loops in agent workflows
- long-running async job handling in web applications
- report generation pipelines that combine LLM output and traditional file export

## Current Status

- End-to-end web flow is implemented through API + SPA: sign up / log in -> submit company information -> generate analyst draft -> collect feedback -> regenerate analysts across multiple rounds -> continue research and report generation -> export files
- Asynchronous task runtime is implemented with task states, event streams, retry support, and dependency blocking
- Multiple model providers are supported: `openai`, `google`, and `groq`
- Report outputs include risk frequency statistics (`high` / `medium` / `low`) and a final recommendation summary

## Core Capabilities

1. **Multi-agent collaboration**
   - The main graph handles analyst generation, parallel interviews, and report synthesis
   - The sub-graph handles questioning, search, answering, interview persistence, and section writing
2. **Human in the loop**
   - Execution pauses at the `human_feedback` node, allowing a feedback -> regenerate -> confirm loop before interviews begin
   - The frontend displays analyst version numbers (`v1`, `v2`, `v3`, ...) to distinguish each revision round
3. **Observable asynchronous tasks**
   - Task state is persisted in `.runtime/tasks.json`
   - Task events are written to `.runtime/task_events.jsonl`
4. **Dual-format report export**
   - Output directory: `generated_report/<report_name>_<timestamp>/`
   - Output formats: `.docx` and `.pdf`
5. **Usage metrics fallback**
   - Token usage is read from model metadata whenever available
   - If a provider does not return usage metadata, the UI displays `N/A` without failing or retrying the task

## Tech Stack

- Python 3.11+
- FastAPI / Uvicorn
- LangGraph / LangChain
- Tavily Search
- SQLAlchemy + SQLite for user accounts
- `python-docx` + `reportlab` for report export
- `structlog` for structured logging
- React / Vite / React Router

## Project Structure

```text
.
├── backend/
│   ├── start_api.py
│   ├── app/
│   │   ├── api/
│   │   ├── workflows/
│   │   ├── schemas/
│   │   ├── database/
│   │   └── ...
│   ├── .runtime/
│   ├── generated_report/
│   └── users.db
├── frontend/
│   ├── src/
│   ├── package.json
│   └── vite.config.ts
├── requirements.txt
└── pyproject.toml
```

## Quick Start

### 1. Install backend dependencies

```bash
python -m venv .venv

# Windows PowerShell
.venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

### 2. Configure environment variables

Create a `.env` file in the project root, or copy from `.env.example` and update the values:

```env
# Required: LLM provider
LLM_PROVIDER=openai

# Shared model parameters
LLM_MODEL_NAME=qwen-plus
LLM_TEMPERATURE=0
LLM_MAX_OUTPUT_TOKENS=2048

# Used when LLM_PROVIDER=openai
OPENAI_BASE_URL=
OPENAI_API_KEY=your-openai-key

# Used when LLM_PROVIDER=google
GOOGLE_API_KEY=

# Used when LLM_PROVIDER=groq
GROQ_API_KEY=

# Required: web search
TAVILY_API_KEY=your-tavily-key

# Backend runtime root for separated deployment
APP_ROOT=backend

# Frontend dev origins allowed by CORS
FRONTEND_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

> Do not commit real secrets from `.env` to version control.

### 3. Start the backend API

```bash
python backend/start_api.py
```

### 4. Start the frontend

```bash
cd frontend
npm install
npm run dev
```

Then open `http://localhost:5173` in your browser.

## Usage Flow

1. Sign up and log in through the frontend
2. Fill in the dashboard form:
   - `company_name` (required)
   - `focus` (optional)
   - `target_role` (optional)
3. The system enters `running_generation` and executes until the `human_feedback` interrupt point, where analyst drafts are generated
4. Task status changes to `awaiting_feedback`, and you can review the analyst plan on the task detail page
5. If feedback is provided, the system enters `running_feedback`, regenerates analysts, and returns to `awaiting_feedback`
6. If feedback is empty, the system treats the plan as approved and continues research, interviews, and report generation
7. When the task reaches `completed`, download the generated `DOCX` / `PDF` files and review the risk metrics and recommendation summary

## End-to-End Flow

```text
User input -> analyst draft -> human feedback loop -> research + interviews
-> report sections -> final report assembly -> DOCX/PDF export
```

## Workflow Overview

### Agent Architecture

```text
+------------------+      +------------------+      +------------------+
| React + Vite SPA |----->+ FastAPI JSON API +----->+    TaskRuntime   |
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
                           Interview sub-graph for each analyst:
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

### Main Graph: `report_generator_workflow.py`

- `create_analyst`: generate analyst personas with structured output
- `human_feedback`: pause execution and wait for user approval or feedback
- `regenerate_analyst`: regenerate analysts based on feedback
- `conduct_interview`: run interview sub-graphs in parallel after approval
- `write_report` / `write_introduction` / `write_conclusion`: synthesize report content in parallel
- `finalize_report`: assemble the final report text

### Sub-Graph: `interview_workflow.py`

- `ask_question` -> `search_web` -> `generate_answer` -> `save_interview` -> `write_section`

### Task Status Flow

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
  +--> (non-empty feedback) -> running_feedback -> awaiting_feedback
  |
  +--> (empty feedback) -> running_feedback -> completed
  |
  +--> failed  --(POST /tasks/{task_id}/retry)--> running_feedback
```

## API Overview

### Page routes

- `GET /`: login page
- `GET /signup`: sign-up page
- `GET /dashboard`: create due diligence task page
- `GET /my_tasks`: task list page
- `GET /report_progress/{task_id}`: task progress page

### Task and report endpoints

- `GET /health`: health check
- `POST /generate_report`: create and start a report task
- `POST /submit_feedback`: submit feedback and continue the workflow
- `GET /tasks`: current user's task list in JSON
- `GET /tasks/{task_id}`: task details in JSON
- `GET /tasks/{task_id}/events`: task event stream in JSON
- `POST /tasks/{task_id}/claim`: assign a task owner
- `POST /tasks/{task_id}/dependencies`: configure task dependencies
- `POST /tasks/{task_id}/retry`: retry a failed task
- `GET /download/{file_name}?task_id=...`: download a task output file

## Data and Outputs

- `users.db`: SQLite user account database
- `.runtime/tasks.json`: persisted task state
- `.runtime/task_events.jsonl`: task event log
- `generated_report/`: generated report files
- `logs/`: application logs

## Limitations

- The current storage model is local-first and geared toward development or demos
- User authentication is backed by local SQLite rather than a production-grade identity system
- Task state and event persistence are file-based instead of using a dedicated queue or database backend
- Report quality depends heavily on model selection, prompt quality, and external search results
- The project currently has limited automated test coverage

## Suitable Use Cases

- Learning how to structure multi-agent workflows with `LangGraph`
- Demonstrating a business analysis agent pipeline in a portfolio project
- Prototyping human-in-the-loop report generation systems
- Exploring task orchestration, retry behavior, and workflow observability

## Not Yet Optimized For

- high-concurrency production workloads
- multi-tenant SaaS deployment
- enterprise authentication and authorization
- durable distributed task execution

## Error Handling

- `exception/custom_exception.py` provides the unified `ResearchAnalystException`
- It wraps lower-level exceptions with additional context such as file name, line number, and traceback
- This makes logs and API error messages easier to debug and keeps exception formatting consistent across modules

## FAQ

### 1. `TAVILY_API_KEY is missing`

`TAVILY_API_KEY` is required because interview generation depends on live web search.

### 2. The task failed in the UI

You can inspect the failure phase through the task endpoints:

- `running_generation` failures are usually caused by model or search configuration issues
- `running_feedback` failures are usually related to thread state or model invocation issues

You can retry a failed task via `POST /tasks/{task_id}/retry`.

### 3. Token usage shows `N/A`

This means the current model provider or invocation path did not return usage metadata. It does not affect report generation or downloads.

### 4. File download failed

The download endpoint validates the relationship between `task_id`, `file_name`, and the current user to prevent cross-task downloads.

## Development Notes

- Keep state fields centralized in `schemas/models.py` when adding new nodes
- Route all task status changes through `task_runtime.py` instead of updating state in multiple places
- Prefer editing prompts in `prompt_lib/` before changing workflow logic
- For production use, replace the current local-file and in-memory storage strategy with more durable infrastructure

## Roadmap Ideas

- Add automated tests for core API routes and workflow state transitions
- Replace local runtime persistence with database-backed task storage
- Improve authentication, session handling, and deployment readiness
- Add example screenshots or a short demo walkthrough
- Introduce more evaluation hooks for report quality and agent performance

