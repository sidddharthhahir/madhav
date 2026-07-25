"""Okapi BM25 over the verse corpus. Stdlib only.

At 701 documents there is no reason to reach for a search engine or a vector
database -- the whole index fits in a few hundred kilobytes and scoring a query
against every document is microseconds of work. This is the lexical half of the
retrieval stack; dense retrieval fuses in on top via reciprocal rank fusion.
"""

import math
from collections import Counter
from dataclasses import dataclass, field

from .normalize import tokenize

K1 = 1.5   # term-frequency saturation
B = 0.75   # length normalisation


@dataclass
class Doc:
    doc_id: str            # verse_id, e.g. 'BG.2.47'
    text: str
    meta: dict = field(default_factory=dict)


@dataclass
class Hit:
    doc_id: str
    score: float
    rank: int
    meta: dict = field(default_factory=dict)


class BM25:
    def __init__(self, docs: list[Doc]):
        if not docs:
            raise ValueError("BM25 needs at least one document")
        self.docs = docs
        self._tf: list[Counter[str]] = []
        self._len: list[int] = []
        df: Counter[str] = Counter()

        for doc in docs:
            tokens = tokenize(doc.text)
            tf = Counter(tokens)
            self._tf.append(tf)
            self._len.append(len(tokens))
            df.update(tf.keys())

        n = len(docs)
        self._avgdl = (sum(self._len) / n) or 1.0
        # Standard BM25 idf with the +1 guard so a term appearing in every
        # document scores a small positive value rather than going negative.
        self._idf = {
            term: math.log(1 + (n - freq + 0.5) / (freq + 0.5))
            for term, freq in df.items()
        }

    def __len__(self) -> int:
        return len(self.docs)

    @property
    def vocabulary_size(self) -> int:
        return len(self._idf)

    def score(self, query: str) -> list[float]:
        terms = tokenize(query)
        scores = [0.0] * len(self.docs)
        for term in terms:
            idf = self._idf.get(term)
            if idf is None:
                continue
            for i, tf in enumerate(self._tf):
                freq = tf.get(term)
                if not freq:
                    continue
                norm = 1 - B + B * (self._len[i] / self._avgdl)
                scores[i] += idf * (freq * (K1 + 1)) / (freq + K1 * norm)
        return scores

    def search(self, query: str, k: int = 10) -> list[Hit]:
        scored = self.score(query)
        order = sorted(range(len(scored)), key=lambda i: -scored[i])
        hits = []
        for rank, i in enumerate(order[:k], start=1):
            if scored[i] <= 0:
                break
            hits.append(Hit(self.docs[i].doc_id, scored[i], rank, self.docs[i].meta))
        return hits

    def explain(self, query: str, doc_id: str) -> list[tuple[str, float]]:
        """Per-term contribution for one document -- for debugging retrieval."""
        idx = next(i for i, d in enumerate(self.docs) if d.doc_id == doc_id)
        tf, dl = self._tf[idx], self._len[idx]
        out = []
        for term in set(tokenize(query)):
            idf = self._idf.get(term)
            freq = tf.get(term)
            if not idf or not freq:
                continue
            norm = 1 - B + B * (dl / self._avgdl)
            out.append((term, idf * (freq * (K1 + 1)) / (freq + K1 * norm)))
        return sorted(out, key=lambda x: -x[1])


def reciprocal_rank_fusion(*rankings: list[Hit], k: int = 60) -> list[Hit]:
    """Fuse ranked lists by RRF: score = sum(1 / (k + rank)).

    Rank-based rather than score-based, so BM25 scores and cosine similarities
    can be combined without calibrating them onto a shared scale -- which is
    what makes this the right fusion for a hybrid lexical/dense setup.
    """
    totals: dict[str, float] = {}
    meta: dict[str, dict] = {}
    for ranking in rankings:
        for hit in ranking:
            totals[hit.doc_id] = totals.get(hit.doc_id, 0.0) + 1.0 / (k + hit.rank)
            meta.setdefault(hit.doc_id, hit.meta)
    order = sorted(totals.items(), key=lambda kv: -kv[1])
    return [Hit(doc_id, score, rank, meta.get(doc_id, {}))
            for rank, (doc_id, score) in enumerate(order, start=1)]
