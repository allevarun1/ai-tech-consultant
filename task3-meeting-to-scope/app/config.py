from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _e(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    db_path: str = field(default_factory=lambda: _e("SCOPE_DB_PATH", str(ROOT / "data" / "scope.sqlite")))
    kb_url: str = field(default_factory=lambda: _e("KB_URL", "http://127.0.0.1:8001").rstrip("/"))
    kb_api_key: str = field(default_factory=lambda: _e("KB_API_KEY", ""))
    kb_timeout_s: float = field(default_factory=lambda: float(_e("KB_TIMEOUT_S", "8")))
    anthropic_key: str = field(default_factory=lambda: _e("ANTHROPIC_API_KEY", ""))
    analyst_model: str = field(default_factory=lambda: _e("SCOPE_ANALYST_MODEL", "claude-sonnet-5-5"))
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
