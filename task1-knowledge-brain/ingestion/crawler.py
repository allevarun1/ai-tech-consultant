"""Polite, resumable async fetching: robots.txt, bounded concurrency, delay, retries with backoff."""
from __future__ import annotations

import asyncio
import random
from typing import Optional
from urllib import robotparser
from urllib.parse import urlparse

import httpx

RETRYABLE = {408, 425, 429, 500, 502, 503, 504}


class FetchError(Exception):
    pass


class Crawler:
    def __init__(self, user_agent: str, max_concurrency: int = 3, delay_s: float = 1.0, max_retries: int = 4,
                 timeout_s: float = 20.0, transport: Optional[httpx.AsyncBaseTransport] = None, respect_robots: bool = True):
        self.ua, self.delay, self.retries = user_agent, delay_s, max_retries
        self._sem = asyncio.Semaphore(max_concurrency)
        self._client = httpx.AsyncClient(headers={"User-Agent": user_agent}, timeout=timeout_s, follow_redirects=True, transport=transport)
        self._robots: dict[str, Optional[robotparser.RobotFileParser]] = {}
        self._respect = respect_robots

    async def close(self) -> None:
        await self._client.aclose()

    async def _allowed(self, url: str) -> bool:
        if not self._respect:
            return True
        host = urlparse(url)
        origin = f"{host.scheme}://{host.netloc}"
        if origin not in self._robots:
            rp = robotparser.RobotFileParser()
            try:
                r = await self._client.get(f"{origin}/robots.txt")
                rp.parse(r.text.splitlines() if r.status_code == 200 else [])
            except httpx.HTTPError:
                rp.parse([])  # unreachable robots.txt => treat as no restrictions
            self._robots[origin] = rp
        return self._robots[origin].can_fetch(self.ua, url)

    async def fetch(self, url: str) -> httpx.Response:
        if not await self._allowed(url):
            raise FetchError(f"blocked by robots.txt: {url}")
        last: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            async with self._sem:
                try:
                    r = await self._client.get(url)
                    if r.status_code in RETRYABLE:
                        ra = r.headers.get("retry-after")
                        last = FetchError(f"HTTP {r.status_code}")
                        wait = float(ra) if ra and ra.isdigit() else None
                    elif r.status_code >= 400:
                        raise FetchError(f"HTTP {r.status_code} (not retried)")
                    else:
                        await asyncio.sleep(self.delay)  # holding the semaphore => delay applies per slot
                        return r
                except httpx.TransportError as e:
                    last, wait = e, None
            # exponential backoff with jitter, outside the semaphore
            await asyncio.sleep(wait if wait is not None else min(30, (2 ** attempt) * 0.5) + random.random() * 0.25)
        raise FetchError(f"gave up after {self.retries + 1} attempts: {last}")
