"""Agent 2 - Product Feasibility. Asks the Knowledge Brain (Task 1) whether the platform already covers a requirement.

Classification (guide step 3.6):
  existing_configuration        the documented product already does it
  existing_with_limitation      documented, but with a stated limit
  potential_custom_development  no documented capability found (NOT proof it is missing - an expert must confirm)
  needs_clarification           the requirement is too vague, or the knowledge service was unavailable
"""
from __future__ import annotations

import re

from app.kb_client import KbClient
from app.models import Feasibility, FeasibilityResult, Intent, Requirement, Status

from .topics import BY_KEY

PROVIDER = re.compile(r"\b(stripe|paypal|razorpay|square|braintree)\b", re.I)
LIMIT = re.compile(r"\b(not supported|cannot|can'?t|does not support|doesn'?t support|limitation|limited to|not available|only (available|works)|unsupported)\b", re.I)

# The cited article must actually be about the capability; a merely "similar" article is not evidence.
EVIDENCE_TERMS = {
    "search.filters.category": r"categor",
    "search.filters.distance": r"radius|distance",
    "search.autocomplete": r"predictive|autocomplete|suggest",
    "search.map_view": r"\bmap\b",
    "search.results_rating": r"rating|review|custom information",
    "search.saved_alerts": r"saved search|search alert|alert",
    "search.ai_recommendations": r"recommend|\bAI\b",
    "mobile.click_to_call": r"click.to.call|call button|phone number",
    "checkout.flow": r"checkout|check-out|signup|sign up|payment flow",
    "payments.integration": r"payment gateway|stripe|authorize\.net|paypal",
    "notifications.order": r"(order|payment|purchase|signup|sign-up).{0,40}(email|notification)|(email|notification).{0,40}(order|payment|purchase)|email template",
    "billing.invoices": r"invoice",
    "billing.plan_changes": r"upgrade|downgrade",
    "billing.refunds": r"refund",
    "homepage.redesign": r"home ?page",
}


def check_requirement(req: Requirement, kb: KbClient, *, escalate: bool = False) -> FeasibilityResult:
    topic = BY_KEY.get(req.topic)
    if req.intent == Intent.REMOVE:
        return FeasibilityResult(category=Feasibility.UNCHECKED, rationale="Requirement was removed; no feasibility check needed.")
    if req.status in (Status.UNCLEAR, Status.DISPUTED) or (topic and topic.vague):
        return FeasibilityResult(category=Feasibility.CLARIFY, verified=False,
                                 rationale="The client has not yet said precisely what is wanted, so there is nothing concrete to check. Clarify first; do not estimate custom work for it.")
    query = topic.kb_query if topic else req.description
    r = kb.query(query, create_escalation=escalate)
    if not r.ok:
        return FeasibilityResult(category=Feasibility.CLARIFY, kb_error=r.error, rationale=f"Could not verify against product documentation ({r.error}). Treated as unverified, not as custom work.")

    terms = re.compile(EVIDENCE_TERMS.get(req.topic, r"."), re.I)
    relevant = [c for c in r.citations if terms.search(f"{c.get('title','')} {c.get('excerpt','')}")] if r.answered else []
    if r.answered and relevant:
        evidence = [{"url": c["url"], "title": c["title"], "excerpt": c["excerpt"][:240]} for c in relevant[:3]]
        text = f"{r.answer}\n" + " ".join(c.get("excerpt", "") for c in relevant)
        asked = " ".join(s.quote for s in req.sources)
        provider = PROVIDER.search(asked)
        if provider and not re.search(re.escape(provider.group(0)), text, re.I):
            # generic gateway docs do not prove that THIS provider is supported
            return FeasibilityResult(category=Feasibility.LIMITED, evidence=evidence, verified=False, needs_expert=True,
                                     rationale=f"Payment-gateway setup is documented, but support for {provider.group(0)} specifically is not confirmed in the retrieved documentation. Confirm with the provider/product expert before promising it.")
        if LIMIT.search(text):
            return FeasibilityResult(category=Feasibility.LIMITED, evidence=evidence, verified=True,
                                     rationale="Documented, but the documentation states a limitation. Configure what exists, then size the gap separately.")
        return FeasibilityResult(category=Feasibility.EXISTING, evidence=evidence, verified=True,
                                 rationale="The product documentation describes this capability. Configuration, not custom development, is the starting assumption.")
    note = "Documentation was found but does not match this capability." if r.answered else "No documented capability found."
    return FeasibilityResult(category=Feasibility.CUSTOM, verified=False, needs_expert=True,
                             rationale=f"{note} This is NOT proof the feature is missing - a product expert should confirm before custom work is quoted.")
