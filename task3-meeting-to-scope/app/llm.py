"""AI access for the Transcript Analyst. The provider is chosen with LLM_PROVIDER (claude | groq | gemini | auto | offline)
and backed up by LLM_FALLBACK (see providers.py). If every provider fails, callers use the rule-based extractor."""
from __future__ import annotations

import json
import re
from typing import Optional

import httpx

from .config import Settings
from .providers import Chain, build_chain


def _extract_json(text: str) -> Optional[dict]:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        out = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return out if isinstance(out, dict) else None


class LLM:
    def __init__(self, settings: Settings, transport: Optional[httpx.BaseTransport] = None):
        self.s = settings
        self.chain: Chain = build_chain(
            settings.llm_provider, settings.llm_fallback,
            keys={"claude": settings.anthropic_key, "groq": settings.groq_key, "gemini": settings.gemini_key},
            models={"claude": (settings.analyst_model, settings.analyst_model), "groq": (settings.groq_model, settings.groq_model),
                    "gemini": (settings.gemini_model, settings.gemini_model)},
            timeout=60.0, transport=transport)
        self.last_provider: Optional[str] = None

    @property
    def available(self) -> bool:
        return self.chain.available

    @property
    def name(self) -> str:
        return self.chain.primary or "rules"

    @property
    def chain_label(self) -> str:
        return self.chain.label.replace("offline", "offline-rules")

    # usage totals across the providers that actually answered (read by the evaluation runner)
    @property
    def input_tokens(self) -> int:
        return sum(u["input_tokens"] for u in self.chain.usage.values())

    @property
    def output_tokens(self) -> int:
        return sum(u["output_tokens"] for u in self.chain.usage.values())

    @property
    def calls(self) -> int:
        return sum(u["calls"] for u in self.chain.usage.values())

    def json(self, system: str, user: str, max_tokens: int = 1500) -> dict:
        """Ask for a JSON object. A provider whose reply has no usable JSON counts as failed and the next one is tried.
        Raises ChainError when every provider failed (the caller then falls back to rules)."""
        c = self.chain.complete(system, user, max_tokens, accept=lambda x: _extract_json(x.text) is not None)
        self.last_provider = f"{c.provider}:{c.model}"
        return _extract_json(c.text) or {}
