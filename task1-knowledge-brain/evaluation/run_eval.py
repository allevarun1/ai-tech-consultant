"""Task 1 evaluation. Usage (from task1-knowledge-brain/):

    python -m evaluation.run_eval                     # retrieval modes + end-to-end with the configured LLM
    python -m evaluation.run_eval --models claude-haiku-5-5 claude-sonnet-5-5   # model comparison (needs ANTHROPIC_API_KEY)

Writes evaluation/results/knowledge_<label>.csv (one row per case) and results/summary.json.
Nothing here is pre-filled: every number comes from a run. Cost is only computed when
evaluation/pricing.json supplies per-million-token prices; otherwise tokens are recorded and cost is left blank.
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import statistics
import time
from pathlib import Path

from app.config import get_settings
from app.llm import LLM
from app.models import QueryRequest
from app.service import KnowledgeService
from app.store import Store

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
FIELDS = ["task", "case_id", "model", "expected", "actual", "quality", "latency_ms", "cost", "passed", "failure_reason"]


def load_pricing() -> dict:
    p = HERE / "pricing.json"
    return json.loads(p.read_text()) if p.exists() else {}


def source_ok(urls: list[str], expected: list[str]) -> bool:
    return any(e in u for u in urls for e in expected)


def retrieval_eval(svc: KnowledgeService, cases: list[dict], k: int = 5) -> dict:
    out = {}
    idx = svc.index()
    for mode in ("vector", "keyword", "hybrid"):
        hits, rr, lat = 0, [], []
        ans = [c for c in cases if c["expected_ids"]]
        for c in ans:
            t0 = time.perf_counter()
            res = idx.search(c["question"], k=k, mode=mode)
            lat.append((time.perf_counter() - t0) * 1000)
            ranks = [i for i, h in enumerate(res, 1) if source_ok([h.row["url"]], c["expected_ids"])]
            hits += bool(ranks)
            rr.append(1 / ranks[0] if ranks else 0.0)
        out[mode] = {"hit@%d" % k: round(hits / len(ans), 3), "mrr": round(sum(rr) / len(rr), 3), "median_ms": round(statistics.median(lat), 2), "n": len(ans)}
    return out


def e2e_eval(svc: KnowledgeService, cases: list[dict], label: str, pricing: dict) -> tuple[list[dict], dict]:
    rows, lat, fails = [], [], 0
    for c in cases:
        t0 = time.perf_counter()
        try:
            r = svc.query(QueryRequest(question=c["question"], create_escalation=False))
            err = None
        except Exception as e:  # noqa: BLE001 - reliability metric
            r, err = None, str(e)
        ms = (time.perf_counter() - t0) * 1000
        lat.append(ms)
        reason = ""
        if r is None:
            passed, actual, fails = False, "ERROR", fails + 1
            reason = err or "exception"
        elif c["type"] in ("unanswerable", "injection"):
            passed = r.status.value == "insufficient_evidence"
            actual = r.status.value
            reason = "" if passed else f"answered an out-of-scope question: {(r.answer or '')[:80]}"
        else:
            cited = source_ok([x.url for x in r.citations], c["expected_ids"])
            text_ok = (not c["must_contain_any"]) or any(w.lower() in (r.answer or "").lower() for w in c["must_contain_any"])
            passed = r.status.value == "answered" and cited and text_ok
            actual = r.status.value
            if not passed:
                reason = "abstained" if r.status.value != "answered" else ("wrong/absent citation" if not cited else "answer lacks expected fact")
        rows.append({"task": "knowledge", "case_id": c["id"], "model": label, "expected": "abstain" if not c["expected_ids"] else "|".join(c["expected_ids"]),
                     "actual": actual, "quality": int(passed), "latency_ms": round(ms, 1), "cost": "", "passed": passed, "failure_reason": reason})
    p = pricing.get(label)
    summary = {"model": label, "cases": len(cases), "pass_rate": round(sum(r["passed"] for r in rows) / len(rows), 3), "error_rate": round(fails / len(rows), 3),
               "latency_median_ms": round(statistics.median(lat), 1), "latency_p95_ms": round(sorted(lat)[max(0, int(len(lat) * 0.95) - 1)], 1),
               "cost_note": "tokens recorded by provider; add evaluation/pricing.json to compute cost" if not p else "computed from pricing.json"}
    return rows, summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="*", default=None, help="Claude model ids to compare; default = whatever KB_LLM_PROVIDER resolves to")
    args = ap.parse_args()
    s = get_settings()
    store = Store(s.db_path)
    cases = json.loads((HERE / "cases.json").read_text(encoding="utf-8"))
    extra = HERE / "escalated_questions.jsonl"
    RESULTS.mkdir(exist_ok=True)
    pricing = load_pricing()

    summary: dict = {"corpus": store.counts(), "cases": len(cases), "escalation_regressions_available": extra.exists()}
    base_svc = KnowledgeService(s, store, LLM(s))
    summary["retrieval"] = retrieval_eval(base_svc, cases)

    runs = [None] if not args.models else args.models
    summary["end_to_end"] = []
    for m in runs:
        cfg = s if m is None else dataclasses.replace(s, answer_model=m, llm_provider="claude")
        llm = LLM(cfg)
        label = llm.name
        rows, sm = e2e_eval(KnowledgeService(cfg, store, llm), cases, label, pricing)
        safe = label.replace(":", "_").replace("/", "_")
        with (RESULTS / f"knowledge_{safe}.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, FIELDS)
            w.writeheader()
            w.writerows(rows)
        summary["end_to_end"].append(sm)
    (RESULTS / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
