"""Article parser: HTML -> normalized article with sections, steps, tables and image context."""
from __future__ import annotations

import hashlib
import re
from typing import Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

NOISE = ["script", "style", "noscript", "nav", "footer", "header", "aside", "form", "iframe", "svg"]
NOISE_HINT = re.compile(r"(nav|menu|footer|sidebar|breadcrumb|cookie|banner|advert|related|share|social|comment)", re.I)
MAIN_SELECTORS = ["article", "[itemprop=articleBody]", ".article-body", ".kb-article", "#article", "#content", ".content", "main"]
HEADINGS = {"h1", "h2", "h3", "h4"}


def article_id_for(url: str) -> str:
    p = urlparse(url)
    slug = re.sub(r"[^a-z0-9]+", "-", (p.path or "/").lower()).strip("-") or "root"
    return f"{slug[:60]}-{hashlib.sha1(url.encode()).hexdigest()[:8]}"


def _clean(t: str) -> str:
    return re.sub(r"[ \t\r\f\v]+", " ", re.sub(r"\n{3,}", "\n\n", t)).strip()


def _table_text(table: Tag) -> str:
    rows = []
    for tr in table.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
        if cells:
            rows.append(" | ".join(cells))
    return "\n".join(rows)  # header row stays with the data rows in the same block


def parse_article(html: str, url: str, lastmod: Optional[str] = None) -> dict:
    soup = BeautifulSoup(html, "lxml")
    title_tag = soup.find("h1") or soup.find("title")
    title = _clean(title_tag.get_text(" ")) if title_tag else url

    main = None
    for sel in MAIN_SELECTORS:
        main = soup.select_one(sel)
        if main and len(main.get_text(strip=True)) > 80:
            break
        main = None
    main = main or soup.body or soup

    for t in main.find_all(NOISE):
        t.decompose()
    for t in main.find_all(True):
        if getattr(t, "attrs", None) is None:  # already removed with a decomposed ancestor
            continue
        cls = " ".join(t.get("class", [])) + " " + (t.get("id") or "")
        if cls.strip() and NOISE_HINT.search(cls) and t.name not in ("body", "html", "article", "main"):
            t.decompose()

    sections: list[dict] = []
    cur = {"heading": title, "level": 1, "blocks": [], "images": []}

    def flush() -> None:
        nonlocal cur
        if cur["blocks"] or cur["images"]:
            sections.append(cur)

    def add_image(img: Tag) -> None:
        src = img.get("src") or img.get("data-src")
        if not src or src.startswith("data:"):
            return
        # Keep the image together with the text immediately before it.
        ctx = " ".join(cur["blocks"][-2:])[-400:]
        cur["images"].append({"url": urljoin(url, src), "alt": _clean(img.get("alt", "")), "context": ctx})

    for el in main.descendants:
        if not isinstance(el, Tag):
            continue
        if el.name in HEADINGS:
            flush()
            cur = {"heading": _clean(el.get_text(" ")) or title, "level": int(el.name[1]), "blocks": [], "images": []}
        elif el.name in ("p", "pre", "blockquote"):
            if el.find_parent(["li", "table"]):
                continue
            t = _clean(el.get_text(" "))
            if t:
                cur["blocks"].append(t)
        elif el.name in ("ol", "ul"):
            if el.find_parent(["li", "table"]):
                continue
            ordered = el.name == "ol"
            items = [_clean(li.get_text(" ")) for li in el.find_all("li", recursive=False)]
            cur["blocks"].append("\n".join(f"{i + 1}. {t}" if ordered else f"- {t}" for i, t in enumerate(items) if t))
        elif el.name == "table":
            tt = _table_text(el)
            if tt:
                cur["blocks"].append(tt)
        elif el.name == "img":
            add_image(el)
    flush()

    body_text = "\n\n".join(f"{s['heading']}\n" + "\n".join(s["blocks"]) for s in sections)
    return {
        "url": url,
        "article_id": article_id_for(url),
        "title": title,
        "lastmod": lastmod,
        "sections": sections,
        "content_hash": hashlib.sha256(re.sub(r"\s+", " ", body_text).encode()).hexdigest(),
    }
