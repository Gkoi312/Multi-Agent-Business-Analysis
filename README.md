# AgentHarness — Multi-Agent Orchestration & Evaluation Platform

A `LangGraph + FastAPI` **pluggable-domain AI agent infrastructure platform** with built-in memory management and evaluation framework. The first domain application is **enterprise due diligence report generation**.

## Project Positioning

```
Operating System  :  Applications  =  AI Harness  :  Due Diligence / Stock Analysis / Legal Review Agents
```

This is not a specific agent application — it is the **infrastructure layer (Harness)** shared by all agent applications. It provides:

1. **Agent Runtime** — orchestration engine, state management, fan-out, interrupt, recovery
2. **Tool Integration** — standardized tool registry, invocation pipeline, result cleaning
3. **Memory & Context** — conversational compression, structured working memory, context window management
4. **Human-in-the-Loop** — approval gates, feedback injection, pause/resume
5. **Observability** — traces, metrics, events, cost tracking
6. **Evaluation** — offline eval (fixture simulation), online monitoring, reliability analysis

## Highlights

- **Harness + Domain layered architecture** — engine and business logic are decoupled; swapping domains only requires a new adapter
- Multi-agent due diligence workflow built on `LangGraph` with fan-out parallel interviews
- Human-in-the-loop analyst review and regeneration loop
- **Pluggable skill packs** — YAML-driven industry configurations (role skills + research skills + search policies)
- **5-stage search result cleaning pipeline** (dedup → clean → relevance → structure → format)
- Multi-provider LLM support: `openai`, `google`, `groq`
- Exportable reports in `DOCX` and `PDF`
- Observable async task runtime with persisted state and event logs

## Current Status

- ✅ **Phase 1 complete**: Harness core layer separated from domain layer
- ✅ **Phase 2 complete**: Tool integration — ToolRegistry + ToolPipeline + 5 cleaning stages + Tavily/Brave/Jina adapters
- ✅ Full API + SPA flow: signup/login → submit company → generate analysts → human feedback → report → export
- 🚧 **Phase 3 next**: Memory management & context compression (multi-turn deep research)
- 📋 Phase 4-5: Evaluation framework → polish & testing

## Tech Stack

- Python 3.11+
- FastAPI / Uvicorn
- LangGraph / LangChain
- Tavily Search / Brave Search / Jina Reader
- SQLAlchemy + SQLite for user accounts
- `python-docx` + `reportlab` for report export
- `structlog` for structured logging
- Jinja2 templated prompts
- React / Vite / React Router

## Project Structure

```text
.
├── backend/
│   ├── start_api.py                    # API entry point
│   ├── harness/                        # 🆕 Harness core platform layer
│   │   ├── runtime/                    # Agent Runtime (graph builder, fan-out, checkpoint)
│   │   ├── tools/                      # Tool Integration (registry, pipeline, adapters)
│   │   │   ├── registry.py            # ToolRegistry
│   │   │   ├── pipeline.py            # ToolPipeline + ProcessingStage
│   │   │   ├── search/                # Search adapters (Tavily, Brave) + cleaner stages
│   │   │   └── browse/                # Browse adapter (Jina Reader)
│   │   ├── memory/                     # Memory & Context (compressor, working memory)
│   │   ├── human_loop/                 # Human-in-the-Loop (gate, feedback)
│   │   ├── observability/              # Observability (task runtime, tracer, metrics)
│   │   ├── evaluation/                 # Evaluation framework (runner, scorer, fixtures)
│   │   └── models/                     # Generic data models (Agent, State, Task)
│   ├── domains/                        # 🆕 Domain application layer (pluggable)
│   │   ├── base.py                     # DomainAdapter base class
│   │   └── due_diligence/              # Due diligence domain
│   │       ├── graph.py                # Main report graph
│   │       ├── interview.py            # Interview sub-graph
│   │       ├── schemas.py              # Domain state definitions
│   │       └── prompts/                # Domain prompt templates
│   ├── skills/                         # Industry skill packs (YAML-driven)
│   ├── app/                            # Web application layer
│   │   ├── api/                        # FastAPI routes + services
│   │   ├── utils/                      # Model loader, etc.
│   │   └── database/                   # User auth database
│   ├── tests/                          # 🆕 Test suite
│   │   └── harness/
│   │       └── test_tools.py           # Tool pipeline unit tests (16 tests)
│   ├── .runtime/
│   ├── generated_report/
│   └── users.db
├── frontend/
│   ├── src/
│   ├── package.json
│   └── vite.config.ts
├── docs/
│   └── Harness改造计划书.md
├── .env.example
└── README.md
```

### Layered Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                  Domain Apps (pluggable)                      │
│   business-due-diligence  │  stock-analysis  │  legal-review  │
├──────────────────────────────────────────────────────────────┤
│                  Agent Harness Core                           │
│  ┌─────────────┐ ┌──────────────┐ ┌───────────────────────┐  │
│  │ Agent        │ │ Tool         │ │ Memory & Context      │  │
│  │ Runtime      │ │ Integration  │ │ Manager               │  │
│  └─────────────┘ └──────────────┘ └───────────────────────┘  │
│  ┌─────────────┐ ┌──────────────┐ ┌───────────────────────┐  │
│  │ Human-in-    │ │ Observability│ │ Evaluation             │  │
│  │ the-Loop     │ │              │ │ Framework             │  │
│  └─────────────┘ └──────────────┘ └───────────────────────┘  │
├──────────────────────────────────────────────────────────────┤
│                  Infrastructure                               │
│   Model Loader  │  DB (SQLite)  │  File Storage  │  HTTP     │
└──────────────────────────────────────────────────────────────┘
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

