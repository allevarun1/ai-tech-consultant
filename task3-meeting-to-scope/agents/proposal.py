"""Agent 5 - Proposal and Review. Assembles the client-ready draft and checks it is consistent with scope + estimate."""
from __future__ import annotations

from typing import Optional

from app.config import Settings
from app.models import ChangeReport, Estimate, Feasibility, Intent, Requirement


def build_proposal(reqs: list[Requirement], scope: dict, est: Estimate, s: Settings, *, project_name: str, client: str, schedule: dict, deadline_text: Optional[str], change: Optional[ChangeReport] = None, version_no: int = 1) -> dict:
    live = [r for r in reqs if r.intent != Intent.REMOVE and r.in_scope]
    unverified = [r for r in live if r.existing_solution.category == Feasibility.CUSTOM or r.existing_solution.needs_expert]
    clarify = [r for r in live if r.existing_solution.category == Feasibility.CLARIFY]
    existing = [r for r in live if r.existing_solution.category in (Feasibility.EXISTING, Feasibility.LIMITED)]

    summary = (f"{s.company} proposes to deliver {len(scope['agreed_requirements'])} agreed improvement(s) to {project_name} for {client}. "
               f"{len(existing)} of them can start from capabilities the platform already provides, which keeps cost down. "
               f"Estimated effort is {est.total_likely:g} hours (range {est.total_low:g}-{est.total_high:g}), "
               f"{len(clarify)} item(s) need clarification, and {len(unverified)} item(s) still need product-expert confirmation.")
    timeline = {
        "client_requested": deadline_text,
        "assessment": schedule,
        "statement": ("The requested date looks achievable at the likely estimate, subject to the open items." if schedule.get("feasible_at_likely_hours")
                      else "The requested date may be at risk at the configured team capacity; this must be discussed before any date is agreed.") if schedule.get("requested") else
                     f"At the configured capacity the work would take about {est.weeks_at_capacity:g} week(s) after kickoff. This is not a delivery commitment.",
    }
    sections = {
        "executive_summary": summary,
        "client_problem_and_solution": {"problem": scope["current_problem"], "solution": [p["approach"] + f" [{p['id']}]" for p in scope["proposed_solution"]]},
        "scope_and_deliverables": scope["deliverables"],
        "timeline_and_milestones": timeline,
        "effort_and_pricing_assumptions": {"hours": {"low": est.total_low, "likely": est.total_likely, "high": est.total_high, "contingency": est.contingency_hours}, "pricing": est.pricing, "notes": est.notes},
        "client_responsibilities": scope["client_responsibilities"],
        "exclusions_and_dependencies": {"exclusions": scope["exclusions"], "dependencies": scope["assumptions_and_dependencies"]},
        "risks_and_open_questions": scope["open_questions_and_risks"],
        "approval_and_next_steps": ["Review this draft and the open questions.", "Confirm or correct the requirements marked unclear.", "Approve the scope to proceed to a formal quotation."],
    }
    if change:
        sections["changes_since_approved_version"] = change.summary
    issues = consistency_check(live, scope, est, sections)
    return {"version_label": f"Proposal draft v{version_no}", "status_banner": "DRAFT - not approved, not for sending", "sections": sections, "consistency_issues": issues, "markdown": render_markdown(sections, project_name, client, version_no)}


def consistency_check(live: list[Requirement], scope: dict, est: Estimate, sections: dict) -> list[str]:
    """The proposal must not promise more than scope and estimate support (guide step 3.9)."""
    issues = []
    in_est = {t.requirement_id for t in est.tasks}
    for d in scope["deliverables"]:
        if d["id"] not in in_est:
            issues.append(f"Deliverable {d['id']} has no estimate.")
    for r in live:
        if r.existing_solution.category == Feasibility.CUSTOM and r.id in {d["id"] for d in scope["deliverables"]} and "UNVERIFIED" not in " ".join(p["approach"] for p in scope["proposed_solution"] if p["id"] == r.id):
            issues.append(f"{r.id} is presented as agreed custom work although product coverage is unverified.")
    text = str(sections).lower()
    for r in live:
        if r.existing_solution.category == Feasibility.CLARIFY and r.id in {d["id"] for d in scope["deliverables"]}:
            issues.append(f"{r.id} needs clarification but appears as a firm deliverable.")
    if "guarantee" in text:
        issues.append("Proposal text contains the word 'guarantee'.")
    return issues


def render_markdown(sec: dict, project_name: str, client: str, version_no: int) -> str:
    L = [f"# Proposal draft v{version_no}: {project_name}", f"*Prepared for {client}. DRAFT - not approved, not for sending.*", "", "## Executive summary", sec["executive_summary"], "", "## Client problem and proposed solution"]
    L += [f"- Problem: {p}" for p in sec["client_problem_and_solution"]["problem"]] + [f"- {x}" for x in sec["client_problem_and_solution"]["solution"]]
    L += ["", "## Scope and deliverables"] + [f"- **{d['id']} {d['deliverable']}** - verified by: {d['verified_by']}" for d in sec["scope_and_deliverables"]]
    t = sec["timeline_and_milestones"]
    L += ["", "## Timeline and milestones", t["statement"]]
    if t.get("client_requested"):
        L.append(f"Client requested: \"{t['client_requested']}\"")
    e = sec["effort_and_pricing_assumptions"]
    L += ["", "## Estimated effort and pricing assumptions", f"- Hours: {e['hours']['likely']:g} likely ({e['hours']['low']:g}-{e['hours']['high']:g}), plus {e['hours']['contingency']:g} contingency.",
          f"- Indicative price: {e['pricing']['currency']} {e['pricing']['indicative_net']:,.2f} net at {e['pricing']['hourly_rate']:g}/h (configured rate). {e['pricing']['disclaimer']}"]
    L += [f"- {n}" for n in e["notes"]]
    L += ["", "## Client responsibilities"] + [f"- {c}" for c in sec["client_responsibilities"]]
    L += ["", "## Exclusions and dependencies"] + [f"- Excluded: {x}" for x in sec["exclusions_and_dependencies"]["exclusions"]] + [f"- Dependency: {x}" for x in sec["exclusions_and_dependencies"]["dependencies"]]
    q = sec["risks_and_open_questions"]
    L += ["", "## Risks and open questions"] + [f"- Risk: {r}" for r in q["risks"]] + [f"- Question: {x}" for x in q["questions"]]
    if "changes_since_approved_version" in sec:
        L += ["", "## Changes since the approved version", sec["changes_since_approved_version"]]
    L += ["", "## Approval and next steps"] + [f"- {x}" for x in sec["approval_and_next_steps"]]
    return "\n".join(L) + "\n"
