"""Task 3 tests. The Knowledge Brain is replaced by an httpx.MockTransport, so nothing here needs the network."""
from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from agents.changes import merge_followup
from agents.estimator import build_estimate, estimate_requirement
from agents.feasibility import check_requirement
from agents.transcript import analyze_meeting, chunk_turns, extract_chunk_llm, normalize_deadline, parse_transcript
from app.config import Settings
from app.kb_client import KbClient
from app.llm import LLM
from app.main import create_app
from app.models import Feasibility, Intent, Requirement, Status
from app.store import Store

T = Path(__file__).resolve().parent.parent / "transcripts"
A = (T / "meeting_a_website_improvements.txt").read_text(encoding="utf-8")
C = (T / "meeting_c_followup_to_a.txt").read_text(encoding="utf-8")


def fake_kb(down: bool = False, calls: list | None = None) -> KbClient:
    """Answers from a tiny fixed 'documentation set' keyed on words in the question."""
    docs = {"radius": ("How To Edit The Default Search Results Radius", "The default radius is 50 miles and can be edited."),
            "predictive": ("Predictive Keyword Search", "Suggestions appear while typing in the search box."),
            "layouts": ("Search Layouts", "List, grid and map view are available."),
            "custom information": ("Adding Custom Information to your Member Search Results", "You can add ratings and other custom information to results."),
            "gateway": ("Finance Payment Settings - Setting Up A Payment Gateway", "Choose a payment gateway such as Authorize.net. Not all gateways permit refunds.")}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if down:
            return httpx.Response(503, json={})
        q = json.loads(req.content)["question"].lower()
        if calls is not None:
            calls.append(q)
        for k, (title, text) in docs.items():
            if k in q or (k == "layouts" and "map view" in q) or (k == "radius" and "distance" in q) or (k == "gateway" and "payment" in q):
                return httpx.Response(200, json={"request_id": "r", "status": "answered", "answer": text, "evidence_score": 0.8, "answered_by": "stub",
                                                 "citations": [{"chunk_id": "c", "url": f"https://help.test/{k}", "title": title, "section": "s", "score": 0.8, "excerpt": text}]})
        return httpx.Response(200, json={"request_id": "r", "status": "insufficient_evidence", "answer": None, "citations": [], "evidence_score": 0.1, "reason": "none"})

    return KbClient("http://kb.test", transport=httpx.MockTransport(handler), retries=1)


@pytest.fixture()
def client(tmp_path):
    s = Settings(db_path=str(tmp_path / "s.sqlite"), anthropic_key="")
    return TestClient(create_app(s, Store(s.db_path), fake_kb(), LLM(s)))


def mkproject(c) -> int:
    return c.post("/projects", json={"name": "Search improvements", "client": "Harbor City Business Directory"}).json()["id"]


def meeting(c, pid, text, kind, date):
    return c.post(f"/projects/{pid}/meetings", json={"title": f"Meeting {kind}", "date": date, "kind": kind, "transcript": text})


# ---- transcript analyst ---------------------------------------------------------------------------------------
def test_parse_keeps_speakers_timestamps_and_roles():
    meta, turns = parse_transcript(A)
    assert meta["date"] == "2026-10-05" and turns[0].ts == "00:00:05" and turns[0].role.value == "consultant" and turns[1].role.value == "client"


def test_extracts_priorities_status_and_source_quotes():
    rep, _, turns = analyze_meeting(A, 1, "initial")
    got = {r.topic: r for r in rep.requirements}
    assert got["search.filters.category"].priority.value == "must-have" and got["search.filters.category"].status == Status.CONFIRMED
    assert got["mobile.experience"].status == Status.UNCLEAR          # vague + hedged: not turned into a commitment
    assert got["search.ai_recommendations"].priority.value == "optional" and got["search.ai_recommendations"].status == Status.UNCLEAR
    assert "search.filters.general" not in got                        # superseded by specific filters
    by_id = {t.id: t for t in turns}
    for r in rep.requirements:                                         # every requirement links to the exact client passage
        assert r.sources and all(s.quote in by_id[s.turn_id].text for s in r.sources)
    assert rep.deadline_iso == "2027-03-31" and any("analytics" in c.quote for c in rep.client_commitments) and rep.exclusions


