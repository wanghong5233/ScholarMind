# ScholarMind DeepResearch Service

## Goal
Build an academic-grade DeepResearch service by combining:
- Queue-based orchestration
- GPT-Researcher-style report scaffolding
- ScholarMind academic RAG grounding

## Structure
- `main.py`: FastAPI entrypoint
- `config.py`: service settings
- `service/`: orchestration logic and state management
- `schemas/`: API request/response models
- `router/`: HTTP endpoints
- `tests/`: unit tests

## Local Development
```bash
cd backend/services/deep_research
pip install -r requirements.txt
uvicorn main:app --reload --port 8004
```

## API
`POST /api/deep-research`
`POST /api/deep-research/submit`
`GET /api/deep-research/runs`
`GET /api/deep-research/{research_id}`
`GET /api/deep-research/{research_id}/snapshot`
`GET /api/deep-research/{research_id}/progress`
`GET /api/deep-research/{research_id}/progress/stream`
`GET /api/deep-research/{research_id}/progress/since`
`POST /api/deep-research/{research_id}/resume`
`POST /api/deep-research/{research_id}/cancel`
`POST /api/deep-research/{research_id}/replay`
`POST /api/idea-generation`
`GET /api/idea-generation/runs`
`GET /api/idea-generation/{idea_id}`
`GET /api/deep-research/queue`
`PATCH /api/deep-research/{research_id}/priority`
`GET /api/deep-research/{research_id}/export?format=markdown|html|pdf`
`GET /api/deep-research/{research_id}/archive/export?format=zip`
`GET /api/deep-research/{research_id}/evidence/{block_id}/export?format=zip`
`POST /api/deep-research/compare`
`GET /api/deep-research/compare/export?left_id=...&right_id=...&format=markdown|html|pdf|json`

## Run Queue (Async)
- `MAX_ACTIVE_RUNS` limits concurrent background runs (default 2).
- `QUEUE_BACKEND` selects queue backend: `sqlite` (default) or `redis`.
- For `QUEUE_BACKEND=sqlite`, queued runs are persisted in `run_queue.db` under the data root.
- For `QUEUE_BACKEND=redis`, queued runs are persisted in Redis with `REDIS_QUEUE_PREFIX`.
- Submit responses include `queue_position` when a run is queued.
- Queue ordering respects `metadata.priority` (higher runs first, range -10~10).
- Priority can be updated via `PATCH /api/deep-research/{research_id}/priority`.
- If the queue is non-empty, new runs are enqueued first and scheduling pulls from the queue.
- `QUEUE_PRIORITY_AGING_SECONDS` enables priority aging (default 300s per +1).
- `QUEUE_MAX_PENDING` caps queued runs (0 disables queue, negative means unlimited).
- Queue status response includes `effective_priority` and `wait_seconds` for queued items.

## Multi-Instance Scheduler
- Each worker claims queued runs with a lease, renews periodically, and can requeue expired runs.
- Workers cancel local tasks if lease ownership is lost, preventing duplicate execution.
- `SCHEDULER_LEASE_SECONDS` controls the lease duration for running runs.
- `SCHEDULER_RENEW_SECONDS` controls the scheduler polling/renew interval.
- For SQLite, all instances must share the same `DATA_ROOT` for multi-instance safety.
- For Redis, configure `REDIS_HOST`, `REDIS_PORT`, `REDIS_DB`, `REDIS_PASSWORD`, `REDIS_QUEUE_PREFIX`.

## Queue Smoke Test
- `python backend/services/deep_research/scripts/queue_smoke_test.py --backend sqlite`
- `python backend/services/deep_research/scripts/queue_smoke_test.py --backend redis --redis-host 127.0.0.1`

## Sync Run (Debug)
- `ENABLE_SYNC_RUN=false` disables `POST /api/deep-research` to avoid bypassing queue control.

## IdeaGen Storage
- IdeaGen metadata stored in `idea_meta.json`, payload in `ideas.json`.

## Run Watchdog
- `RUN_TIMEOUT_SECONDS` hard timeout for a run (0 disables).
- `RUN_IDLE_TIMEOUT_SECONDS` idle timeout based on last progress event (0 disables).
- `RUN_WATCHDOG_INTERVAL_SECONDS` polling interval for the watchdog.

