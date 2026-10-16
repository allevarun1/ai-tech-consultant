"""Agent 4 - Estimation.

Estimates come from a transparent work-breakdown template per requirement, NOT from a model inventing a number.
The templates' shape (design / backend / frontend / integration / testing / acceptance) and the illustrative figures
follow the capstone brief; they are starting points that the team must calibrate against its own delivery history.
Every task records low / likely / high, reasoning, assumptions and dependencies (guide step 3.8).

Cost-saving rule: a requirement the product already supports is estimated as configuration, not custom development.
"""
from __future__ import annotations

import math
from typing import Optional

from app.config import Settings
from app.models import Estimate, EstimateTask, Feasibility, Intent, Requirement

from .topics import BY_KEY

# (task name, low, likely, high) per complexity of CUSTOM work
WBS = {
    "small": [("Technical investigation and design", 1, 2, 3), ("Implementation", 2, 4, 7), ("Automated and manual testing", 1, 2, 3), ("Acceptance, deployment and documentation", 0.5, 1, 2)],
    "medium": [("Requirements and technical design", 2, 3, 5), ("Backend implementation", 4, 7, 12), ("Frontend implementation", 3, 5, 8), ("Integration and error handling", 2, 3, 6), ("Automated and manual testing", 2, 4, 7), ("Acceptance, deployment and documentation", 1, 2, 4)],
    "large": [("Requirements and technical design", 4, 6, 10), ("Backend implementation", 8, 14, 24), ("Frontend implementation", 6, 10, 16), ("Integration and error handling", 4, 7, 12), ("Automated and manual testing", 4, 7, 12), ("Acceptance, deployment and documentation", 2, 4, 7)],
}
CONFIG = [("Configure the existing capability to the agreed settings", 1, 2, 3), ("Verify behaviour with the client's data", 0.5, 1, 2)]
DISCOVERY = [("Clarification session and written requirement", 2, 3, 5)]
GAP = {"small": 0.5, "medium": 0.75}  # share of the custom WBS needed to close a documented limitation


def _task(req_id: str, name: str, lo: float, li: float, hi: float, why: str, assumptions: list[str], deps: list[str]) -> EstimateTask:
    return EstimateTask(requirement_id=req_id, name=name, low=round(lo, 1), likely=round(li, 1), high=round(hi, 1), reasoning=why, assumptions=assumptions, dependencies=deps)


def estimate_requirement(req: Requirement) -> list[EstimateTask]:
    topic = BY_KEY.get(req.topic)
    cx = topic.complexity if topic else "medium"
    cat = req.existing_solution.category
    deps = list(req.dependencies)
    tasks: list[EstimateTask] = []
    if req.intent == Intent.REMOVE:
        return tasks
    if cat == Feasibility.CLARIFY:
        for n, lo, li, hi in DISCOVERY:
            tasks.append(_task(req.id, n, lo, li, hi, "Too vague to size; only discovery work is estimated. Revisit once the client specifies what is wanted.", ["Client attends one clarification session"], deps))
        req.estimate_status = "needs review"
        return tasks
    if cat == Feasibility.EXISTING:
        for n, lo, li, hi in CONFIG:
            tasks.append(_task(req.id, n, lo, li, hi, "The documented product already supports this, so the work is configuration and verification.", ["Documentation matches the client's version of the platform"], deps))
        req.estimate_status = "estimated"
        return tasks
    if cat == Feasibility.LIMITED:
        for n, lo, li, hi in CONFIG:
            tasks.append(_task(req.id, n, lo, li, hi, "Configure the documented part first.", ["Documented limitation applies"], deps))
        share = GAP.get(cx, 0.75)
        for n, lo, li, hi in WBS["small" if cx == "small" else "medium"]:
            tasks.append(_task(req.id, f"Close the gap: {n.lower()}", lo * share, li * share, hi * share, f"Scaled to {int(share * 100)}% of a {cx} custom item because part of the need is already covered.", ["The limitation is accurately described by the documentation"], deps))
        req.estimate_status = "needs review"
        return tasks
    # custom (unverified) or unchecked-but-confirmed
    unverified = cat in (Feasibility.CUSTOM, Feasibility.UNCHECKED) and not req.existing_solution.verified
    why = f"Template for a {cx} custom item (illustrative figures from the brief; calibrate with team history)."
    for n, lo, li, hi in WBS[cx]:
        high = hi * (1.25 if topic and topic.external else 1.0) * (1.2 if unverified else 1.0)
        a = ["No existing product feature covers this (NOT yet confirmed by an expert)"] if unverified else []
        if topic and topic.external:
            a.append("External provider API behaves as documented")
        tasks.append(_task(req.id, n, lo, li, high, why + (" Upper bound widened for external dependency/unverified scope." if high > hi else ""), a, deps))
    req.estimate_status = "needs review" if unverified else "estimated"
    return tasks