def test_deadline_normalisation():
    assert normalize_deadline("by the end of March", "2026-10-05") == "2027-03-31"
    assert normalize_deadline("before the 15th of December", "2026-10-08") == "2026-12-15"


def test_long_transcript_is_processed_in_stages_with_same_result():
    lines = A.splitlines()
    turns = [ln for ln in lines if ln.startswith("[")]
    head = [ln for ln in lines if not ln.startswith("[")]
    long_text = "\n".join(head + turns * 3)
    one, _, _ = analyze_meeting(long_text, 1, "initial", chunk_size=500)
    staged, _, ts = analyze_meeting(long_text, 1, "initial", chunk_size=12, overlap=4)
    assert staged.chunks_processed > 1 and one.chunks_processed == 1
    assert {r.topic for r in one.requirements} == {r.topic for r in staged.requirements}
    assert len(chunk_turns(ts, 12, 4)[1]) == 12 and chunk_turns(ts, 12, 4)[1][0].id == chunk_turns(ts, 12, 4)[0][-4].id  # overlap


def test_llm_output_is_validated_against_the_source():
    """A model that invents a quote must not be able to add a requirement."""
    _, turns = parse_transcript(A)

    class Fake:
        available = True

        def json(self, *_a, **_k):
            real = next(t for t in turns if "map view" in t.text)
            return {"requirements": [
                {"topic": "search.map_view", "description": "map", "quote": "we need a map view of the results", "turn_id": real.id, "priority": "must-have", "status": "confirmed", "intent": "add"},
                {"topic": "other", "description": "free lifetime licence", "quote": "we want a free lifetime licence", "turn_id": real.id, "priority": "must-have", "status": "confirmed", "intent": "add"}],
                "decisions": [], "exclusions": [], "client_commitments": [], "deadlines": [], "open_questions": []}

    res = extract_chunk_llm(Fake(), turns, 1, "initial", set())
    assert [r.topic for r in res.requirements] == ["search.map_view"]


# ---- feasibility ------------------------------------------------------------------------------------------------------
def test_feasibility_existing_custom_clarify_and_outage():
    kb = fake_kb()
    mk = lambda topic, status=Status.CONFIRMED: Requirement(topic=topic, description=topic, status=status)  # noqa: E731
    assert check_requirement(mk("search.filters.distance"), kb).category == Feasibility.EXISTING
    unverified = check_requirement(mk("homepage.redesign"), kb)
    assert unverified.category == Feasibility.CUSTOM and unverified.needs_expert and not unverified.verified
    assert check_requirement(mk("mobile.experience", Status.UNCLEAR), kb).category == Feasibility.CLARIFY
    down = check_requirement(mk("search.filters.distance"), fake_kb(down=True))
    assert down.category == Feasibility.CLARIFY and down.kb_error                       # outage is NOT turned into 'custom work'
    calls: list = []
    check_requirement(mk("mobile.experience", Status.UNCLEAR), fake_kb(calls=calls))
    assert calls == []                                                                   # vague items are not sent to the KB


def test_provider_specific_claims_are_not_assumed():
    r = Requirement(topic="payments.integration", description="x", status=Status.CONFIRMED)
    from app.models import SourceRef
    r.sources = [SourceRef(meeting_id=1, turn_id=1, ts="00:00:01", speaker="Marcus", quote="We want to accept card payments through Stripe.")]
    f = check_requirement(r, fake_kb())
    assert f.category == Feasibility.LIMITED and f.needs_expert and "Stripe" in f.rationale


# ---- estimation ---------------------------------------------------------------------------------------------------------
def test_existing_capability_is_cheaper_than_custom_and_vague_is_discovery_only():
    def est(topic, cat):
        r = Requirement(id="REQ-001", topic=topic, description=topic, status=Status.CONFIRMED)
        r.existing_solution.category = cat
        return sum(t.likely for t in estimate_requirement(r)), r
    cfg, _ = est("search.filters.distance", Feasibility.EXISTING)
    cus, rc = est("search.filters.distance", Feasibility.CUSTOM)
    vague, rv = est("mobile.experience", Feasibility.CLARIFY)
    assert cfg < cus / 2 and vague <= 5 and rc.estimate_status == "needs review" and rv.estimate_status == "needs review"
    for t in estimate_requirement(rc):
        assert t.low <= t.likely <= t.high and t.reasoning


