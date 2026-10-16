"""Runtime configuration, read from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


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
    # LLM: "auto" uses Claude when ANTHROPIC_API_KEY is set, else the offline fallback.
    llm_provider: str = field(default_factory=lambda: _env("KB_LLM_PROVIDER", "auto"))
    answer_model: str = field(default_factory=lambda: _env("KB_ANSWER_MODEL", "claude-sonnet-5-5"))
    vision_model: str = field(default_factory=lambda: _env("KB_VISION_MODEL", "claude-haiku-5-5"))
    # Evidence thresholds (hybrid score is a rank-fusion score scaled to 0..1, see retrieval/index.py).
    min_evidence_score: float = field(default_factory=lambda: float(_env("KB_MIN_EVIDENCE_SCORE", "0.30")))
    top_k: int = field(default_factory=lambda: int(_env("KB_TOP_K", "5")))
    api_key: str = field(default_factory=lambda: _env("KB_API_KEY", ""))  # empty => auth disabled (dev)


def get_settings() -> Settings:
    return Settings()
