"""Sitemap download/parse, URL validation, dedup and diffing against the saved manifest."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import urldefrag, urlparse

from lxml import etree


@dataclass(frozen=True)
class SitemapEntry:
    url: str
    lastmod: Optional[str] = None


def normalize_url(url: str) -> Optional[str]:
    """Return a canonical https URL, or None if the URL is not a valid http(s) page."""
    url, _ = urldefrag(url.strip())
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.netloc:
        return None
    path = p.path.rstrip("/") or "/"
    return p._replace(path=path, scheme=p.scheme.lower(), netloc=p.netloc.lower()).geturl()


def parse_sitemap(xml_bytes: bytes) -> tuple[list[SitemapEntry], list[str]]:
    """Parse a <urlset> or <sitemapindex>.

    Returns (page entries, child sitemap URLs). Duplicates and invalid URLs are dropped;
    when a URL repeats, the entry with the latest lastmod wins.
    """
    root = etree.fromstring(xml_bytes, parser=etree.XMLParser(resolve_entities=False, no_network=True, recover=False))
    local = lambda el: etree.QName(el).localname  # noqa: E731
    pages: dict[str, SitemapEntry] = {}
    children: list[str] = []
    for node in root:
        kind = local(node)
        loc = next((c.text for c in node if local(c) == "loc" and c.text), None)
        lastmod = next((c.text.strip() for c in node if local(c) == "lastmod" and c.text), None)
        url = normalize_url(loc) if loc else None
        if not url:
            continue
        if kind == "sitemap":
            children.append(url)
        elif kind == "url":
            old = pages.get(url)
            if old is None or (lastmod or "") > (old.lastmod or ""):
                pages[url] = SitemapEntry(url, lastmod)
    return list(pages.values()), children


@dataclass
class Diff:
    new: list[SitemapEntry]
    changed: list[SitemapEntry]
    unchanged: list[SitemapEntry]
    removed: list[str]
    retry: list[SitemapEntry]  # previously failed/pending: resume these


def diff_against_manifest(entries: list[SitemapEntry], manifest: dict) -> Diff:
    """Classify pages as new/changed/unchanged/removed. 'pending' and 'failed' rows are retried (resume)."""
    seen = {e.url for e in entries}
    d = Diff([], [], [], [], [])
    for e in entries:
        row = manifest.get(e.url)
        if row is None or row["state"] == "removed":
            d.new.append(e)
        elif row["state"] in ("pending", "failed"):
            d.retry.append(e)
        elif e.lastmod and row["lastmod"] and e.lastmod != row["lastmod"]:
            d.changed.append(e)
        else:
            d.unchanged.append(e)
    d.removed = [u for u, r in manifest.items() if u not in seen and r["state"] != "removed"]
    return d