Copy `.env.example` to `.env` and fill in the values:

```env
# Required: LLM provider
LLM_PROVIDER=openai

# Shared model parameters
LLM_MODEL_NAME=qwen-plus
LLM_TEMPERATURE=0
LLM_MAX_OUTPUT_TOKENS=8192

# Provider-specific keys
OPENAI_BASE_URL=
OPENAI_API_KEY=your-openai-key
GOOGLE_API_KEY=
GROQ_API_KEY=

# Required: web search
TAVILY_API_KEY=your-tavily-key

# Optional: alternative search backend with site: filter (Phase 2)
BRAVE_SEARCH_API_KEY=

# Optional: URL-to-Markdown reader (Phase 2, free tier works without key)
JINA_API_KEY=

# Backend runtime root
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
   - `industry_pack` (available via `GET /api/skill-packs`, currently supports `ai`)
3. The system enters `running_generation` and executes until the `human_feedback` interrupt point
4. Task status changes to `awaiting_feedback` — review the analyst plan on the task detail page
5. Submit feedback to regenerate analysts, or leave empty to approve and continue
6. When the task reaches `completed`, download the `DOCX` / `PDF` files

## End-to-End Flow

```text
User input → company type classification → skill assembly → analyst draft
→ human feedback loop → research planning → parallel interviews (fan-out)
→ report section merge → final report assembly → DOCX/PDF export
```

### Search Pipeline (Phase 2)

```text
LLM generates search query
  → TOOL_REGISTRY resolves backend (Tavily / Brave)
    → SearchTool.search() returns raw results
      → ToolPipeline (5 stages)
          ├── DeduplicateStage    (exact URL + Jaccard title >0.85)
          ├── CleanTextStage      (strip HTML, collapse whitespace, min 100 chars)
          ├── RelevanceFilterStage(keyword gate + optional cheap-LLM binary filter)
          ├── StructureFactsStage (extract numbers/dates/sentiment — regex, zero cost)
          └── FormatDocumentStage (structured <Document> XML)
        → cleaned results → LLM
```

## API Overview

| Endpoint | Method | Description |
|----------|:------:|-------------|
| `/api/skill-packs` | GET | List available industry skill packs |
| `/api/auth/signup` | POST | Register a user |
| `/api/auth/login` | POST | Log in |
| `/api/auth/logout` | POST | Log out |
| `/api/auth/me` | GET | Get current user |
| `/api/reports` | POST | Create and start a report task |
| `/api/tasks` | GET | List current user's tasks |
| `/api/tasks/{id}` | GET | Task details |
| `/api/tasks/{id}/events` | GET | Task event stream |
| `/api/tasks/{id}/feedback` | POST | Submit feedback and continue |
| `/api/tasks/{id}/retry` | POST | Retry a failed task |
| `/api/tasks/{id}/files/{name}` | GET | Download output file |

## Data and Outputs

- `users.db` — SQLite user account database
- `.runtime/tasks.json` — persisted task state
- `.runtime/task_events.jsonl` — task event log
- `generated_report/` — generated report files
- `logs/` — application logs

## Development Notes

### Code organization

- **Harness layer** (`backend/harness/`) — generic infrastructure, zero business logic
- **Domain layer** (`backend/domains/`) — domain-specific business logic; implement `DomainAdapter` to add new domains
- **App layer** (`backend/app/`) — web application (API routes, service orchestration, database)
- **Skills** (`backend/skills/`) — YAML skill packs, configuration-driven

### Adding a new domain

1. Create a new directory under `backend/domains/`
2. Implement the `DomainAdapter` interface from `domains/base.py`
3. Provide domain-specific `schemas.py`, `prompts/`, and `graph.py`
4. Create a corresponding `skill_pack.yaml` under `backend/skills/`

### Adding a new search backend

```python
from harness.tools.registry import TOOL_REGISTRY
from harness.tools.search.brave import BraveSearchAdapter

TOOL_REGISTRY.register_search(BraveSearchAdapter(api_key="..."))
```

The domain code resolves backends by name automatically via `TOOL_REGISTRY.get_search(name)`.

## Roadmap

| Phase | Content | Status |
|:---:|------|:---:|
| 1 | **Foundation**: Separate Harness core from Domain layer | ✅ Done |
| 2 | **Tool Integration**: 5-stage search cleaning pipeline + multi-backend adapters | ✅ Done |
| 3 | **Memory & Context**: Incremental turn compression + working memory + token management | 🚧 Next |
| 4 | **Evaluation Framework**: Fixture simulation + 5-dimension scorer + reliability analysis | 📋 Planned |
| 5 | **Polish**: Unit tests + multi-industry fixtures + frontend visualization | 📋 Planned |

## Limitations

- Current storage model is local-first and geared toward development or demos
- User authentication is backed by local SQLite
- Task state and event persistence are file-based
- Report quality depends heavily on model selection, prompt quality, and external search results

## FAQ

### `TAVILY_API_KEY is missing`

`TAVILY_API_KEY` is required because interview generation depends on live web search.

### Task failed in the UI

Inspect the failure phase through the task endpoints. Retry via `POST /api/tasks/{task_id}/retry`.

### Token usage shows `N/A`

This means the current model provider did not return usage metadata. It does not affect report generation or downloads.
