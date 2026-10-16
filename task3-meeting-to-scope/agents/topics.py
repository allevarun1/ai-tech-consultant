"""Topic taxonomy: a stable key per kind of requirement.

Stable keys are the matching strategy for follow-up meetings (a requirement keeps its key and its REQ id across
versions). They also drive the feasibility query and the estimation template. A topic's group (text before the first
dot) is used to decide whether a *new* requirement belongs to the project's existing theme or is potentially out of scope.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Topic:
    key: str
    label: str
    pattern: re.Pattern
    kb_query: str          # how to ask the Knowledge Brain about this capability
    complexity: str        # small | medium | large  (used when custom work is needed)
    vague: bool = False    # too vague to estimate without clarification
    external: bool = False # depends on an external provider


def _t(key, label, pat, q, cx, vague=False, external=False) -> Topic:
    return Topic(key, label, re.compile(pat, re.I), q, cx, vague, external)


# Order matters: specific topics first. One utterance can match several topics.
TOPICS: list[Topic] = [
    _t("search.filters.category", "Filter search results by category", r"(filter\w*|narrow)\W+(\w+\W+){0,4}categor|categor\w*\W+(\w+\W+){0,3}filter|by category|category (and|filter)|(filter|narrow)[^.]{0,40}categor", "How do visitors filter or browse member search results by category?", "medium"),
    _t("search.filters.distance", "Filter search results by distance", r"distance|how far|radius|\bmiles?\b|nearby|near me|close to (them|me)", "How do I change the default search results radius and distance search?", "small"),
    _t("search.autocomplete", "Autocomplete suggestions in the search box", r"auto-?complete|suggestions? (to )?(pop|appear|show)|as (people|they|users|visitors) type|type-?ahead|predictive", "Predictive keyword search suggestions while typing in the search box", "medium"),
    _t("search.map_view", "Map view of search results", r"map view|map of (the results|where)|on a map|see where (the|each)|pin map|map (with|showing) (pins|markers)|results on (the |a )?map", "Search layouts list grid and map view for member search results", "medium"),
    _t("search.results_rating", "Show star ratings in search results", r"star rating|ratings? (in|on) the (results|list)|show[^.]{0,30}rating|reviews? (in|on) the results", "Add custom information such as ratings to member search results", "small"),
    _t("search.saved_alerts", "Saved searches with email alerts", r"saved search|email alerts?|alerts? (when|if)|notify (them|users|visitors) when", "Can visitors save a search and receive email alerts for new matching members?", "large"),
    _t("search.ai_recommendations", "AI-based recommendations in search", r"\bAI\b|artificial intelligence|recommend\w*|personali[sz]", "Does the platform have AI or recommendation features for search results?", "large", vague=True),
    _t("search.relevance", "Faster, more relevant search", r"find (things|businesses|members|products|what they)\b[^.]{0,20}(faster|easier|quicker)|search (isn'?t|is not) great|better search|improve (the )?search|search results (are|is) (poor|bad|off)|miles away", "How does search result ordering and relevance work?", "medium", vague=True),
    _t("mobile.click_to_call", "Click-to-call buttons on results", r"click[- ]to[- ]call|call button|tap[- ]to[- ]call|call (the business )?directly from", "Can search results show a click-to-call phone button on mobile?", "small"),
    _t("mobile.experience", "Better mobile experience", r"\bmobile\b|\bphones?\b|smartphones?|small screens?|tiny buttons", "How is the member search page displayed on mobile devices?", "medium", vague=True),
    _t("checkout.flow", "Checkout flow changes", r"checkout|check-out|guest checkout|one[- ]page", "Can the checkout or signup payment flow be customised?", "medium"),
    _t("payments.integration", "Payment provider integration", r"payment (provider|gateway|integration|processor)|\bstripe\b|razorpay|paypal|authorize\.net|card payments?|pay (by|with) card", "Setting up a payment gateway and supported payment providers", "large", external=True),
    _t("notifications.order", "Order notifications", r"order (notification|confirmation|email)|notify[^.]{0,30}order|(email|sms|text)s?[^.]{0,30}(new )?orders?|order[^.]{0,20}(email|sms|text)", "How do I customise the emails sent when an order or payment is completed?", "medium"),
    _t("billing.invoices", "Invoices", r"invoices?\b", "How can members and admins download invoices?", "small"),
    _t("billing.plan_changes", "Self-service plan upgrades and downgrades", r"upgrade|downgrade|change (their |his |her )?(plan|membership)", "Upgrades and downgrades of membership plans from the member's perspective", "small"),
    _t("billing.refunds", "Refunds", r"\brefund", "How do refunds work in the billing module?", "small"),
    _t("homepage.redesign", "Homepage redesign", r"home ?page|redesign|re-design|new look", "Can the homepage layout be redesigned or customised?", "large"),
    _t("search.filters.general", "Search filters (unspecified)", r"\bfilters?\b", "What search filters are available for member search?", "medium", vague=True),
]
BY_KEY = {t.key: t for t in TOPICS}


def group_of(key: str) -> str:
    return key.split(".", 1)[0]


def match_topics(text: str) -> list[Topic]:
    """All topics mentioned in `text`; the generic filter topic is dropped when a specific filter topic matched."""
    hits = [t for t in TOPICS if t.pattern.search(text)]
    if any(t.key.startswith("search.filters.") and t.key != "search.filters.general" for t in hits):
        hits = [t for t in hits if t.key != "search.filters.general"]
    # a specific mobile item (click-to-call) supersedes the generic "better mobile experience" in the same sentence
    if any(t.key == "mobile.click_to_call" for t in hits):
        hits = [t for t in hits if t.key != "mobile.experience"]
    # the vague "better search" topic is noise when specific search topics are present in the same sentence
    specific_search = [t for t in hits if t.key.startswith("search.") and t.key not in ("search.relevance", "search.ai_recommendations", "search.filters.general")]
    if specific_search:
        hits = [t for t in hits if t.key != "search.relevance"]
    return hits