def build_estimate(reqs: list[Requirement], s: Settings, *, baseline: Optional[Estimate] = None, deadline_iso: Optional[str] = None, today_iso: Optional[str] = None) -> Estimate:
    tasks: list[EstimateTask] = []
    per: dict[str, dict] = {}
    unverified = 0
    for r in reqs:
        if not r.in_scope or r.intent == Intent.REMOVE:
            continue
        ts = estimate_requirement(r)
        tasks.extend(ts)
        lo, li, hi = (sum(getattr(t, k) for t in ts) for k in ("low", "likely", "high"))
        per[r.id] = {"low": round(lo, 1), "likely": round(li, 1), "high": round(hi, 1), "feasibility": r.existing_solution.category.value, "estimate_status": r.estimate_status}
        if r.existing_solution.category == Feasibility.CUSTOM:
            unverified += 1
    lo, li, hi = (round(sum(getattr(t, k) for t in tasks), 1) for k in ("low", "likely", "high"))
    contingency = round(li * 0.10, 1) if unverified else 0.0
    weeks = round((li + contingency) / s.capacity_hours_per_week, 1) if li else 0.0
    if baseline:
        for rid, v in per.items():
            old = baseline.per_requirement.get(rid)
            v["delta_likely"] = round(v["likely"] - (old["likely"] if old else 0), 1)
        for rid, old in baseline.per_requirement.items():
            if rid not in per:
                per[rid] = {"low": 0, "likely": 0, "high": 0, "delta_likely": round(-old["likely"], 1), "feasibility": "removed", "estimate_status": "removed"}
    price = round((li + contingency) * s.hourly_rate, 2)
    notes = ["Hours are estimates with a stated range, not delivery commitments.",
             "Task figures come from illustrative templates in the capstone brief; calibrate them against the team's actual delivery history before relying on them.",
             "Items marked 'needs review' must be checked by a human estimator."]
    return Estimate(tasks=tasks, total_low=lo, total_likely=li, total_high=hi, contingency_hours=contingency,
                    contingency_reason=f"10% of likely hours because {unverified} requirement(s) have unverified product coverage" if unverified else "No unverified items",
                    weeks_at_capacity=weeks, capacity_hours_per_week=s.capacity_hours_per_week, per_requirement=per,
                    pricing={"hourly_rate": s.hourly_rate, "currency": s.currency, "tax_rate": s.tax_rate, "indicative_net": price,
                             "indicative_gross": round(price * (1 + s.tax_rate), 2),
                             "disclaimer": "Indicative figure derived from likely hours x configured rate. It is NOT an approved quote; rate, currency and tax are configuration."},
                    notes=notes)


def schedule_check(est: Estimate, start_iso: str, deadline_iso: Optional[str]) -> dict:
    """Is the requested date plausible at the configured team capacity? Informational: never a commitment."""
    from datetime import date, timedelta

    start = date.fromisoformat(start_iso)
    earliest = start + timedelta(weeks=math.ceil(est.weeks_at_capacity))
    out = {"earliest_completion_at_capacity": earliest.isoformat(), "capacity_hours_per_week": est.capacity_hours_per_week, "start_assumed": start_iso}
    if deadline_iso:
        d = date.fromisoformat(deadline_iso)
        out["requested"] = deadline_iso
        out["feasible_at_likely_hours"] = earliest <= d
        out["slack_days"] = (d - earliest).days
    return out
