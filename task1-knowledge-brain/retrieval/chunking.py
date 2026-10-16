"""Heading-first chunking with a size cap and limited overlap.

Strategies are selectable so they can be compared in evaluation/run_eval.py:
  - "section": one chunk per heading section, split only if above max_chars
  - "fixed":   fixed-size windows ignoring headings (baseline for comparison)
Every chunk carries title, heading and the images that sit inside that section.
"""
from __future__ import annotations

import re

MAX_CHARS = 1200
OVERLAP_CHARS = 150


def _split_long(blocks: list[str], max_chars: int, overlap: int) -> list[str]:
    """Pack whole blocks (paragraphs / lists / tables) into windows; never cut a block unless it alone exceeds the cap."""
    out: list[str] = []
    cur = ""
    for b in blocks:
        pieces = [b] if len(b) <= max_chars else re.split(r"(?<=[.!?])\s+", b)
        for p in pieces:
            if cur and len(cur) + len(p) + 1 > max_chars:
                out.append(cur)
                cur = cur[-overlap:] + "\n" + p if overlap else p
            else:
                cur = f"{cur}\n{p}" if cur else p
    if cur:
        out.append(cur)
    return out


def chunk_article(article: dict, strategy: str = "section", max_chars: int = MAX_CHARS, overlap: int = OVERLAP_CHARS) -> list[dict]:
    chunks: list[dict] = []
    if strategy == "fixed":
        flat = "\n".join(f"{s['heading']}\n" + "\n".join(s["blocks"]) for s in article["sections"])
        step = max_chars - overlap
        images = [i for s in article["sections"] for i in s["images"]]
        for n, start in enumerate(range(0, max(len(flat), 1), step)):
            chunks.append({"idx": n, "heading": article["title"], "text": flat[start:start + max_chars], "images": images if n == 0 else []})
        return chunks

    for s in article["sections"]:
        # A heading with no body (e.g. a "Prerequisites" line followed by steps) stays attached to what follows via the text prefix.
        pieces = _split_long(s["blocks"], max_chars, overlap) or [""]
        for k, piece in enumerate(pieces):
            header = f"{article['title']} > {s['heading']}" if s["heading"] != article["title"] else article["title"]
            imgs = s["images"] if k == 0 else []
            img_text = " ".join(f"[Screenshot: {i.get('description') or i['alt']}]" for i in imgs if (i.get("description") or i["alt"]))
            text = "\n".join(x for x in (header, piece, img_text) if x).strip()
            if len(text) > len(header) + 2 or imgs:
                chunks.append({"idx": len(chunks), "heading": s["heading"], "text": text, "images": imgs})
    return chunks
