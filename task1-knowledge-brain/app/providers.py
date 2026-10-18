"""AI provider layer: Claude, Groq and Gemini behind one interface, with an ordered fallback chain.

Choose the default with LLM_PROVIDER (claude | groq | gemini | auto | offline) and the backups with
LLM_FALLBACK (auto | none | comma list such as "groq,gemini"). When every provider fails (or none has a key)
the caller uses its own offline method, so the system never stops working.

Groq and Gemini are called through their OpenAI-compatible chat endpoints with plain HTTP, so no extra
packages are needed. Claude uses the official `anthropic` SDK.

This file is intentionally identical in task1-knowledge-brain/app and task3-meeting-to-scope/app
(the two Python services are separate packages). Keep them in sync.
"""
from __future__ import annotations

import base64
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import httpx

ORDER = ["claude", "groq", "gemini"]            # default priority for "auto"
KEY_ENV = {"claude": "ANTHROPIC_API_KEY", "groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY"}
ENDPOINT = {
    "groq": "https://api.groq.com/openai/v1/chat/completions",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
}


def endpoint(name: str) -> str:
    """The chat URL for a provider. GROQ_BASE_URL / GEMINI_BASE_URL (full URL) override it, e.g. for a proxy or a local test server."""
    return os.environ.get(f"{name.upper()}_BASE_URL") or ENDPOINT[name]


@dataclass
class Completion:
    text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0


class ProviderError(Exception):
    """A single provider failed (rate limit, bad key, outage, bad response)."""


class ChainError(Exception):
    """Every provider in the chain failed."""


class ClaudeProvider:
    name = "claude"

    def __init__(self, key: str, model: str, vision_model: str, timeout: float):
        import anthropic

        self.model, self.vision_model = model, vision_model
        self._c = anthropic.Anthropic(api_key=key, timeout=timeout, max_retries=1)

    def complete(self, system: str, user: str, max_tokens: int) -> Completion:
        t0 = time.perf_counter()
        try:
            m = self._c.messages.create(model=self.model, max_tokens=max_tokens, system=system, messages=[{"role": "user", "content": user}])
        except Exception as e:  # noqa: BLE001 - SDK raises many types; the chain only needs "it failed"
            raise ProviderError(f"claude: {type(e).__name__}: {str(e)[:120]}") from e
        text = "".join(b.text for b in m.content if getattr(b, "type", "") == "text")
        return Completion(text, self.name, self.model, m.usage.input_tokens, m.usage.output_tokens, (time.perf_counter() - t0) * 1000)

    def describe(self, image: bytes, media: str, prompt: str, max_tokens: int) -> Completion:
        t0 = time.perf_counter()
        try:
            m = self._c.messages.create(model=self.vision_model, max_tokens=max_tokens, messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media, "data": base64.b64encode(image).decode()}},
                {"type": "text", "text": prompt}]}])
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"claude vision: {type(e).__name__}: {str(e)[:120]}") from e
        text = "".join(b.text for b in m.content if getattr(b, "type", "") == "text")
        return Completion(text, self.name, self.vision_model, m.usage.input_tokens, m.usage.output_tokens, (time.perf_counter() - t0) * 1000)


class OpenAICompatProvider:
    """Groq and Gemini (OpenAI-style /chat/completions)."""

    def __init__(self, name: str, key: str, model: str, vision_model: str, timeout: float, transport: Optional[httpx.BaseTransport] = None):
        self.name, self.model, self.vision_model = name, model, vision_model
        self._c = httpx.Client(timeout=timeout, transport=transport, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})

    def _post(self, model: str, messages: list[dict], max_tokens: int) -> Completion:
        t0 = time.perf_counter()
        try:
            r = self._c.post(endpoint(self.name), json={"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0})
        except httpx.HTTPError as e:
            raise ProviderError(f"{self.name}: network error {type(e).__name__}") from e
        if r.status_code != 200:
            raise ProviderError(f"{self.name}: HTTP {r.status_code} {r.text[:120]}")
        try:
            j = r.json()
            text = j["choices"][0]["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise ProviderError(f"{self.name}: unexpected response shape") from e
        u = j.get("usage") or {}
        return Completion(text, self.name, model, int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0)), (time.perf_counter() - t0) * 1000)

    def complete(self, system: str, user: str, max_tokens: int) -> Completion:
        return self._post(self.model, [{"role": "system", "content": system}, {"role": "user", "content": user}], max_tokens)

    def describe(self, image: bytes, media: str, prompt: str, max_tokens: int) -> Completion:
        url = f"data:{media};base64,{base64.b64encode(image).decode()}"
        return self._post(self.vision_model, [{"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": url}}]}], max_tokens)


