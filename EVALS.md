# EVALS

All numbers below come from runs of the code in this repo on **2026-10-17**; raw per-case rows are in each task's `evaluation/results/*.csv` (schema `task,case_id,model,expected,actual,quality,latency_ms,cost,passed,failure_reason`, plus extras) and `summary.json`. Failed cases are kept. Nothing is estimated.

## Read this first - what these results are and are not
* **No Anthropic API key was available during the build.** Every result is for the **offline fallbacks** (extractive answers, rule-based classification, template replies, rule-based transcript analysis). **The required Claude model comparison (quality, cost, latency per model) is therefore PENDING.** The harnesses are ready: `run_eval --models claude-haiku-5-5 claude-sonnet-5-5` (Task 1), `npm run eval -- --compare ...` (Task 2), `python -m evaluation.run_eval` with the key set (Task 3). Cost is `$0` for offline runs; with a key, tokens are recorded and cost is computed only if `pricing.json` supplies per-token prices (none are guessed).
* **Corpus:** the real Brilliant Directories Solution Center, crawled politely on 2026-10-16/17: **1,537 articles, 10,692 chunks, 0 failed pages** (sitemap 1,537 article URLs). Cases were first written against an 80-article sample, then **re-run on the full corpus**; both are reported.
* **Reference answers are the author's.** Expected articles/keywords/hour bands were written by the same person who built the system and should be reviewed by a second person. Several tuning passes used the dev cases; where a held-out set was later consulted this is stated.

## Task 1 - Knowledge Brain (32 cases: 26 answerable/multi-source, 5 unanswerable, 1 injection; 6 are held out: `KB-H01..06`)
Retrieval on the 26 answerable cases (is a correct article in the top 5?):

| mode | hit@5 | MRR | median latency |
|---|---|---|---|
| vector (TF-IDF) | 0.962 | 0.822 | 197 ms |
| keyword (BM25) | 1.000 | 0.830 | 198 ms |
| **hybrid (RRF) - chosen** | **1.000** | **0.881** | 195 ms |

Hybrid was chosen because it had the best MRR on real questions, not on theory. End-to-end (retrieve -> answer -> cite/abstain), offline extractive:

| corpus | pass rate | error rate | latency p50 / p95 |
|---|---|---|---|
| 80-article sample | 28/32 = **87.5%** | 0% | 12 / 15 ms |
| **full, 1,537 articles** | 25/32 = **78.1%** | 0% | 200 / 252 ms |

By type on the full corpus: answerable 20/26, unanswerable+injection 5/6. **Held-out (KB-H01-06): 5/6** (the one failure is a wrong-citation case). Pass = correct status AND cited the expected article AND (when defined) the answer contains an expected fact.
**Failures kept (7):** KB-004, KB-005, KB-018 (answer lacks the expected fact - the extractive fallback quotes the wrong sentences); KB-008, KB-017, KB-H02 (a different, plausible article ranked first; the expected one is in the top 5); KB-024 (answers "recommend a pizza restaurant" from an unrelated article - the abstention rules cannot see that every word exists somewhere in 1,500 articles).
**What the failures changed:** first run on the sample scored 65.4% because out-of-scope questions were answered; I added the unseen-term ratio and an injection guard (-> 84.6%/87.5%); I capped the overlap denominator after a long customer email diluted every sentence below the threshold. **Scale hurt:** quality fell 87.5% -> 78.1% as the corpus grew 19x (more near-duplicate articles; latency 12 -> 200 ms; I did not profile it, but the BM25 scoring loop is plain Python over every chunk, so that is the likely cause). A dense model plus a reranker is the next step; the offline extractive answerer is the weakest component and a Claude answerer is expected to beat it (unmeasured).
Other Task 1 evidence: 14 automated tests (resume after failure, retry/backoff, robots, mass-removal guard, rollback, XXE-safe sitemap, escalation lifecycle, auth, injection).

## Task 2 - Inbox Agent (52 emails + 3 knowledge-outage cases = 55; 11 held out as the `test` split)
Offline rules + templates, live Knowledge Brain, full corpus:

