"""Screenshot understanding. Descriptions are searchable (they are written into chunk text).

With a Claude key, each screenshot is sent to a vision model together with the surrounding article text.
Without one, only the HTML alt text is indexed and the image is flagged `uncertain` - we never invent labels.
"""
from __future__ import annotations

from app.llm import LLM

MAX_IMAGES_PER_ARTICLE = 12


def enrich_images(article: dict, llm: LLM, previous: dict | None = None) -> int:
    """Fill `description`/`uncertain` on every image. Reuses descriptions from the previous version if the URL is unchanged."""
    cache: dict[str, dict] = {}
    if previous:
        for s in previous.get("sections", []):
            for im in s["images"]:
                if im.get("description"):
                    cache[im["url"]] = im
    described = 0
    budget = MAX_IMAGES_PER_ARTICLE
    for s in article["sections"]:
        for im in s["images"]:
            old = cache.get(im["url"])
            if old:
                im["description"], im["uncertain"] = old["description"], old.get("uncertain", True)
                continue
            im.setdefault("description", None)
            im["uncertain"] = True
            if budget <= 0:
                continue
            res = llm.describe_image(im["url"], im.get("context") or s["heading"])
            budget -= 1
            if res and res.get("description"):
                im["description"], im["uncertain"] = res["description"], bool(res.get("uncertain", False))
                described += 1
    return described
