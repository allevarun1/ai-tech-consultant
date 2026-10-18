"""Runtime configuration, read from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Load KEY=VALUE lines from the repo-root .env into the environment. Variables that are already set win."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (x.strip() for x in line.split("=", 1))
        if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].split("\t#", 1)[0].strip()
            if value.startswith("#"):
                value = ""
        if key and value and key not in os.environ:
            os.environ[key] = value


_load_dotenv(ROOT.parent / ".env")


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    sitemap_url: str = field(default_factory=lambda: _env("KB_SITEMAP_URL", "https://support.brilliantdirectories.com/support/sitemap.xml"))
    db_path: str = field(default_factory=lambda: _env("KB_DB_PATH", str(ROOT / "data" / "kb.sqlite")))
    user_agent: str = field(default_factory=lambda: _env("KB_USER_AGENT", "ai-tech-consultant-capstone/1.0 (polite crawler; contact: student)"))
    max_concurrency: int = field(default_factory=lambda: int(_env("KB_MAX_CONCURRENCY", "3")))
    request_delay_s: float = field(default_factory=lambda: float(_env("KB_REQUEST_DELAY_S", "1.0")))
    max_retries: int = field(default_factory=lambda: int(_env("KB_MAX_RETRIES", "4")))
    timeout_s: float = field(default_factory=lambda: float(_env("KB_TIMEOUT_S", "20")))
    # AI provider: LLM_PROVIDER = claude | groq | gemini | auto | offline ; LLM_FALLBACK = auto | none | "groq,gemini"
    # (KB_LLM_PROVIDER is still read for backward compatibility). Every failure ends in the offline extractive answerer.
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", _env("KB_LLM_PROVIDER", "auto")))
    llm_fallback: str = field(default_factory=lambda: _env("LLM_FALLBACK", "auto"))
    anthropic_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY", ""))
    groq_key: str = field(default_factory=lambda: _env("GROQ_API_KEY", ""))
    gemini_key: str = field(default_factory=lambda: _env("GEMINI_API_KEY", ""))
    answer_model: str = field(default_factory=lambda: _env("KB_ANSWER_MODEL", "claude-sonnet-5-5"))
    vision_model: str = field(default_factory=lambda: _env("KB_VISION_MODEL", "claude-haiku-5-5"))
    # Free-tier providers. Model names change over time: check the provider's current model list before relying on these.
    groq_model: str = field(default_factory=lambda: _env("GROQ_MODEL", "llama-3.3-70b-versatile"))
    groq_vision_model: str = field(default_factory=lambda: _env("GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct"))
    gemini_model: str = field(default_factory=lambda: _env("GEMINI_MODEL", "gemini-2.0-flash"))
    gemini_vision_model: str = field(default_factory=lambda: _env("GEMINI_VISION_MODEL", "gemini-2.0-flash"))
    # Evidence thresholds (hybrid score is a rank-fusion score scaled to 0..1, see retrieval/index.py).
    min_evidence_score: float = field(default_factory=lambda: float(_env("KB_MIN_EVIDENCE_SCORE", "0.30")))
    top_k: int = field(default_factory=lambda: int(_env("KB_TOP_K", "5")))
    api_key: str = field(default_factory=lambda: _env("KB_API_KEY", ""))  # empty => auth disabled (dev)


def get_settings() -> Settings:
    return Settings()
