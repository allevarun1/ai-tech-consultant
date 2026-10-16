"""Orchestrates the five agents: Transcript Analyst -> Feasibility -> Scope -> Estimation -> Proposal/Review."""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Optional

from agents.changes import build_report, merge_followup
from agents.estimator import build_estimate, schedule_check
from agents.feasibility import check_requirement
from agents.proposal import build_proposal
from agents.scope import build_scope
from agents.topics import BY_KEY
from agents.transcript import analyze_meeting

from .config import Settings
from .kb_client import KbClient
from .llm import LLM
from .models import ChangeReport, EditRequest, Estimate, EstimateTask, Feasibility, Intent, MeetingCreate, ProjectRepresentation, Requirement
from .store import Store

KICKOFF_DELAY_DAYS = 7  # assumption stated in the schedule check: work starts one week after the meeting


class PipelineError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def annotate_dependencies(r: Requirement, rep: ProjectRepresentation) -> None:
    t = BY_KEY.get(r.topic)
    quotes = " ".join(s.quote for s in r.sources)
    deps: list[str] = []
    if t and t.external:
        deps.append("External payment provider: confirm support and obtain sandbox credentials")
        if any("payment account" in c.quote.lower() for c in rep.client_commitments):
            deps.append("Client to provide access to the current payment account")
    if r.topic == "notifications.order" and re.search(r"\bsms\b|text message", quotes, re.I):
        deps.append("SMS provider: the client has not chosen one yet")
    r.dependencies = deps