| metric | result |
|---|---|
| classification accuracy | 51/52 = **98.1%** (risk 98.1%) |
| overall pass (right category, right behaviour, no unsafe text, expected flags) | 46/52 = **88.5%** |
| **unsafe-action rate** (refund/price/date promise, complying with injection) | **0 / 52** |
| grounded drafts citing the expected article | 15/19 = 78.9% |
| knowledge-outage cases -> safe holding reply, no invented facts | 3/3 |
| held-out test split (11) | 9/11 = 81.8% |
| latency p50 / p95 (classify + KB call + draft) | 227 / 273 ms |
| average draft length | 751 chars |

**Failures kept (6):** E004, E010, E016, E021 (grounded but the KB returned a different article); E013 (a how-to that should have been escalated was answered - sparse evidence still passed the threshold); E049 (a follow-up that mentions a prior *charge* was classified as a refund). **Honesty note on the classifier:** accuracy rose from 80.8% to 98.1% after I widened its vocabulary using *dev-split* failures; the held-out split was not used for that. Human metrics - **edit rate, approval rate** - are **not measured** (no real reviewers); the system records them, and only automated tests exercise them.
**Prompt/rule revision measured (guide 7.5):** problem "reviewers shorten drafts" -> rule v2 (`concise`) -> regression over the 41-case dev set: accuracy 97.6% -> 97.6%, pass 90.2% -> 90.2%, unsafe 0 -> 0, average length **742 -> 719 chars (-3%)**. Activation was refused (HTTP 409) before the regression run, allowed after, and rollback to v1 worked. The effect is small; I report it as such. (`rule_revision_v2.json`)
12 automated tests cover the 20-streak rule (19 -> not eligible; an edit resets; 20 -> eligible; explicit enable; global switch; injected email never auto-sends; QA failure disables), refund/complaint exclusion, idempotent send, guardrail blocks, client retry/breaker and the regression gate.

## Task 3 - Meeting to Scope (36 cases)
Rule-based analyst, live Knowledge Brain: **36/36 pass**, but read the next paragraph.

| group | result | note |
|---|---|---|
| extraction (10 tuned) | 10/10 | written after tuning on the same transcripts - a regression check, not generalisation |
| **extraction, unseen phrasing (6)** | **first run 1/6** | failures: "pin map", "we'd love", "please make", "require", "recommendation engine, no idea", "on phones" |
| same 6 after I widened the vocabulary | 6/6 | **no longer held out** - do not read as generalisation |
| feasibility vs live KB (5) | 5/5 | |
| estimate sanity bands (4) | 4/4 | author-defined bands, not project actuals |
| change detection Meeting A -> C (6) | 6/6 | added 2, modified 1, removed 1, out of scope 1, deadline 2027-03-31 -> 04-30, ids stable |
| approval compliance (4) | 4/4 | follow-up refused before approval; unapproved send blocked; edit resets approval; send idempotent |
| long transcript in stages (1) | 1/1 | staged == single pass |

The honest generalisation figure for the rule-based extractor is the **first-run 1/6**: it is vocabulary-bound, which is why the quote-validated Claude path is the intended primary extractor (unmeasured). Latency, full pipeline: initial meeting 1.2 s, follow-up 0.8 s (the Knowledge Brain calls dominate). 15 automated tests; one validates that a model-invented quote cannot create a requirement.

## Reliability, safety, cost summary
* Reliability: 0 errors across 55 + 32 + 36 cases; crawler 0 failed of 1,537; knowledge outage handled in Tasks 2 and 3 (tested and evaluated).
* Safety: 0 unsafe commitments; injection text in emails, transcripts and questions never changed behaviour in any case.
* Cost: offline runs are free; **per-model cost/latency/quality: pending an API key.**

## Limits
Reference data is author-written; offline baselines only; lexical retrieval degrades with corpus size; role-played transcripts; no real reviewers. These bound every claim above.
