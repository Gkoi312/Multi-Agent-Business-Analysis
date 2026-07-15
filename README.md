# AgentHarness — Multi-Agent Orchestration & Evaluation Platform

A `LangGraph + FastAPI` multi-agent **due diligence research platform**, with a reusable memory/tooling/evaluation core (Harness) underneath a single, concrete domain (report generation). Industry breadth comes from **pluggable skill packs** (data), not new domains (code).

## Project Positioning

```
Operating System  :  Applications        =  AI Harness   :  Due Diligence Research
Plugin / Config    :  Vertical knowledge  =  Skill Pack   :  AI / Beauty / Real Estate / ...
```

The **Harness layer** is business-agnostic infrastructure shared by any research workflow built on top of it. It provides:

1. **Tool Integration** — standardized tool registry, invocation pipeline, result cleaning
2. **Memory & Context** — conversational compression, structured working memory, context window management, reusable research-loop node factories (`harness/memory/nodes.py`)
3. **Observability** — traces, metrics, events, cost tracking
4. **Evaluation** — offline eval (fixture simulation), online monitoring, reliability analysis

The **Domain layer** (`domains/due_diligence/`) owns the one thing that's genuinely business-specific: the LangGraph workflow shape, the prompts, and the report structure. **Industry variation** (AI / beauty / real estate / …) is handled by a third, orthogonal axis — **skill packs** (`skills/<industry>/*.md`) — markdown data injected into generic prompt templates at render time, requiring zero code changes to add a new vertical.

## Highlights

