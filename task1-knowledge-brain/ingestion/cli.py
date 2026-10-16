"""CLI: python -m ingestion.cli --limit 12 [--filter /articles/]"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging

from app.config import get_settings
from app.llm import LLM
from app.store import Store

from .pipeline import run_ingestion


def main() -> None:
    ap = argparse.ArgumentParser(description="Run one incremental ingestion job")
    ap.add_argument("--limit", type=int, default=None, help="process at most N pages (start with 10-20)")
    ap.add_argument("--filter", default="/articles/", help="only URLs containing this substring ('' for all)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    s = get_settings()
    report = asyncio.run(run_ingestion(s, Store(s.db_path), LLM(s), limit=a.limit, url_filter=a.filter or None))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
