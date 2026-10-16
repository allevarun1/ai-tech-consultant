"""Query orchestration: retrieve -> answer -> cite -> abstain/escalate."""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from retrieval.index import HybridIndex

from .config import Settings
from .llm import LLM
from .models import Citation, Escalation, EscalationResolve, ImageRef, QueryRequest, QueryResponse, QueryStatus
from .store import Store, now

INJECTION_IN_QUESTION = re.compile(r"(ignore (all |any )?(previous|prior|above)|system prompt|reveal your (instructions|prompt)|you are now)", re.I)
UNKNOWN_TERM_LIMIT = 0.3  # fraction of unseen query terms at/above which we abstain

EVAL_ADDITIONS =Path(__file__).resolve().parent.parent / "evaluation" / "escalated_questions.jsonl"


class KnowledgeService:
    def __init__(self, settings: Settings, store: Store, llm: LLM):
        self.s, self.store, self.llm = settings, store, llm
        self._index: Optional[HybridIndex] = None
        self._index_version = -1

    # -- index ---------------------------------------------------------------
    def _rows(self) -> list[dict]:
        rows = [dict(r) for r in self.store.active_chunks()]
        # Approved, unexpired expert answers are separate, versioned records (never merged into articles).
        for r in self.store.q("SELECT * FROM expert_records WHERE approved=1"):
            if r["expires_at"] and r["expires_at"] < datetime.now(timezone.utc).date().isoformat():
                continue
            rows.append({"chunk_id": f"expert:{r['id']}", "url": f"expert-record:{r['id']}", "title": "Expert-approved answer",
                         "heading": r["question"], "text": f"{r['question']}\n{r['answer']}", "images_json": "[]"})
        return rows

    def index(self) -> HybridIndex:
        v = self.store.data_version()
        if self._index is None or v != self._index_version:
            self._index, self._index_version = HybridIndex(self._rows()), v
        return self._index

    # -- query ---------------------------------------------------------------
    def query(self, req: QueryRequest) -> QueryResponse:
        rid = uuid.uuid4().hex[:12]
        hits = self.index().search(req.question, k=req.top_k)
        best = hits[0].score if hits else 0.0

        def insufficient(reason: str) -> QueryResponse:
            esc_id = None
            if req.create_escalation:
                esc_id = self._escalate(req.question, hits, reason)
            return QueryResponse(request_id=rid, status=QueryStatus.INSUFFICIENT_EVIDENCE, evidence_score=round(best, 3),
                                 reason=reason, escalation_id=esc_id, answered_by=self.llm.name,
                                 citations=[self._citation(h) for h in hits[:2]])

        if INJECTION_IN_QUESTION.search(req.question):
            return insufficient("question looks like an attempt to override system instructions; not answerable from the knowledge base")
        unknown = self.index().unknown_ratio(req.question)
        if unknown >= UNKNOWN_TERM_LIMIT:
            return insufficient(f"{unknown:.0%} of the question's key terms never appear in the knowledge base (likely out of scope)")
        if not hits or best < self.s.min_evidence_score:
            return insufficient(f"best retrieval score {best:.2f} is below the evidence threshold {self.s.min_evidence_score:.2f}")

        res = self.llm.answer(req.question, [h.row["text"] for h in hits], req.context)
        if not res.supported or not res.answer:
            return insufficient("retrieved passages do not contain a supported answer")

        used = [hits[i - 1] for i in res.cited if 1 <= i <= len(hits)] or hits[:1]
        images: list[ImageRef] = []
        for h in used:
            for im in json.loads(h.row.get("images_json") or "[]"):
                images.append(ImageRef(url=im["url"], description=im.get("description") or im.get("alt") or None,
                                       uncertain=bool(im.get("uncertain", True)), chunk_id=h.chunk_id))
        return QueryResponse(request_id=rid, status=QueryStatus.ANSWERED, answer=res.answer, citations=[self._citation(h) for h in used],
                             images=images[:6], evidence_score=round(best, 3), answered_by=res.by,
                             expert_record_ids=[int(h.chunk_id.split(":")[1]) for h in used if h.chunk_id.startswith("expert:")])

    @staticmethod
    def _citation(h) -> Citation:
        return Citation(chunk_id=h.chunk_id, url=h.row["url"], title=h.row["title"], section=h.row["heading"], score=round(h.score, 3),
                        excerpt=h.row["text"][:300])

    # -- escalations -----------------------------------------------------------
    def _escalate(self, question: str, hits, reason: str) -> int:
        existing = self.store.q("SELECT id FROM escalations WHERE status='open' AND question=?", (question,))
        if existing:  # same open question: do not create duplicates
            return existing[0]["id"]
        ev = [{"chunk_id": h.chunk_id, "url": h.row["url"], "score": round(h.score, 3)} for h in hits[:3]]
        return self.store.x("INSERT INTO escalations(question,reason,evidence_json,created_at) VALUES(?,?,?,?)",
                            (question, reason, json.dumps(ev), now()))

    def create_escalation(self, question: str, reason: str) -> int:
        return self._escalate(question, [], reason)

    def _esc(self, r) -> Escalation:
        return Escalation(id=r["id"], question=r["question"], reason=r["reason"], status=r["status"], evidence=json.loads(r["evidence_json"]),
                          created_at=r["created_at"], resolved_at=r["resolved_at"])

    def list_escalations(self, status: Optional[str] = None) -> list[Escalation]:
        rows = self.store.q("SELECT * FROM escalations WHERE (?1 IS NULL OR status=?1) ORDER BY id DESC", (status,))
        return [self._esc(r) for r in rows]

    def get_escalation(self, esc_id: int) -> Optional[Escalation]:
        rows = self.store.q("SELECT * FROM escalations WHERE id=?", (esc_id,))
        return self._esc(rows[0]) if rows else None

    def resolve_escalation(self, esc_id: int, body: EscalationResolve) -> Optional[dict]:
        rows = self.store.q("SELECT * FROM escalations WHERE id=?", (esc_id,))
        if not rows:
            return None
        prev = self.store.q("SELECT COALESCE(MAX(version),0) v FROM expert_records WHERE escalation_id=?", (esc_id,))[0]["v"]
        rec_id = self.store.x(
            "INSERT INTO expert_records(escalation_id,version,question,answer,evidence_urls,reviewer,approved,expires_at,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (esc_id, prev + 1, rows[0]["question"], body.answer, json.dumps(body.evidence_urls), body.reviewer, int(body.approve_for_retrieval), body.expires_at, now()))
        self.store.x("UPDATE escalations SET status='resolved', resolved_at=? WHERE id=?", (now(), esc_id))
        if body.approve_for_retrieval:
            self.store.bump_version()
        # the original question becomes a regression case
        EVAL_ADDITIONS.parent.mkdir(parents=True, exist_ok=True)
        with EVAL_ADDITIONS.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"question": rows[0]["question"], "expected_answer": body.answer, "source": f"escalation:{esc_id}"}) + "\n")
        return {"record_id": rec_id, "version": prev + 1, "approved": body.approve_for_retrieval}
