"""Optional Claude access. `available` is False without an API key; callers then use their rule-based path."""
from __future__ import annotations

import json
import re
import time

from .config import Settings


class LLM:
    def __init__(self, settings: Settings):
        self.s = settings
        self.available = bool(settings.anthropic_key)
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.latency_s = 0.0
        self._client = None
        if self.available:
            import anthropic

            self._client = anthropic.Anthropic(api_key=settings.anthropic_key, timeout=60, max_retries=2)

    def json(self, system: str, user: str, max_tokens: int = 1500, model: str | None = None) -> dict:
        if not self._client:
            raise RuntimeError("LLM not configured")
        t0 = time.perf_counter()
        msg = self._client.messages.create(model=model or self.s.analyst_model, max_tokens=max_tokens, system=system, messages=[{"role": "user", "content": user}])
        self.latency_s += time.perf_counter() - t0
        self.calls += 1
        self.input_tokens += msg.usage.input_tokens
        self.output_tokens += msg.usage.output_tokens
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise ValueError("model returned no JSON")
        return json.loads(m.group(0))
