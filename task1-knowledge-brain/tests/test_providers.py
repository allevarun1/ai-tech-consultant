"""AI provider selection and fallback. Groq/Gemini are replaced by an httpx.MockTransport; nothing touches the network."""
from __future__ import annotations

import json

import httpx
import pytest

from app.config import Settings
from app.llm import LLM
from app.providers import ChainError, ProviderError, build_chain

MODELS = {"claude": ("c-text", "c-vis"), "groq": ("g-text", "g-vis"), "gemini": ("m-text", "m-vis")}
PASSAGES = ["Open Settings, then Domain Manager. Choose the option called A domain name I own to connect the subdomain."]
GOOD = {"supported": True, "answer": "Open Settings > Domain Manager.", "cited": [1]}


def chat_ok(payload: dict, tokens=(11, 7)):
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(payload)}}], "usage": {"prompt_tokens": tokens[0], "completion_tokens": tokens[1]}})


def settings(**kw) -> Settings:
    base = dict(llm_provider="auto", llm_fallback="auto", anthropic_key="", groq_key="", gemini_key="", timeout_s=5)
    base.update(kw)
    return Settings(**base)


def llm_with(handler, **kw) -> LLM:
    return LLM(settings(**kw), transport=httpx.MockTransport(handler))


# ---- chain construction ---------------------------------------------------------------------------------------
def test_auto_picks_first_keyed_provider_and_adds_the_rest_as_fallback():
    c = build_chain("auto", "auto", {"claude": "", "groq": "k1", "gemini": "k2"}, MODELS)
    assert [p.name for p in c.providers] == ["groq", "gemini"] and c.primary == "groq:g-text"


def test_explicit_default_is_first_even_if_not_first_in_priority_order():
    c = build_chain("gemini", "auto", {"claude": "", "groq": "k1", "gemini": "k2"}, MODELS)
    assert [p.name for p in c.providers] == ["gemini", "groq"]


def test_fallback_none_and_explicit_lists():
    keys = {"claude": "", "groq": "k1", "gemini": "k2"}
    assert [p.name for p in build_chain("groq", "none", keys, MODELS).providers] == ["groq"]
    assert [p.name for p in build_chain("groq", "gemini", keys, MODELS).providers] == ["groq", "gemini"]


def test_missing_key_is_explained_not_silently_ignored():
    c = build_chain("groq", "none", {"claude": "", "groq": "", "gemini": ""}, MODELS)
    assert not c.available and any("GROQ_API_KEY" in n for n in c.notes)
    assert any("no API key" in n for n in build_chain("auto", "auto", {"claude": "", "groq": "", "gemini": ""}, MODELS).notes)


def test_offline_means_no_ai_at_all_even_with_keys():
    assert not build_chain("offline", "auto", {"claude": "x", "groq": "y", "gemini": "z"}, MODELS).available


def test_bad_configuration_fails_loudly():
    with pytest.raises(ValueError, match="LLM_PROVIDER"):
        build_chain("openai", "auto", {}, MODELS)
    with pytest.raises(ValueError, match="LLM_FALLBACK"):
        build_chain("groq", "groq,bard", {"groq": "k"}, MODELS)


# ---- calling and falling back ---------------------------------------------------------------------------------------
def test_groq_answer_is_used_and_attributed():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"], seen["body"] = str(req.url), req.headers["authorization"], json.loads(req.content)
        return chat_ok(GOOD)

    llm = llm_with(handler, llm_provider="groq", groq_key="gk")
    a = llm.answer("How do I connect a subdomain?", PASSAGES)
    assert a.supported and a.by == "groq:llama-3.3-70b-versatile" and a.cited == [1] and (a.input_tokens, a.output_tokens) == (11, 7)
    assert seen["url"].startswith("https://api.groq.com/openai/v1") and seen["auth"] == "Bearer gk"
    assert seen["body"]["messages"][0]["role"] == "system" and "ONLY the numbered evidence" in seen["body"]["messages"][0]["content"]
    assert llm.chain.usage["groq"]["calls"] == 1


def test_rate_limited_primary_falls_back_to_the_next_provider():
    def handler(req: httpx.Request) -> httpx.Response:
        if "groq.com" in str(req.url):
            return httpx.Response(429, text="rate limit")
        return chat_ok(GOOD)

    llm = llm_with(handler, llm_provider="groq", groq_key="gk", gemini_key="mk")
    a = llm.answer("How do I connect a subdomain?", PASSAGES)
    assert a.by.startswith("gemini:") and a.supported
    assert llm.chain.usage["groq"]["failures"] == 1 and llm.chain.usage["gemini"]["calls"] == 1


def test_every_provider_failing_falls_back_to_the_offline_answerer_not_an_error():
    llm = llm_with(lambda r: httpx.Response(500, text="down"), llm_provider="groq", groq_key="gk", gemini_key="mk")
    a = llm.answer("How do I connect the subdomain? Domain Manager", PASSAGES)
    assert a.by == "offline-extractive (AI providers failed)" and a.supported
    assert len(llm.chain.last_errors) == 2


def test_reply_without_json_moves_on_to_the_next_provider():
    def handler(req: httpx.Request) -> httpx.Response:
        if "groq.com" in str(req.url):
            return httpx.Response(200, json={"choices": [{"message": {"content": "Sure! Here you go."}}], "usage": {}})
        return chat_ok(GOOD)

    llm = llm_with(handler, llm_provider="groq", groq_key="gk", gemini_key="mk")
    assert llm.answer("How do I connect a subdomain?", PASSAGES).by.startswith("gemini:")


def test_malformed_provider_response_is_a_provider_error():
    chain = build_chain("groq", "none", {"groq": "k"}, MODELS, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"oops": 1})))
    with pytest.raises(ChainError, match="unexpected response shape"):
        chain.complete("s", "u")
    assert isinstance(ProviderError("x"), Exception)


def test_offline_setting_never_calls_the_network():
    def boom(req):  # pragma: no cover - must not run
        raise AssertionError("network used in offline mode")

    llm = llm_with(boom, llm_provider="offline", groq_key="gk")
    a = llm.answer("How do I connect the subdomain? Domain Manager", PASSAGES)
    assert a.by == "offline-extractive" and llm.name == "offline-extractive" and llm.chain_label == "offline-extractive"


def test_chain_label_shows_default_then_fallbacks():
    llm = llm_with(lambda r: chat_ok(GOOD), llm_provider="groq", groq_key="gk", gemini_key="mk")
    assert llm.chain_label == "groq:llama-3.3-70b-versatile > gemini:gemini-2.0-flash > offline-extractive"


def test_dotenv_loader_handles_comments_quotes_empty_values_and_existing_variables(tmp_path, monkeypatch):
    import os

    from app.config import _load_dotenv

    monkeypatch.setattr(os, "environ", {"EXISTING": "from-shell"})          # isolate: nothing leaks into other tests
    f = tmp_path / ".env"
    f.write_text("# comment\nLLM_PROVIDER=groq   # my default\nGROQ_API_KEY=\nGEMINI_API_KEY=\"abc#1\"\nANTHROPIC_API_KEY=   # optional\nEXISTING=from-file\nNOT_A_LINE\n", encoding="utf-8")
    _load_dotenv(f)
    assert os.environ["LLM_PROVIDER"] == "groq" and os.environ["GEMINI_API_KEY"] == "abc#1"
    assert "GROQ_API_KEY" not in os.environ and "ANTHROPIC_API_KEY" not in os.environ
    assert os.environ["EXISTING"] == "from-shell"
