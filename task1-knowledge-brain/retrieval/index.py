"""Hybrid retrieval: TF-IDF cosine ("semantic-ish" lexical vectors) + BM25 keyword, fused with RRF.

Embedding choice: the machine this was built on had no embedding API or GPU model, so vectors are
sparse TF-IDF over word + bigram features (scikit-learn). `Embedder` is the seam: swap in a dense
model (e.g. sentence-transformers / Voyage) without touching callers. Modes "vector", "keyword" and
"hybrid" exist so evaluation can compare them on real questions rather than assume.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Optional

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = set("the a an of to in on for and or is are be how do does can i my your you it this that with from at as by what".split())


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


@dataclass
class Hit:
    chunk_id: str
    score: float  # 0..1 fused ranking score
    vector_score: float
    keyword_score: float
    row: dict


class HybridIndex:
    def __init__(self, rows: list[dict], k1: float = 1.5, b: float = 0.75):
        self.rows = rows
        self.texts = [f"{r['title']} {r['heading']} {r['text']}" for r in rows]
        self._tok = [tokenize(t) for t in self.texts]
        self._vec: Optional[TfidfVectorizer] = None
        self._mat = None
        if rows:
            self._vec = TfidfVectorizer(tokenizer=tokenize, lowercase=False, token_pattern=None, ngram_range=(1, 2), sublinear_tf=True, min_df=1)
            self._mat = self._vec.fit_transform(self.texts)
        # BM25 statistics
        self.k1, self.b = k1, b
        self._df: Counter = Counter()
        for toks in self._tok:
            self._df.update(set(toks))
        self._avgdl = (sum(len(t) for t in self._tok) / len(self._tok)) if self._tok else 1.0

    def _bm25(self, q: list[str]) -> np.ndarray:
        n = len(self.rows)
        scores = np.zeros(n)
        for i, toks in enumerate(self._tok):
            tf = Counter(toks)
            dl = len(toks)
            s = 0.0
            for term in q:
                if term not in tf:
                    continue
                idf = math.log(1 + (n - self._df[term] + 0.5) / (self._df[term] + 0.5))
                s += idf * tf[term] * (self.k1 + 1) / (tf[term] + self.k1 * (1 - self.b + self.b * dl / self._avgdl))
            scores[i] = s
        return scores

    def unknown_ratio(self, query: str) -> float:
        """Share of the query's content words that appear nowhere in the corpus.

        A strong off-domain signal (e.g. "Netflix", "pizza"): in-scope questions reuse the product's vocabulary.
        """
        q = set(tokenize(query))
        q = {t for t in q if len(t) > 2}
        if not q:
            return 0.0
        return sum(1 for t in q if self._df.get(t, 0) == 0) / len(q)

    def search(self, query: str, k: int = 5, mode: str = "hybrid") -> list[Hit]:
        if not self.rows:
            return []
        q = tokenize(query)
        vec = (self._mat @ self._vec.transform([query]).T).toarray().ravel() if self._vec is not None else np.zeros(len(self.rows))
        kw = self._bm25(q)
        kw_norm = kw / kw.max() if kw.max() > 0 else kw
        if mode == "vector":
            fused = vec
        elif mode == "keyword":
            fused = kw_norm
        else:
            # Reciprocal-rank fusion for ordering, blended with raw magnitudes so that an off-topic
            # query (all scores ~0) does not look confident just because something ranked first.
            fused = np.zeros(len(self.rows))
            for arr in (vec, kw_norm):
                order = np.argsort(-arr)
                for rank, idx in enumerate(order[:50]):
                    if arr[idx] > 0:
                        fused[idx] += 1.0 / (60 + rank + 1)
            fused = fused / (2.0 / 61) if fused.max() > 0 else fused  # 1.0 == rank-1 in both lists
            fused = fused * (0.5 * np.clip(vec / max(vec.max(), 1e-9), 0, 1) * min(1.0, float(vec.max()) * 3) + 0.5 * np.clip(kw_norm, 0, 1) * min(1.0, float(kw.max()) / 6))
            fused = fused ** 0.5  # soften so typical good matches land in 0.3-0.9
        top = np.argsort(-fused)[:k]
        return [Hit(self.rows[i]["chunk_id"], float(fused[i]), float(vec[i]), float(kw_norm[i]), self.rows[i]) for i in top if fused[i] > 0]
