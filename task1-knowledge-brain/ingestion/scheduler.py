"""Daily incremental refresh: `python -m ingestion.scheduler` (or run `/ingestion/run` from cron / Task Scheduler)."""
from __future__ import annotations

import asyncio
import logging
import os

from app.config import get_settings
from app.llm import LLM
from app.store import Store

from .pipeline import run_ingestion

INTERVAL_S = int(os.environ.get("KB_REFRESH_INTERVAL_S", str(24 * 3600)))


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    s = get_settings()
    store, llm = Store(s.db_path), LLM(s)
    while True:
        report = await run_ingestion(s, store, llm)
        logging.info("daily refresh: %s", report)
        await asyncio.sleep(INTERVAL_S)


if __name__ == "__main__":
    asyncio.run(main())
