# Project explainer: AI Tech Consultant
*Talking notes for presenting this project as a developer (about 3 years' experience). Read it once, then speak from the headings. Every number here is in EVALS.md.*

## 1. The 60-second pitch
"A small software company loses time on three things: finding answers in about 1,500 help articles, replying to customer emails, and turning vague client meetings into scopes and estimates. I built three connected services. The **Knowledge Brain** answers product questions with citations and says 'I don't know' when it should. The **Inbox Agent** drafts email replies that a human reviews. **Meeting-to-Scope** turns a meeting transcript into requirements, an estimate and a proposal. The second and third services call the first through its API, so there is one source of truth for product knowledge. Every customer-facing output needs a human approval."

## 2. Architecture in one picture
```
Help-centre sitemap -> [Task 1 Knowledge Brain :8001, Python/FastAPI, SQLite]
                              ^ POST /query (typed contract)          ^
          [Task 2 Inbox Agent :8002, TypeScript]      [Task 3 Meeting-to-Scope :8003, Python]
```
Why a shared service: if both apps needed product facts, two copies of retrieval would drift apart. One API means a knowledge update changes both apps with no code change, and the service can be reused for another business.

## 3. How I built it (the order matters)
1. **Read the spec first**, then built in dependency order: Task 1, then 2, then 3, then integration, evaluation and docs.
2. **Task 1 first** because everything depends on it. I got ingestion working on 12 pages, then 80, then the full 1,537, before building features on top.
3. **Contracts before code.** Typed request/response models (pydantic in Python, zod in TypeScript) so the apps could be built and tested independently.
4. **Tests and evaluations alongside each task**, not at the end. 41 automated tests; every task has an evaluation set with real results.
5. **Commit per task**, and a one-command start (`python start.py`, with `setup.py` for a new machine).

## 4. Key design decisions and why
| Decision | Why | Trade-off |
|---|---|---|
| SQLite + hybrid search (TF-IDF + BM25 with rank fusion) | Zero-ops, transactional (rollback of a bad crawl), fits ~10k chunks | Weaker than dense embeddings on paraphrase; there's a seam to swap one in |
| Polite, resumable crawler (manifest, robots.txt, backoff) | Don't hammer the source; survive a crash | More code than a simple loop |
| Abstain instead of guessing | A support bot that invents answers is worse than none | Some valid questions get escalated |
| Guardrails on every draft | Never promise refunds, prices or dates | Replies for money topics are templates |
| "20 unmodified drafts" defined precisely, plus extra safeguards | The brief's rule alone isn't proof of safety | Refunds and complaints can never be automatic |
| Estimates from a work-breakdown template, not a model's number | Auditable; avoids invented commitments | Templates need calibrating on real data |
| Follow-ups compared with the *approved* version only | An AI suggestion must never become an agreed commitment | Need an approval step first |

## 5. Problems I hit and how I fixed them (good interview material)
* **Bot answered "capital of France".** The first answer logic matched "capital letters" in an article. I added out-of-scope detection and a prompt-injection check: pass rate 65% -> 85% on my set.
* **A perfect score that proved nothing.** The rule-based transcript reader scored 30/30 on text I'd tuned it on. I wrote 6 unseen phrasings and the first run was **1/6**. I kept that number and reported it.
* **Quality dropped as the corpus grew** (87.5% on 80 articles, 78.1% on 1,537) because near-duplicate articles compete. Real finding; fix is dense embeddings plus a reranker.
* **A test that checked the wrong rows.** Caught when it failed.

## 6. What I'd say about quality, honestly
* **Verified:** services start from a fresh clone; 41 tests pass; 0 unsafe replies in 52 emails; classification 98.1%; change detection on the follow-up meeting is correct; outages produce a visible safe fallback.
* **Not yet done:** the Claude model comparison (no API key was available; everything measured is the offline fallback), real reviewer feedback data, a second person reviewing my expected answers, and real recorded meetings (mine are role-played scripts).
* **Say this if asked about AI use:** "I built it with Claude Code as my coding assistant and kept the conversation history, as the brief requires. I reviewed the design decisions and the evaluation results myself."

## 7. Likely questions (short answers)
* **Why not a vector database?** Scale and ops cost: one transactional file was enough for ~10k chunks, and I kept the index behind one class so embeddings can be swapped in.
* **How do you stop hallucination?** Answer only from retrieved evidence, cite sources, abstain when evidence is weak, and for transcripts drop any extracted requirement whose quote isn't in the original text.
* **What if the knowledge service is down?** Task 2 sends a holding reply with no product facts and shows a banner; Task 3 marks items unverified. It never turns an outage into "custom work" or "existing feature".
* **How is prompt injection handled?** Retrieved text and emails are treated as data; known patterns are flagged; the guardrails run on the final text regardless.
* **What would you do next?** Run the Claude comparison, add dense embeddings and a reranker, calibrate estimates with real project data, pilot the inbox with real reviewers.

## 8. Demo script (5 minutes)
1. Knowledge Chat: ask a product question, show the citation, then an unanswerable one and the expert queue.
2. Inbox Review: process an email, show evidence and draft, approve; try a refund email to show it makes no promise.
3. Scope Builder: load Meeting A, approve, load Meeting C, open the **Changes** tab; try sending an unapproved version to show the block.
4. Show EVALS.md, including one failure, and say what you'd improve.
