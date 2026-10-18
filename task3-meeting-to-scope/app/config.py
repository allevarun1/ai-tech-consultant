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


def _e(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    db_path: str = field(default_factory=lambda: _e("SCOPE_DB_PATH", str(ROOT / "data" / "scope.sqlite")))
    kb_url: str = field(default_factory=lambda: _e("KB_URL", "http://127.0.0.1:8001").rstrip("/"))
    kb_api_key: str = field(default_factory=lambda: _e("KB_API_KEY", ""))
    kb_timeout_s: float = field(default_factory=lambda: float(_e("KB_TIMEOUT_S", "8")))
    # AI provider: LLM_PROVIDER = claude | groq | gemini | auto | offline ; LLM_FALLBACK = auto | none | "groq,gemini".
    # Any failure ends in the offline rule-based extractor.
    llm_provider: str = field(default_factory=lambda: _e("LLM_PROVIDER", "auto"))
    llm_fallback: str = field(default_factory=lambda: _e("LLM_FALLBACK", "auto"))
    anthropic_key: str = field(default_factory=lambda: _e("ANTHROPIC_API_KEY", ""))
    groq_key: str = field(default_factory=lambda: _e("GROQ_API_KEY", ""))
    gemini_key: str = field(default_factory=lambda: _e("GEMINI_API_KEY", ""))
    analyst_model: str = field(default_factory=lambda: _e("SCOPE_ANALYST_MODEL", "claude-sonnet-5-5"))
    # Free-tier providers. Model names change over time: check the provider's current model list before relying on these.
    groq_model: str = field(default_factory=lambda: _e("GROQ_MODEL", "llama-3.3-70b-versatile"))
    gemini_model: str = field(default_factory=lambda: _e("GEMINI_MODEL", "gemini-2.0-flash"))
    # Commercial settings are configuration, never constants baked into a document.
    # The defaults below are PLACEHOLDERS to be replaced before any real quote is produced.
    hourly_rate: float = field(default_factory=lambda: float(_e("SCOPE_HOURLY_RATE", "85")))
    currency: str = field(default_factory=lambda: _e("SCOPE_CURRENCY", "USD"))
    tax_rate: float = field(default_factory=lambda: float(_e("SCOPE_TAX_RATE", "0")))
    capacity_hours_per_week: float = field(default_factory=lambda: float(_e("SCOPE_CAPACITY_H_PER_WEEK", "30")))
    company: str = field(default_factory=lambda: _e("COMPANY_NAME", "Northwind Web Studio"))
    chunk_size: int = field(default_factory=lambda: int(_e("SCOPE_CHUNK_TURNS", "25")))
    chunk_overlap: int = field(default_factory=lambda: int(_e("SCOPE_CHUNK_OVERLAP", "5")))


def get_settings() -> Settings:
    return Settings()
