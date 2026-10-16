"""Follow-up comparison against the APPROVED baseline (never against an unapproved draft) - guide step 3.10.

Matching strategy: requirements are matched by their stable topic key (see agents/topics.py). A follow-up statement is
  - REMOVE  when the client drops a topic that exists in the baseline
  - MODIFY  when the client changes a topic that exists in the baseline
  - ADD     when the topic is new; if its topic *group* is not part of the baseline theme it is also flagged
            potentially out of scope (needs a separate quotation) and excluded from the revised totals.
"""
from __future__ import annotations

from typing import Optional

from app.models import ChangeReport, Estimate, Intent, ProjectRepresentation, Requirement, Status

from .topics import group_of


def merge_followup(baseline: list[Requirement], rep: ProjectRepresentation, flagged_out_of_scope_topics: set[str] | None = None) -> tuple[list[Requirement], dict]:
    """Return (new requirement list, bookkeeping for the report). Baseline objects are copied, never mutated."""
    new_by_topic = {r.topic: r for r in rep.requirements}
    base_groups = {group_of(r.topic) for r in baseline if r.intent != Intent.REMOVE}
    max_id = max((int(r.id.split("-")[1]) for r in baseline), default=0)
    out: list[Requirement] = []
    book = {"added": [], "modified": [], "removed": [], "unchanged": [], "out_of_scope": [], "conflicts": []}
    for b in baseline:
        n = new_by_topic.get(b.topic)
        if n is None:
            out.append(b.model_copy(deep=True))
            book["unchanged"].append(b.id)
        elif n.intent == Intent.REMOVE:
            book["removed"].append(b)
        elif n.intent == Intent.MODIFY:
            m = b.model_copy(deep=True)
            m.description = n.description
            m.sources = b.sources + n.sources
            m.priority = n.priority if n.priority.value != "should-have" else b.priority
            m.intent = Intent.MODIFY
            m.estimate_status = "needs review"
            m.existing_solution = m.existing_solution.model_copy(update={"rationale": m.existing_solution.rationale + " (Requirement changed after baseline: re-check.)"})
            out.append(m)
            book["modified"].append((b, m))
            if n.status == Status.DISPUTED:
                book["conflicts"].append({"id": b.id, "reason": "Client both requested and dropped this item"})
        else:
            # restated without change: keep baseline, add the new evidence
            m = b.model_copy(deep=True)
            m.sources = b.sources + n.sources
            out.append(m)
            book["unchanged"].append(b.id)
    base_topics = {b.topic for b in baseline}
    for n in rep.requirements:
        if n.topic in base_topics or n.intent == Intent.REMOVE:
            continue
        max_id += 1
        add = n.model_copy(deep=True)
        add.id = f"REQ-{max_id:03d}"
        add.intent = Intent.ADD
        if group_of(n.topic) not in base_groups or (flagged_out_of_scope_topics and n.topic in flagged_out_of_scope_topics):
            add.in_scope = False
            add.notes.append("Outside the approved baseline theme: needs a separate quotation; not included in revised totals.")
            book["out_of_scope"].append(add)
        else:
            book["added"].append(add)
        out.append(add)
    return out, book


def build_report(baseline_version_id: int, book: dict, new_reqs: list[Requirement], base_est: Estimate, new_est: Estimate,
                 base_deadline: Optional[str], new_deadline: Optional[str], new_deadline_text: Optional[str], schedule: dict) -> ChangeReport:
    def hrs(rid: str) -> dict:
        return new_est.per_requirement.get(rid, {})

    rep = ChangeReport(baseline_version_id=baseline_version_id)
    for a in book["added"]:
        rep.added.append({"id": a.id, "topic": a.topic, "text": a.description, "feasibility": a.existing_solution.category.value, "likely_hours": hrs(a.id).get("likely"), "note": "Estimate and acceptance criteria need review."})
    for b, m in book["modified"]:
        rep.modified.append({"id": b.id, "topic": b.topic, "before": b.description, "after": m.description, "delta_likely_hours": hrs(b.id).get("delta_likely"), "note": "Reassess the affected tasks."})
    for b in book["removed"]:
        rep.removed.append({"id": b.id, "topic": b.topic, "text": b.description, "hours_freed": round(base_est.per_requirement.get(b.id, {}).get("likely", 0), 1)})
    rep.unchanged = book["unchanged"]
    for o in book["out_of_scope"]:
        rep.potentially_out_of_scope.append({"id": o.id, "topic": o.topic, "text": o.description, "action": "Flag for clarification and a separate quotation."})
    rep.unresolved_conflicts = book["conflicts"]
    if new_deadline or base_deadline:
        rep.deadline = {"before": base_deadline, "after": new_deadline or base_deadline, "client_said": new_deadline_text, "schedule_check": schedule}
    rep.estimate_delta = {"baseline_likely": base_est.total_likely, "revised_likely": new_est.total_likely, "delta_likely": round(new_est.total_likely - base_est.total_likely, 1),
                          "baseline_range": [base_est.total_low, base_est.total_high], "revised_range": [new_est.total_low, new_est.total_high]}
    rep.summary = (f"{len(rep.added)} added, {len(rep.modified)} modified, {len(rep.removed)} removed, {len(rep.potentially_out_of_scope)} potentially out of scope. "
                   f"Likely effort moves from {base_est.total_likely:g}h to {new_est.total_likely:g}h ({rep.estimate_delta['delta_likely']:+g}h)."
                   + (f" Deadline moves from {base_deadline} to {new_deadline}." if new_deadline and new_deadline != base_deadline else ""))
    return rep