def test_totals_pricing_and_out_of_scope_exclusion():
    s = Settings(hourly_rate=100, tax_rate=0.1, currency="EUR", capacity_hours_per_week=20)
    a = Requirement(id="REQ-001", topic="search.map_view", description="m", status=Status.CONFIRMED)
    a.existing_solution.category = Feasibility.EXISTING
    b = Requirement(id="REQ-002", topic="homepage.redesign", description="h", status=Status.CONFIRMED, in_scope=False)
    e = build_estimate([a, b], s)
    assert "REQ-002" not in e.per_requirement and e.total_likely == 3.0
    assert e.pricing["indicative_net"] == 300.0 and e.pricing["indicative_gross"] == 330.0 and e.pricing["currency"] == "EUR"
    assert "NOT an approved quote" in e.pricing["disclaimer"]


# ---- the whole flow through the API --------------------------------------------------------------------------------------------
def test_initial_meeting_produces_scope_estimate_proposal(client):
    pid = mkproject(client)
    v = meeting(client, pid, A, "initial", "2026-10-05").json()
    assert v["status"] == "draft" and v["version_no"] == 1
    ids = [r["id"] for r in v["requirements"]]
    assert ids == [f"REQ-{i:03d}" for i in range(1, len(ids) + 1)]
    assert v["scope"]["exclusions"] and v["scope"]["pending_clarification"] and v["scope"]["deliverables"]
    assert all(d["verified_by"] and "not yet testable" not in d["verified_by"] for d in v["scope"]["deliverables"])
    assert v["estimate"]["total_likely"] > 0 and v["proposal"]["consistency_issues"] == [] and "DRAFT" in v["proposal"]["markdown"]
    assert "Indicative price" in v["proposal"]["markdown"]


def test_followup_requires_approved_baseline_and_detects_changes(client):
    pid = mkproject(client)
    v1 = meeting(client, pid, A, "initial", "2026-10-05").json()
    refused = meeting(client, pid, C, "follow_up", "2026-10-19")
    assert refused.status_code == 409 and "APPROVED" in refused.json()["detail"]
    assert client.post(f"/versions/{v1['id']}/send", json={"to": "d@x.example"}).status_code == 409
    client.post(f"/versions/{v1['id']}/approve", json={"reviewer": "Priya Raman"})
    v2 = meeting(client, pid, C, "follow_up", "2026-10-19").json()
    ch = v2["change"]
    assert sorted(a["topic"] for a in ch["added"]) == ["mobile.click_to_call", "search.saved_alerts"]
    assert [r["topic"] for r in ch["removed"]] == ["search.ai_recommendations"] and ch["removed"][0]["hours_freed"] >= 0
    assert [m["topic"] for m in ch["modified"]] == ["search.filters.category"]
    assert [o["topic"] for o in ch["potentially_out_of_scope"]] == ["homepage.redesign"]
    assert (ch["deadline"]["before"], ch["deadline"]["after"]) == ("2027-03-31", "2027-04-30")
    by_topic = {r["topic"]: r for r in v2["requirements"]}
    assert by_topic["search.autocomplete"]["id"] == next(r["id"] for r in v1["requirements"] if r["topic"] == "search.autocomplete")  # stable ids
    assert by_topic["homepage.redesign"]["in_scope"] is False and "REQ" not in "".join(t["requirement_id"] for t in v2["estimate"]["tasks"] if t["requirement_id"] == by_topic["homepage.redesign"]["id"])
    assert ch["estimate_delta"]["delta_likely"] != 0 and v2["version_no"] == 2


