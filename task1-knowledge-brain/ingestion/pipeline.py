"""Incremental ingestion: sitemap -> diff -> fetch/parse/chunk/save, resumable via the manifest."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from app.config import Settings
from app.llm import LLM
from app.store import Store, now
from multimodal.images import enrich_images
from retrieval.chunking import chunk_article

from .crawler import Crawler, FetchError
from .parser import parse_article
from .sitemap import SitemapEntry, diff_against_manifest, parse_sitemap

log = logging.getLogger("ingestion")
MAX_REMOVAL_FRACTION = 0.5  # refuse to mass-deactivate if the sitemap suddenly lost >50% of known pages


async def load_sitemap(crawler: Crawler, url: str, depth: int = 0) -> list[SitemapEntry]:
    r = await crawler.fetch(url)
    pages, children = parse_sitemap(r.content)
    if children and depth < 2:
        for child in children[:50]:
            pages.extend(await load_sitemap(crawler, child, depth + 1))
    by_url = {p.url: p for p in pages}
    return list(by_url.values())


async def run_ingestion(settings: Settings, store: Store, llm: LLM, limit: Optional[int] = None, url_filter: Optional[str] = None,
                        crawler: Optional[Crawler] = None, entries: Optional[list[SitemapEntry]] = None) -> dict:
    """Run one ingestion job and return its report. Safe to call again after a crash: unfinished pages resume."""
    job_id = store.x("INSERT INTO jobs(state,started_at) VALUES('running',?)", (now(),))
    own_crawler = crawler is None
    crawler = crawler or Crawler(settings.user_agent, settings.max_concurrency, settings.request_delay_s, settings.max_retries, settings.timeout_s)
    rep = {"added": 0, "updated": 0, "removed": 0, "unchanged": 0, "failed": 0}
    state = "done"
    try:
        if entries is None:
            entries = await load_sitemap(crawler, settings.sitemap_url)
        if url_filter:
            entries = [e for e in entries if url_filter in e.url]
        manifest = store.manifest()
        d = diff_against_manifest(entries, manifest)
        rep["unchanged"] = len(d.unchanged)

        # removals (guarded against a truncated/broken sitemap)
        known = [u for u, r in manifest.items() if r["state"] != "removed"]
        if d.removed and not url_filter and len(known) >= 10 and len(d.removed) / len(known) > MAX_REMOVAL_FRACTION:
            log.warning("sitemap lost %d/%d pages; skipping removals (possible broken sitemap)", len(d.removed), len(known))
            state = "done_with_warnings"
        elif not url_filter:
            for u in d.removed:
                store.deactivate(u)
                store.set_manifest(u, "removed")
                rep["removed"] += 1

        work = [(e, "new") for e in d.new] + [(e, "retry") for e in d.retry] + [(e, "changed") for e in d.changed]
        if limit:
            work = work[:limit]
        for e, _ in work:  # record intent first so an interrupted job can resume
            if manifest.get(e.url) is None or manifest[e.url]["state"] in ("removed", "failed"):
                store.set_manifest(e.url, "pending", e.lastmod)

        async def one(e: SitemapEntry) -> None:
            try:
                resp = await crawler.fetch(e.url)
                article = parse_article(resp.text, e.url, e.lastmod)
                prev_row = store.current_article(e.url)
                if prev_row is not None and prev_row["content_hash"] == article["content_hash"]:
                    store.set_manifest(e.url, "done", e.lastmod)
                    rep["unchanged"] += 1
                    return
                prev = json.loads(prev_row["body_json"]) if prev_row else None
                await asyncio.to_thread(enrich_images, article, llm, prev)
                chunks = chunk_article(article)
                if not chunks:
                    raise ValueError("no extractable content")
                store.save_article(article, chunks)
                store.set_manifest(e.url, "done", e.lastmod)
                rep["updated" if prev_row else "added"] += 1
            except Exception as exc:  # noqa: BLE001 - one bad page must not stop the job
                store.set_manifest(e.url, "failed", e.lastmod, error=str(exc)[:300], bump_attempt=True)
                rep["failed"] += 1
                log.warning("failed %s: %s", e.url, exc)

        await asyncio.gather(*(one(e) for e, _ in work))
    except Exception as exc:  # sitemap-level failure
        state = "failed"
        log.error("ingestion failed: %s", exc)
        rep["error"] = str(exc)[:300]
    finally:
        if own_crawler:
            await crawler.close()
        store.x(
            "UPDATE jobs SET state=?,finished_at=?,added=?,updated=?,removed=?,unchanged=?,failed=? WHERE id=?",
            (state, now(), rep["added"], rep["updated"], rep["removed"], rep["unchanged"], rep["failed"], job_id),
        )
    return {"job_id": job_id, "state": state, **rep}
