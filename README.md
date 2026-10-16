# AI Tech Consultant

Three connected AI services for a small software/service business: **Knowledge Brain** (answers product questions from the help centre, with citations), **Inbox Agent** (drafts replies to customer emails for human review), **Meeting to Scope** (turns client meetings into scope, estimate and proposal). Tasks 2 and 3 use Task 1 only through its HTTP API.

| Service | Folder | Stack | Port |
|---|---|---|---|
| Knowledge Brain + chat UI | `task1-knowledge-brain/` | Python, FastAPI, SQLite | 8001 |
| Inbox Agent + review UI | `task2-inbox-agent/` | TypeScript (Node 22.18+), zod | 8002 |
| Meeting to Scope + builder UI | `task3-meeting-to-scope/` | Python, FastAPI | 8003 |

**Prerequisites:** Python 3.11+, Node.js 22.18+ (24 recommended), internet access for the first ingestion.

**Configure:** `cp .env.example .env`. Set `ANTHROPIC_API_KEY` to use Claude. Without it every service runs in a documented **offline fallback** (extractive answers, rule-based classification, template replies, rule-based transcript analysis) - fine for the demo and tests, but not a substitute for the model comparison in `EVALS.md`. Never commit `.env`.

**Run everything (one command):**

```bash
python start.py                 # installs deps, ingests 20 articles if the DB is empty, starts all 3 services
python start.py --full-ingest   # ingest the whole sitemap instead (polite rate limit; takes a while)
```

Open http://127.0.0.1:8001/ (Knowledge Chat), :8002 (Inbox Review), :8003 (Scope Builder). API docs: http://127.0.0.1:8001/docs.
Demo path for Task 3: create a project, load *Meeting A*, **Approve** the draft, then load *Meeting C* (follow-up) and open the **Changes** tab.

**Tests (no network needed):**

```bash
cd task1-knowledge-brain  && python -m pytest -q      # 14 tests
cd task2-inbox-agent      && npm install && npm test  # 12 tests (+ npm run typecheck)
cd task3-meeting-to-scope && python -m pytest -q      # 15 tests
```

**Evaluations (need the Knowledge Brain running; results land in each task's `evaluation/results/`):**

```bash
cd task1-knowledge-brain  && python -m evaluation.run_eval [--models claude-haiku-5-5 claude-sonnet-5-5]
cd task2-inbox-agent      && npm run eval -- [--compare claude-haiku-5-5,claude-sonnet-5-5]
cd task3-meeting-to-scope && python -m evaluation.run_eval
```

**What is simulated:** email delivery (an outbox table, no SMTP), proposal sending (recorded, not emailed), meeting transcripts (role-played scripts, not recordings), and payment/SMS providers (not integrated). See `DESIGN.md` for limits and `EVALS.md` for measured results.

**Layout:** `DESIGN.md` (architecture, choices), `EVALS.md` (results, failures), `AI_LOG.md` (assistant usage), `start.py`, one folder per task.
