# AI Tech Consultant

Three connected AI services for a small software/service business: **Knowledge Brain** (answers product questions from the help centre, with citations), **Inbox Agent** (drafts replies to customer emails for human review), **Meeting to Scope** (turns client meetings into scope, estimate and proposal). Tasks 2 and 3 use Task 1 only through its HTTP API.

| Service | Folder | Stack | Port |
|---|---|---|---|
| Knowledge Brain + chat UI | `task1-knowledge-brain/` | Python, FastAPI, SQLite | 8001 |
| Inbox Agent + review UI | `task2-inbox-agent/` | TypeScript (Node 22.18+), zod | 8002 |
| Meeting to Scope + builder UI | `task3-meeting-to-scope/` | Python, FastAPI | 8003 |

**Prerequisites:** Python 3.11+, Node.js 22.18+ (24 recommended), internet access for the first ingestion.

**Get the code:** `git clone https://github.com/allevarun1/ai-tech-consultant.git` then `cd ai-tech-consultant`.

**First-time setup on a new machine:** `python setup.py` (checks Python/Node, creates `.venv`, installs everything, creates `.env`; add `--test` to run the suites, `--ingest 20` to load articles). Then run `.venv/Scripts/python start.py` (Windows) or `.venv/bin/python start.py` (macOS/Linux).

**Configure:** `cp .env.example .env` (the services read it themselves; a variable already set in your shell wins). Never commit `.env`.

**Choose the AI provider** (Claude, Groq or Gemini) with two lines in `.env`:

```
LLM_PROVIDER=auto     # claude | groq | gemini | auto | offline   (the default provider)
LLM_FALLBACK=auto     # backups if the default fails: auto | none | a list such as groq,gemini
ANTHROPIC_API_KEY=... GROQ_API_KEY=... GEMINI_API_KEY=...   # only the ones you use
```

`auto` picks the first provider that has a key (claude, then groq, then gemini). If the default fails (rate limit, outage, unusable reply) the next provider is tried; if all fail, each service uses its built-in **offline fallback** (extractive answers, rule-based classification, template replies, rule-based transcript analysis), so nothing stops working. `/health` on every service shows the active chain. The capstone brief asks for Claude as the central model family: use Claude for the final results and the model comparison, and treat free providers as a development convenience. Groq/Gemini model names change, so check them in `.env`.

**Run everything (one command):**

```bash
python start.py                 # installs deps, ingests 20 articles if the DB is empty, starts all 3 services
python start.py --full-ingest   # ingest the whole sitemap instead (polite rate limit; takes a while)
```

Open http://127.0.0.1:8001/ (Knowledge Chat), :8002 (Inbox Review), :8003 (Scope Builder). API docs: http://127.0.0.1:8001/docs.
Demo path for Task 3: create a project, load *Meeting A*, **Approve** the draft, then load *Meeting C* (follow-up) and open the **Changes** tab.

**Tests (no network needed):**

```bash
cd task1-knowledge-brain  && python -m pytest -q      # 28 tests
cd task2-inbox-agent      && npm install && npm test  # 22 tests (+ npm run typecheck)
cd task3-meeting-to-scope && python -m pytest -q      # 18 tests
```

**Evaluations (need the Knowledge Brain running; results land in each task's `evaluation/results/`):**

```bash
cd task1-knowledge-brain  && python -m evaluation.run_eval [--models claude:claude-haiku-5-5 claude:claude-sonnet-5-5 groq:<model>]
cd task2-inbox-agent      && npm run eval -- [--compare claude:claude-haiku-5-5,claude:claude-sonnet-5-5,groq:<model>]
cd task3-meeting-to-scope && python -m evaluation.run_eval [--provider claude:claude-sonnet-5-5]
```

Models are written `provider:model`; each run pins exactly one provider with no fallback, so results are comparable and labelled with the provider that produced them.

**What is simulated:** email delivery (an outbox table, no SMTP), proposal sending (recorded, not emailed), meeting transcripts (role-played scripts, not recordings), and payment/SMS providers (not integrated). See `DESIGN.md` for limits and `EVALS.md` for measured results.

**Layout:** `DESIGN.md` (architecture, choices), `EVALS.md` (results, failures), `AI_LOG.md` (assistant usage), `start.py`, one folder per task.