def test_followup_compares_with_approved_version_not_a_later_unapproved_draft(client):
    pid = mkproject(client)
    v1 = meeting(client, pid, A, "initial", "2026-10-05").json()
    client.post(f"/versions/{v1['id']}/approve", json={"reviewer": "Priya Raman"})
    v2 = meeting(client, pid, C, "follow_up", "2026-10-19").json()       # draft, NOT approved (it drops AI)
    v3 = meeting(client, pid, C, "follow_up", "2026-10-20").json()       # compared with v1 again, so AI is still 'removed' not 'unknown'
    assert v3["change"]["baseline_version_id"] == v1["id"] == v2["change"]["baseline_version_id"]
    assert [r["topic"] for r in v3["change"]["removed"]] == ["search.ai_recommendations"]


def test_approval_gate_edit_reset_and_idempotent_send(client):
    pid = mkproject(client)
    v = meeting(client, pid, A, "initial", "2026-10-05").json()
    ap = client.post(f"/versions/{v['id']}/approve", json={"reviewer": "Priya Raman"}).json()
    assert ap["status"] == "approved" and ap["approved_by"] == "Priya Raman" and ap["approved_at"]
    t = v["estimate"]["tasks"][0]
    key = f"{t['requirement_id']}|{t['name']}"
    bad = client.post(f"/versions/{v['id']}/edit", json={"reviewer": "estimator", "task_hours": {key: {"low": 5, "likely": 2, "high": 9}}})
    assert bad.status_code == 422                                           # low <= likely <= high enforced
    ed = client.post(f"/versions/{v['id']}/edit", json={"reviewer": "estimator", "task_hours": {key: {"low": 1, "likely": 6, "high": 9}}, "note": "ours is harder"}).json()
    assert ed["status"] == "draft" and ed["approved_by"] is None            # editing invalidates approval
    assert ed["estimate"]["total_likely"] != v["estimate"]["total_likely"]
    assert client.post(f"/versions/{v['id']}/send", json={"to": "d@x.example"}).status_code == 409
    client.post(f"/versions/{v['id']}/approve", json={"reviewer": "Priya Raman"})
    one = client.post(f"/versions/{v['id']}/send", json={"to": "d@x.example"}).json()
    two = client.post(f"/versions/{v['id']}/send", json={"to": "d@x.example"}).json()
    assert one["already_sent"] is False and two["already_sent"] is True
    log = client.get(f"/projects/{pid}").json()["audit"]
    assert {"version_approved", "estimate_edited", "version_sent", "meeting_analyzed"} <= {a["event"] for a in log}


def test_validation_and_outage_are_visible_not_silent(tmp_path):
    s = Settings(db_path=str(tmp_path / "s.sqlite"))
    c = TestClient(create_app(s, Store(s.db_path), fake_kb(down=True), LLM(s)))
    pid = mkproject(c)
    assert c.post(f"/projects/{pid}/meetings", json={"title": "x", "date": "05/10/2026", "kind": "initial", "transcript": A}).status_code == 422
    assert meeting(c, pid, "no timestamps here at all, just a paragraph of text that is long enough to pass length validation", "initial", "2026-10-05").status_code == 422
    v = meeting(c, pid, A, "initial", "2026-10-05").json()
    # an outage is recorded per requirement and never silently turned into "existing" or "custom"
    outage = [r for r in v["requirements"] if r["existing_solution"]["kb_error"]]
    assert outage and all(r["existing_solution"]["category"] == "needs_clarification" and not r["existing_solution"]["verified"] for r in outage)
    assert "unavailable" in " ".join(v["scope"]["open_questions_and_risks"]["risks"]).lower()


def test_merge_followup_marks_removed_and_new_group_out_of_scope():
    rep, _, _ = analyze_meeting(A, 1, "initial")
    for i, r in enumerate(rep.requirements, 1):
        r.id = f"REQ-{i:03d}"
    fu, _, _ = analyze_meeting(C, 2, "follow_up", {r.topic for r in rep.requirements})
    new, book = merge_followup(rep.requirements, fu)
    assert [b.topic for b in book["removed"]] == ["search.ai_recommendations"] and "search.ai_recommendations" not in [r.topic for r in new]
    assert [o.topic for o in book["out_of_scope"]] == ["homepage.redesign"] and all(r.intent != Intent.REMOVE for r in new)