@dataclass
class Chain:
    providers: list = field(default_factory=list)
    notes: list[str] = field(default_factory=list)       # why a requested provider is not in the chain
    usage: dict = field(default_factory=dict)            # provider -> {"calls", "failures", "input_tokens", "output_tokens"}
    last_errors: list[str] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return bool(self.providers)

    @property
    def primary(self) -> Optional[str]:
        return f"{self.providers[0].name}:{self.providers[0].model}" if self.providers else None

    @property
    def label(self) -> str:
        return " > ".join([f"{p.name}:{p.model}" for p in self.providers] + ["offline"])

    def _run(self, fn_name: str, accept: Optional[Callable[[Completion], bool]], *args) -> Completion:
        """Try each provider in order. `accept` lets the caller reject a reply (e.g. no usable JSON) so the next provider is tried."""
        self.last_errors = []
        for p in self.providers:
            u = self.usage.setdefault(p.name, {"calls": 0, "failures": 0, "input_tokens": 0, "output_tokens": 0})
            try:
                c: Completion = getattr(p, fn_name)(*args)
            except ProviderError as e:
                u["failures"] += 1
                self.last_errors.append(str(e))
                continue
            u["input_tokens"] += c.input_tokens
            u["output_tokens"] += c.output_tokens
            if accept is not None and not accept(c):
                u["failures"] += 1
                self.last_errors.append(f"{p.name}: reply was not usable")
                continue
            u["calls"] += 1
            return c
        raise ChainError("; ".join(self.last_errors) or "no AI provider configured")

    def complete(self, system: str, user: str, max_tokens: int = 700, accept: Optional[Callable[[Completion], bool]] = None) -> Completion:
        return self._run("complete", accept, system, user, max_tokens)

    def describe(self, image: bytes, media: str, prompt: str, max_tokens: int = 400, accept: Optional[Callable[[Completion], bool]] = None) -> Completion:
        return self._run("describe", accept, image, media, prompt, max_tokens)


def build_chain(provider: str, fallback: str, keys: dict[str, str], models: dict[str, tuple[str, str]], timeout: float = 60.0,
                transport: Optional[httpx.BaseTransport] = None) -> Chain:
    """Build the ordered provider chain.

    provider: claude | groq | gemini | auto (first provider that has a key, in ORDER) | offline (no AI at all)
    fallback: auto (every other provider that has a key, in ORDER) | none | comma list of provider names
    keys:     {"claude": ..., "groq": ..., "gemini": ...}        models: name -> (text model, vision model)
    """
    provider, fallback = (provider or "auto").strip().lower(), (fallback or "auto").strip().lower()
    valid = set(ORDER)
    if provider not in valid | {"auto", "offline", "none"}:
        raise ValueError(f"LLM_PROVIDER must be one of claude, groq, gemini, auto, offline (got '{provider}')")
    chain = Chain()
    if provider in ("offline", "none"):
        return chain
    has = lambda n: bool((keys.get(n) or "").strip())  # noqa: E731

    wanted: list[str] = []
    if provider == "auto":
        first = next((n for n in ORDER if has(n)), None)
        if first:
            wanted.append(first)
        else:
            chain.notes.append("no API key set for claude, groq or gemini")
    else:
        wanted.append(provider)

    if fallback == "auto":
        wanted += [n for n in ORDER if n not in wanted]
    elif fallback != "none":
        for n in [x.strip() for x in fallback.split(",") if x.strip()]:
            if n not in valid:
                raise ValueError(f"LLM_FALLBACK has unknown provider '{n}' (use claude, groq, gemini, auto or none)")
            if n not in wanted:
                wanted.append(n)

    for n in wanted:
        if not has(n):
            if n == provider:
                chain.notes.append(f"{n} was requested but {KEY_ENV[n]} is not set")
            continue
        text_model, vision_model = models[n]
        chain.providers.append(ClaudeProvider(keys[n], text_model, vision_model, timeout) if n == "claude"
                               else OpenAICompatProvider(n, keys[n], text_model, vision_model, timeout, transport))
    return chain
