"""Client for the Knowledge Brain API (Task 1). Task 3 depends on this contract only - never on Task 1 internals."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

import httpx


@dataclass
class KbResult:
    ok: bool                              # the call itself succeeded
    answered: bool = False
    answer: str = ""
    citations: list[dict] = field(default_factory=list)   # [{url, title, section, score, excerpt}]
    reason: str = ""
    escalation_id: Optional[int] = None
    error: Optional[str] = None
    request_id: str = ""


class KbClient:
    def __init__(self, base_url: str, api_key: str = "", timeout_s: float = 8.0, retries: int = 2, transport: httpx.BaseTransport | None = None):
        self.base_url, self.retries = base_url.rstrip("/"), retries
        self._c = httpx.Client(timeout=timeout_s, transport=transport, headers={"x-api-key": api_key} if api_key else {})

    def health(self) -> dict:
        try:
            r = self._c.get(f"{self.base_url}/health")
            return {"ok": r.status_code == 200, "detail": "ok" if r.status_code == 200 else f"HTTP {r.status_code}"}
        except httpx.HTTPError as e:
            return {"ok": False, "detail": str(e)[:120]}

    def query(self, question: str, *, create_escalation: bool = False, request_id: Optional[str] = None) -> KbResult:
        rid = request_id or uuid.uuid4().hex[:12]
        last = ""
        for attempt in range(self.retries + 1):
            try:
                r = self._c.post(f"{self.base_url}/query", headers={"x-request-id": rid}, json={"question": question[:2000], "create_escalation": create_escalation})
                if 400 <= r.status_code < 500 and r.status_code != 429:
                    return KbResult(ok=False, error=f"knowledge service rejected the request (HTTP {r.status_code})", request_id=rid)  # not retried
                r.raise_for_status()
                j = r.json()
                return KbResult(ok=True, answered=j.get("status") == "answered", answer=j.get("answer") or "", citations=j.get("citations", []),
                                reason=j.get("reason") or "", escalation_id=j.get("escalation_id"), request_id=rid)
            except (httpx.HTTPError, ValueError) as e:
                last = str(e)[:160]
                if attempt < self.retries:
                    time.sleep(0.15 * 2 ** attempt)
        return KbResult(ok=False, error=f"knowledge service unavailable: {last}", request_id=rid)