- **Harness / Domain / Skill-pack separation** — generic infra (memory, tool pipeline, checkpointing, eval) has zero business logic; the domain layer owns workflow structure; skill packs own industry knowledge as data
- Multi-agent due diligence workflow built on `LangGraph` with fan-out parallel interviews
- Human-in-the-loop analyst review and regeneration loop, via LangGraph's native `interrupt_before` + checkpointer (pause at a node, inject feedback, resume)
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
- ✅ 434 automated tests passing (`backend/tests/`)
- ✅ Removed the unused generic `harness/runtime/` graph-template layer and `harness/human_loop/` — validated only by a mock-domain smoke test, never wired into the real workflow; extracted the parts that *were* genuinely reusable (compress / update_memory / compact_history / continue-router) into `harness/memory/nodes.py`, which the real interview graph now calls
- ✅ Removed 3 unused Pydantic types (`SkillRef`/`SourcePolicy`/`DomainMemoryRef`) and one dead helper method that had zero call sites anywhere in the repo
- ✅ Moved `ModelLoader`, `SkillRegistry`, the structured logger, and the shared exception type out of `app/` and into `harness/` — they were pure cross-cutting infrastructure (zero HTTP logic, zero due-diligence logic) that had been misplaced under the web layer by convention rather than by design
- ✅ Closed the `harness` → `app` reverse-dependency entirely: `harness/observability/{task_runtime,tracer}.py` no longer import anything from `app.*`. `TaskRuntime`/`NodeTracer` now take an optional `runtime_dir` constructor param and fall back to a self-contained default (`harness/paths.py`, reads the same `RUNTIME_DIR` env var directly rather than importing `app.config`) — genuinely dependency-injected, not just relocated
- ✅ Renamed `app/` → `server/` — once stripped of the misplaced infra above, all that was left was the FastAPI routes, DB, and config; "app" was an ambiguous name for that (the repo also has `frontend/`, which is arguably "the app" too)
- ✅ Fixed a real, previously-silent bug found while doing the rename: `domains/due_diligence/graph.py` constructed `SkillRegistry` with a path one directory level too shallow (`parents[3]` instead of `parents[2]`, a leftover from before the Phase 1 restructuring added a directory level). `SkillRegistry.load_skill_pack()` fails silently on a missing directory, so this had been returning an empty skill bundle on every real run — the `ai` skill pack's Markdown content was likely never actually reaching the LLM in production, only the generic fallback. Verified fixed: `load_skill_pack("ai")` now returns 3 role skills + 3 domain-memory entries as expected
- ✅ Repo-wide dead-function sweep (AST-based, cross-checked against every source file and test): removed ~15 functions/methods with zero callers anywhere — mostly abandoned "async twin" methods (`acompress_completed_turn`, `acompact_history`, `acompute_new_summary`, `_agenerate_summary`) and a whole unused `EvalRunner` class in `harness/evaluation/runner.py` (the real eval script, `run_real_evals.py`, always drove scorers directly and never went through it — kept `EvalRunResult`, which *is* used). Excluded from the sweep: FastAPI route handlers and framework callback methods (`@app.on_event`, `HTMLParser.handle_*`), which only look unreferenced because the framework calls them by convention, not by name
- ✅ Merged `harness/models/state.py` (a single 10-line function) into `harness/models/__init__.py`, and merged `harness/logger/` + `harness/paths.py` into `harness/observability/` (their only real consumers) — deliberately did *not* create a `harness/utils/` catch-all, since that reintroduces the same "vague dumping ground" problem `app/` had; `exceptions.py`, `llm_loader.py`, `skill_registry.py` stay as distinct top-level harness modules because each maps to one clearly named capability
- 📋 Phase 5 next: a second populated skill pack (e.g. `beauty/`) to prove the "industry via data, not code" claim end-to-end, plus CI integration

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
│   ├── harness/                        # 🆕 Harness core platform layer (zero business logic)
│   │   ├── tools/                      # Tool Integration (registry, pipeline, adapters)
│   │   │   ├── registry.py            # ToolRegistry
│   │   │   ├── pipeline.py            # ToolPipeline + ProcessingStage
│   │   │   ├── search/                # Search adapters (Tavily, Brave) + cleaner stages
│   │   │   └── browse/                # Browse adapter (Jina Reader)
│   │   ├── memory/                     # Memory & Context (compressor, working memory, nodes.py generic node factories)
│   │   ├── observability/              # Observability (task runtime, tracer, metrics, logger.py, paths.py)
│   │   ├── evaluation/                 # Evaluation framework (scorer, fixtures, reliability — EvalResult, not EvalRunner)
│   │   ├── models/                     # Generic data models (Agent, memory types, keep_latest reducer)
│   │   ├── exceptions.py               # ResearchAnalystException — shared error wrapper
│   │   ├── skill_registry.py           # Loads skill packs from skills/<industry>/*.md
│   │   └── llm_loader.py               # Multi-provider LLM loading (openai/google/groq/deepseek)
│   ├── domains/                        # 🆕 Domain application layer (workflow structure)
│   │   └── due_diligence/              # Due diligence domain — the only domain today
│   │       ├── graph.py                # Main report graph
│   │       ├── interview.py            # Interview sub-graph
│   │       ├── schemas.py              # Domain state definitions
│   │       └── prompts/                # Domain prompt templates (skill_card injected at render time)
│   ├── skills/                         # Industry skill packs (Markdown-driven; only `ai/` populated today)
│   ├── server/                         # Web server layer — HTTP delivery only (renamed from app/)
│   │   ├── api/                        # FastAPI routes + services
│   │   ├── database/                   # User auth database
│   │   └── config.py                   # Env-driven config (CORS origins, runtime dir, ...)
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
│           Skill Packs (data — industry knowledge)             │
│         skills/ai/*.md   │  skills/beauty/*.md (future)       │
├──────────────────────────────────────────────────────────────┤
│         Domain Layer (code — workflow structure)              │
│   domains/due_diligence/  (graph shape, prompts, report spec) │
├──────────────────────────────────────────────────────────────┤
│                  Agent Harness Core (zero business logic)     │
│  ┌─────────────┐ ┌──────────────┐ ┌───────────────────────┐  │
│  │ Tool         │ │ Memory &     │ │ Observability          │  │
│  │ Integration  │ │ Context      │ │                       │  │
│  └─────────────┘ └──────────────┘ └───────────────────────┘  │
│  ┌───────────────────────────────────────────────────────┐   │
│  │ Evaluation Framework                                    │   │
│  └───────────────────────────────────────────────────────┘   │
├──────────────────────────────────────────────────────────────┤
│                  Infrastructure                               │
│   Model Loader  │  DB (SQLite)  │  File Storage  │  HTTP     │
└──────────────────────────────────────────────────────────────┘
```

Agent orchestration itself (graph construction, `Send`-based fan-out, `interrupt_before` + checkpointer for human-in-the-loop) is handled directly with native LangGraph primitives inside the domain layer — there is no separate generic "runtime template" layer. An earlier iteration explored one (`harness/runtime/`, a parameterized `AgentGraphTemplate`), but it was removed after establishing that this project's actual extension axis is skill packs (data) within one domain, not multiple structurally different domains — the abstraction had no second real consumer, so it was deleted rather than kept as unused scaffolding.

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
- **Domain layer** (`backend/domains/`) — domain-specific business logic (graph shape + prompts + report spec). Adding an industry goes through skill packs, not this layer; adding a structurally different domain (stock analysis, legal review) means hand-writing a new `graph.py`
- **Server layer** (`backend/server/`) — HTTP delivery only (API routes, service orchestration, database); no cross-cutting infra lives here anymore, that's all in `harness/`
- **Skills** (`backend/skills/`) — Markdown skill packs, data-driven (no code)

### Adding a new industry (skill pack — the common case)

1. Create a new directory under `backend/skills/<industry>/`
2. Add role-skill Markdown files (analyst personas) following `skills/ai/*.md` as a template
3. No domain code changes needed — `skill_card.body` is injected directly into the existing prompt templates in `domains/due_diligence/prompts/interview.py`

### Adding a new domain (a structurally different workflow — the rare case)

There's no assisted scaffolding for this today (an earlier generic `AgentGraphTemplate` attempt was removed as unused — see Roadmap). Adding a genuinely new domain (e.g. a debate-structured legal review, not a due-diligence-shaped report) currently means hand-building a new `StateGraph` under `backend/domains/<name>/`, following `domains/due_diligence/graph.py` as a reference, and reusing what's already domain-agnostic from `harness/` (tools, memory, observability, evaluation).

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
- Only one skill pack (`ai`) is actually populated today; industry breadth (beauty, real estate, …) is architecturally supported (skill_card content is injected into generic prompt templates) but not yet exercised with a second pack
- The report's macro-structure and the search source-type routing table are still written directly into the domain's prompt text, not into skill-pack config — a genuinely different vertical (e.g. real estate, which cares about property registries, not SEC filings) would currently require editing domain prompt code, not just adding a skill pack

## FAQ

### `TAVILY_API_KEY is missing`

`TAVILY_API_KEY` is required because interview generation depends on live web search.

### Task failed in the UI

Inspect the failure phase through the task endpoints. Retry via `POST /api/tasks/{task_id}/retry`.

### Token usage shows `N/A`

This means the current model provider did not return usage metadata. It does not affect report generation or downloads.
