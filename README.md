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
- **Persistent checkpointing via SqliteSaver** — failed tasks resume from the exact node that failed, zero token waste across server restarts
- **Pluggable skill packs** — YAML-driven industry configurations (role skills + research skills + search policies)
- **9-stage search result cleaning pipeline** (canonicalize → clean → dedup → near-dedup → relevance → quality → structure → guard → format)
- **7 search adapters** — Serper, Tavily, Bocha, SEC EDGAR, CNINFO, GitHub Repos, Jina/Direct Reader
- **4.5K-line memory system** — incremental compression, fact reconciliation with SPDV decomposition, context window management, context assembly with token budget enforcement
- Multi-provider LLM support: `openai`, `google`, `groq`, `deepseek`
- Chinese-first report output with KaiTi font, justified alignment, and CJK PDF support
- Exportable reports in `DOCX` and `PDF`
- Observable async task runtime with persisted state and event logs
- **Three-layer evaluation framework** — component-level scorers (compression fidelity, pipeline quality), integration consistency checks, and end-to-end scoring, backed by real LLM-judge runs and a reliability report (CV/σ across repeats)

## Current Status

- ✅ **Phase 1 complete**: Harness core layer separated from domain layer
- ✅ **Phase 2 complete**: Tool integration — ToolRegistry + ToolPipeline + 9 cleaning stages + 7 search adapters
- ✅ **Phase 3 complete**: Memory & context management — incremental compression, fact reconciliation, context assembly
- ✅ **Phase 4 complete**: Evaluation framework — 3 scorers (compression fidelity, pipeline quality, source traceability), 7 consistency checks, fixture-driven runner with N-repeat reliability analysis
- ✅ Full API + SPA flow: signup/login → submit company → generate analysts → human feedback → report → export
- ✅ Persistent checkpoints with SqliteSaver — exact-node retry across server restarts
- ✅ 428 automated tests passing (`backend/tests/`)
- 📋 Phase 5 next: polish, second domain to validate pluggability, CI integration

### Evaluation results (real LLM runs, deepseek-chat, 3 repeats)

| Metric | Result |
|---|---|
| Compression fact retention | 86% (CV 0.091 — stable) |
| Compression hallucination rate | 0% |
| Pipeline dedup recall / precision | 100% / 75% |
| Source traceability (regex-only) | 3/3 fixture cases scored correctly |

Full data: `backend/eval_results/` (raw run records + `reliability_report.md`).

## Tech Stack

- Python 3.11+
- FastAPI / Uvicorn
- LangGraph / LangChain / `langgraph-checkpoint-sqlite`
- Serper / Tavily / Bocha / SEC EDGAR / CNINFO / GitHub Repos — multi-backend search
- Jina Reader / Direct Reader — URL-to-text browsing
- SQLAlchemy + SQLite for user accounts
- `python-docx` + `reportlab` (with CJK TTFont) for report export
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
│   ├── tests/                          # 🆕 Test suite (428 tests)
│   │   ├── harness/                    # Unit/integration tests (memory, tools, eval scorers, checkpoint reliability, ...)
│   │   └── fixtures/                   # Evaluation fixtures (compression/ pipeline/ end_to_end/)
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
  → TOOL_REGISTRY resolves backend (Serper / Tavily / Bocha / SEC / CNINFO)
    → SearchTool.search() returns raw results
      → ToolPipeline (9 stages)
          ├── CanonicalizeURLStage    (strip tracking params)
          ├── CleanTextStage          (strip HTML, collapse whitespace)
          ├── ExactDeduplicateStage   (canonical URL, best-wins)
          ├── NearDuplicateStage      (content fingerprint + bigram Jaccard)
          ├── RelevanceScoreStage     (keyword + optional LLM rerank)
          ├── QualityScoreStage       (domain, fact density, SEO filler)
          ├── StructureFactsStage     (extract numbers/dates/entities — CJK-aware)
          ├── OutputGuardStage        (prompt injection detection)
          └── FormatDocumentStage     (structured <Document> XML)
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
| 2 | **Tool Integration**: 9-stage search cleaning pipeline + multi-backend adapters | ✅ Done |
| 3 | **Memory & Context**: Incremental compression + fact reconciliation + context assembly | ✅ Done |
| 4 | **Evaluation Framework**: Fixture simulation + component/integration/e2e scorers + reliability analysis | ✅ Done |
| 5 | **Polish**: Second domain to prove pluggability + CI integration + frontend eval visualization | 📋 Planned |

## Limitations

- Current storage model is local-first and geared toward development or demos
- User authentication is backed by local SQLite
- Task state and event persistence are file-based
- Report quality depends heavily on model selection, prompt quality, and external search results
- Only one domain (due diligence) is actually implemented today; the "pluggable domain" architecture is in place but not yet validated with a second domain

## FAQ

### `TAVILY_API_KEY is missing`

`TAVILY_API_KEY` is required because interview generation depends on live web search.

### Task failed in the UI

Inspect the failure phase through the task endpoints. Retry via `POST /api/tasks/{task_id}/retry`.

### Token usage shows `N/A`

This means the current model provider did not return usage metadata. It does not affect report generation or downloads.