## Report Export
- `format=markdown|html|pdf` (default markdown).
- PDF export uses `reportlab` and renders a text-only version of the report.
 - Report details include a `quality` field (evidence/citation coverage metrics) for debugging and demos.

## Archive Export
- `GET /api/deep-research/{research_id}/archive/export?format=zip`
- Includes `meta.json`, `report.json`, `citations.json`, `queue.json`, `outline.json`, `progress.jsonl` when present.
- Adds `manifest.json` with file list, sizes, SHA256 hashes, and export metadata.

## Evidence Export
- `GET /api/deep-research/{research_id}/evidence/{block_id}/export?format=zip`
- Produces `evidence.json` containing block details, citations, traces, and progress events.
- Adds `manifest.json` with counts for notes/citations/traces/decisions/events and highlights.

## Run Comparison
- `POST /api/deep-research/compare`
- Compares two runs with summary and diff metrics (blocks/citations/tools/decisions/errors/duration).
- `GET /api/deep-research/compare/export` exports comparison as markdown/html/pdf/json.

Key request flags:
- `use_web_search`: enable web search tools per run
- `use_code_exec`: enable code execution tools per run

Streaming progress:
- `GET /api/deep-research/{research_id}/progress/stream?user_id=1`

Progress retention:
- `PROGRESS_MAX_BYTES` (default 5MB) trims the progress JSONL file.
- `PROGRESS_TAIL_LINES` controls how many recent events are kept after trimming.

## Report LLM Refinement (Optional)
Set the following environment variables to enable LLM-based report generation:

- `REPORT_LLM_ENABLED=true`
- `REPORT_LLM_SECTIONAL=true` (optional; generate section-by-section and write partial `report.json` for live preview)
- `REPORT_LLM_SECTION_MAX_TOKENS=1024` (optional per-section budget)
- `OPENAI_API_KEY=...`
- `OPENAI_BASE_URL=https://api.openai.com/v1`
- `OPENAI_MODEL_NAME=gpt-4o`

## Research Decision LLM (Optional)
Enable LLM-driven tool selection and sufficiency checks:

- `DECISION_LLM_ENABLED=true`
- `DECISION_LLM_MODEL_NAME=gpt-4o-mini`

When enabled, the DecisionAgent can output `tool_calls` (e.g. `web.search`, `code.exec`) that are executed by the ToolRouter.

## Follow-up Execution Mode
Choose whether follow-up questions are executed inline or expanded into the queue:

- `FOLLOWUP_EXECUTION_MODE=queue` (default)
- `FOLLOWUP_EXECUTION_MODE=inline`

## Web Search (Optional)
Enable external web search with a supported provider:

- `ENABLE_WEB_SEARCH=true`
- `WEB_SEARCH_PROVIDER=tavily` (or `serper`)
- `WEB_SEARCH_API_KEY=...`
- `WEB_SEARCH_BASE_URL=...` (optional override)
- `WEB_SEARCH_MAX_RESULTS=5`

## Code Execution (Optional)
Run Python snippets for calculations:

- `ENABLE_CODE_EXEC=true`
- `CODE_EXEC_TIMEOUT_SECONDS=5`
- `CODE_EXEC_MAX_OUTPUT_CHARS=2000`
- `CODE_EXEC_MAX_CODE_CHARS=2000`

## Tool Budget (Safety)
Limit per-block tool usage:

- `MAX_TOOL_CALLS_PER_BLOCK=6`
- `MAX_CODE_EXEC_SNIPPETS=2`

## Next Steps
Completed (current implementation):
- LLM-powered planning (`PlannerAgent.plan_with_rag`)
- GPT-Researcher style report templates + optional LLM report refinement (`ReportTemplateBuilder`, `ReportRefiner`)
- Web search + code execution tools (optional)
- Frontend DeepResearch workspace UI (run list / queue / progress / exports)

Possible enhancements (quality-focused):
- Deeper multi-level report outline (e.g., 3-level outline) and section-by-section generation
- Automated hallucination checks (claim ↔ citation consistency) and evaluation scripts
- Report style presets (academic / blog / slide notes) driven by `report_style`
