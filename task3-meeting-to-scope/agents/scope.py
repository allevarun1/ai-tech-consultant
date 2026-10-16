"""Agent 3 - Scope. Builds the scope-of-work document (guide step 3.7) from structured requirements, feasibility and exclusions."""
from __future__ import annotations

import re
from typing import Optional

from app.models import Feasibility, Intent, ProjectRepresentation, Requirement, Role, Status, Turn

from .topics import BY_KEY

ACCEPTANCE = {
    "search.filters.category": "A visitor can choose a category on the search page and the results list shows only businesses in that category.",
    "search.filters.distance": "A visitor can limit results to businesses within a chosen distance of a location, and results outside it are not shown.",
    "search.autocomplete": "While typing in the search box, suggestions appear within the agreed time and selecting one runs the search.",
    "search.map_view": "Search results can be switched to a map view showing a marker for each business in the current results.",
    "search.results_rating": "Each result in the list shows the business's star rating when one exists.",
    "search.saved_alerts": "A visitor can save a search and receives an email when a new business matches it; the visitor can unsubscribe.",
    "mobile.click_to_call": "On a phone, each result with a phone number shows a button that starts a call when tapped.",
    "checkout.flow": "A customer can complete purchase from a single page; lead packages can be bought without creating an account.",
    "payments.integration": "A test payment through the agreed provider completes in the sandbox and is recorded against the order.",
    "notifications.order": "After a purchase, the sales team receives an email and the customer receives the agreed confirmation message.",
    "billing.invoices": "A downloadable invoice exists for every completed payment.",
    "billing.plan_changes": "A member can upgrade or downgrade their own plan from their account without staff involvement.",
    "billing.refunds": "Refunds remain a manual finance process (no automation in scope).",
    "homepage.redesign": "To be defined in a separate quotation.",
}
CLARIFY_Q = {
    "search.relevance": "What exactly goes wrong in today's search (examples of searches and what the visitor expected)? Which measure tells us it is better?",
    "mobile.experience": "Which mobile screens and actions are hard today (buttons, filter panel, other)? What would 'good enough' look like, ideally with a target?",
    "search.ai_recommendations": "What should the AI do, with what data, and how will success be judged? Is it worth the cost to the client?",
    "search.filters.general": "Which filters are required, and on which attributes?",
}
PROBLEM = re.compile(r"not great|\bmess\b|hard to|clunky|drop out|can'?t find|miles away|dated|too high|isn'?t working|slow|confus", re.I)


def problem_statements(turns: list[Turn], limit: int = 4) -> list[str]:
    out: list[str] = []
    for t in turns:
        if t.role != Role.CLIENT:
            continue
        for s in re.split(r"(?<=[.!?])\s+", t.text):
            if PROBLEM.search(s) and s not in out:
                out.append(s.strip())
    return out[:limit]


def solution_for(r: Requirement) -> str:
    f = r.existing_solution
    ev = f.evidence[0]["title"] if f.evidence else None
    return {
        Feasibility.EXISTING: f"Configure the platform's existing capability{f' ({ev})' if ev else ''}; no custom development assumed.",
        Feasibility.LIMITED: f"Configure the documented capability{f' ({ev})' if ev else ''} and close the documented gap with a small extension.",
        Feasibility.CUSTOM: "Custom development. Product coverage is UNVERIFIED: a product expert must confirm no suitable capability exists before this is quoted as custom work.",
        Feasibility.CLARIFY: "Hold until the client clarifies the need (see open questions); only a clarification session is planned.",
        Feasibility.UNCHECKED: "Not checked.",
    }[f.category]


def build_scope(reqs: list[Requirement], rep: ProjectRepresentation, turns: list[Turn], *, project_name: str, client: str, previous_exclusions: Optional[list[str]] = None) -> dict:
    live = [r for r in reqs if r.intent != Intent.REMOVE]
    agreed = [r for r in live if r.in_scope and r.status == Status.CONFIRMED and r.existing_solution.category != Feasibility.CLARIFY]
    pending = [r for r in live if r.in_scope and r not in agreed]
    out_of_scope = [r for r in live if not r.in_scope]

    for r in live:
        r.acceptance_criteria = [ACCEPTANCE[r.topic]] if r.topic in ACCEPTANCE and r.status == Status.CONFIRMED else ["To be agreed after clarification - not yet testable."]
    deps = []
    for r in live:
        deps += [d for d in r.dependencies if d not in deps]
    exclusions = list(previous_exclusions or [])
    for e in rep.exclusions:
        txt = f"{e.quote} ({e.ts}, {e.speaker})"
        if txt not in exclusions:
            exclusions.append(txt)
    for r in out_of_scope:
        exclusions.append(f"{BY_KEY[r.topic].label if r.topic in BY_KEY else r.topic}: outside the approved baseline; requires a separate quotation.")
    questions = list(rep.open_questions)
    for r in pending:
        q = CLARIFY_Q.get(r.topic)
        if q and q not in questions:
            questions.append(f"[{r.id}] {q}")
    for r in live:
        if r.existing_solution.needs_expert:
            questions.append(f"[{r.id}] Expert to confirm product coverage: {r.existing_solution.rationale[:160]}")
    risks = []
    if any(r.existing_solution.category == Feasibility.CUSTOM for r in live):
        risks.append("Some requirements may already be supported by the platform; confirming this could reduce cost.")
    if any(r.existing_solution.kb_error for r in live):
        risks.append("The knowledge service was unavailable for some checks; those items are unverified.")
    if any(r.status == Status.UNCLEAR for r in live):
        risks.append("Unclear requirements may change the estimate materially once clarified.")
    return {
        "objective": f"Improve {project_name} for {client} by delivering the agreed requirements below.",
        "current_problem": problem_statements(turns) or ["(No explicit problem statement was captured; confirm with the client.)"],
        "agreed_requirements": [{"id": r.id, "topic": r.topic, "text": r.description, "priority": r.priority.value, "sources": [f"{s.ts} {s.speaker}" for s in r.sources]} for r in agreed],
        "pending_clarification": [{"id": r.id, "text": r.description, "status": r.status.value, "priority": r.priority.value} for r in pending],
        "proposed_solution": [{"id": r.id, "approach": solution_for(r), "feasibility": r.existing_solution.category.value, "evidence": r.existing_solution.evidence[:2]} for r in live if r.in_scope],
        "deliverables": [{"id": r.id, "deliverable": BY_KEY[r.topic].label if r.topic in BY_KEY else r.description, "verified_by": r.acceptance_criteria[0]} for r in agreed],
        "exclusions": exclusions,
        "assumptions_and_dependencies": deps + [f"Client commitment: {c.quote} ({c.ts})" for c in rep.client_commitments] + ["Timeline assumes timely client feedback and access to the items above."],
        "client_responsibilities": [c.quote for c in rep.client_commitments] + ["Provide timely feedback and sign-off on acceptance criteria."],
        "out_of_scope_requests": [{"id": r.id, "text": r.description} for r in out_of_scope],
        "open_questions_and_risks": {"questions": questions, "risks": risks},
    }