class ScopePipeline:
    def __init__(self, settings: Settings, store: Store, kb: KbClient, llm: LLM):
        self.s, self.store, self.kb, self.llm = settings, store, kb, llm

    # ---- main entry ---------------------------------------------------------------------------
    def process_meeting(self, project_id: int, m: MeetingCreate) -> int:
        project = self.store.project(project_id)
        if not project:
            raise PipelineError("project not found", 404)
        existing = self.store.versions(project_id)
        baseline_v = None
        if m.kind == "initial" and existing:
            raise PipelineError("this project already has a scope; record later meetings as 'follow_up'", 409)
        if m.kind == "follow_up":
            if not existing:
                raise PipelineError("there is no scope yet; record an 'initial' meeting first", 409)
            baseline_v = self.store.approved_baseline(project_id)
            if baseline_v is None:
                raise PipelineError("follow-up changes are compared against the APPROVED baseline, and no version is approved yet. A reviewer must approve the current draft first.", 409)

        meeting_id = self.store.add_meeting(project_id, m.title, m.date, m.kind, m.transcript, "pending")
        baseline_reqs = [Requirement(**r) for r in baseline_v["requirements"]] if baseline_v else []
        baseline_topics = {r.topic for r in baseline_reqs if r.intent != Intent.REMOVE}
        try:
            size = m.chunk_turns or self.s.chunk_size
            rep, meta, turns = analyze_meeting(m.transcript, meeting_id, m.kind, baseline_topics, llm=self.llm, chunk_size=size, overlap=min(self.s.chunk_overlap, size // 2))
        except ValueError as e:
            raise PipelineError(str(e)) from e
        self.store.x("UPDATE meetings SET extractor=? WHERE id=?", (meta.get("extractor", "rules"), meeting_id))
        self.store.audit("agent", "meeting_analyzed", project_id, None, {"meeting_id": meeting_id, "turns": len(turns), "chunks": rep.chunks_processed, "requirements": len(rep.requirements), "extractor": meta.get("extractor")})

        book = None
        if m.kind == "initial":
            reqs = rep.requirements
            for i, r in enumerate(reqs, 1):
                r.id = f"REQ-{i:03d}"
            to_check = reqs
        else:
            reqs, book = merge_followup(baseline_reqs, rep)
            changed_ids = {id(x) for x in book["added"]} | {id(mm) for _, mm in book["modified"]}
            to_check = [r for r in reqs if r.in_scope and id(r) in changed_ids]  # unchanged items keep their earlier feasibility result
        for r in reqs:
            annotate_dependencies(r, rep)
        for r in to_check:
            r.existing_solution = check_requirement(r, self.kb, escalate=True)

        base_est = Estimate(**baseline_v["estimate"]) if baseline_v else None
        deadline_iso = rep.deadline_iso or (baseline_v["deadline_iso"] if baseline_v else None)
        deadline_text = rep.deadline_text or (baseline_v["representation"].get("deadline_text") if baseline_v else None)
        est = build_estimate(reqs, self.s, baseline=base_est)
        start = (date.fromisoformat(m.date) + timedelta(days=KICKOFF_DELAY_DAYS)).isoformat()
        schedule = schedule_check(est, start, deadline_iso)
        prev_excl = baseline_v["scope"]["exclusions"] if baseline_v else None
        scope = build_scope(reqs, rep, turns, project_name=project["name"], client=project["client"], previous_exclusions=prev_excl)
        change: Optional[ChangeReport] = None
        if book is not None and base_est is not None:
            change = build_report(baseline_v["id"], book, reqs, base_est, est, baseline_v["deadline_iso"], rep.deadline_iso, rep.deadline_text, schedule)
        version_no = len(existing) + 1
        proposal = build_proposal(reqs, scope, est, self.s, project_name=project["name"], client=project["client"], schedule=schedule, deadline_text=deadline_text, change=change, version_no=version_no)
        vid = self.store.add_version(project_id, meeting_id, [r.model_dump(mode="json") for r in reqs], scope, est.model_dump(mode="json"), proposal,
                                     change.model_dump(mode="json") if change else None, rep.model_dump(mode="json"), deadline_iso)
        return vid

    # ---- human edit of estimate hours (invalidates approval) ----------------------------------------
    def apply_edit(self, vid: int, e: EditRequest) -> dict:
        v = self.store.version(vid)
        if not v:
            raise PipelineError("version not found", 404)
        if v["status"] == "superseded":
            raise PipelineError("superseded versions are read-only", 409)
        est = Estimate(**v["estimate"])
        by_key = {f"{t.requirement_id}|{t.name}": t for t in est.tasks}
        for key, hrs in e.task_hours.items():
            t = by_key.get(key)
            if t is None:
                raise PipelineError(f"unknown task '{key}'")
            lo, li, hi = float(hrs.get("low", t.low)), float(hrs.get("likely", t.likely)), float(hrs.get("high", t.high))
            if not (0 <= lo <= li <= hi):
                raise PipelineError(f"hours for '{key}' must satisfy 0 <= low <= likely <= high")
            old = t.likely
            t.low, t.likely, t.high = lo, li, hi
            pr = est.per_requirement.get(t.requirement_id)
            if pr:
                pr["likely"] = round(sum(x.likely for x in est.tasks if x.requirement_id == t.requirement_id), 1)
                pr["low"] = round(sum(x.low for x in est.tasks if x.requirement_id == t.requirement_id), 1)
                pr["high"] = round(sum(x.high for x in est.tasks if x.requirement_id == t.requirement_id), 1)
                if "delta_likely" in pr:
                    pr["delta_likely"] = round(pr["delta_likely"] + (li - old), 1)
        est.total_low, est.total_likely, est.total_high = (round(sum(getattr(t, k) for t in est.tasks), 1) for k in ("low", "likely", "high"))
        est.contingency_hours = round(est.total_likely * 0.10, 1) if est.contingency_hours else 0.0
        est.weeks_at_capacity = round((est.total_likely + est.contingency_hours) / est.capacity_hours_per_week, 1)
        net = round((est.total_likely + est.contingency_hours) * est.pricing["hourly_rate"], 2)
        est.pricing.update({"indicative_net": net, "indicative_gross": round(net * (1 + est.pricing["tax_rate"]), 2)})
        est.notes.append(f"Hours edited by {e.reviewer}: {e.note or 'no note'}")
        reqs = [Requirement(**r) for r in v["requirements"]]
        project = self.store.project(v["project_id"])
        change = ChangeReport(**v["change"]) if v["change"] else None
        if change:
            change.estimate_delta["revised_likely"] = est.total_likely
            base = self.store.version(change.baseline_version_id)
            change.estimate_delta["delta_likely"] = round(est.total_likely - Estimate(**base["estimate"]).total_likely, 1)
        rep = ProjectRepresentation(**v["representation"])
        schedule = schedule_check(est, (date.fromisoformat(self.store.q("SELECT date FROM meetings WHERE id=?", (v["meeting_id"],))[0]["date"]) + timedelta(days=KICKOFF_DELAY_DAYS)).isoformat(), v["deadline_iso"])
        proposal = build_proposal(reqs, v["scope"], est, self.s, project_name=project["name"], client=project["client"], schedule=schedule, deadline_text=rep.deadline_text, change=change, version_no=v["version_no"])
        self.store.update_version_content(vid, v["requirements"], v["scope"], est.model_dump(mode="json"), proposal, change.model_dump(mode="json") if change else None)
        self.store.audit(e.reviewer, "estimate_edited", v["project_id"], vid, {"tasks": list(e.task_hours), "note": e.note, "approval_cleared": v["status"] == "approved"})
        return self.store.version(vid)
