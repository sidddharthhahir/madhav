"""Dense retrieval over verse embeddings, fused with BM25 via reciprocal rank
fusion. Stdlib only -- talks to a local Ollama server over `urllib` rather than
pulling in an HTTP client or a vector database.

Anthropic has no embeddings endpoint, and paying Voyage/OpenAI per query is
hard to justify for 701 short documents that fit in a few megabytes. A local
model (nomic-embed-text, 768-dim, served by `ollama serve` on localhost) costs
nothing per query and needs no API key.
"""

import array
import json
import math
import urllib.error
import urllib.request

from .bm25 import Hit

OLLAMA_URL = "http://localhost:11434/api/embed"
OLLAMA_VERSION_URL = "http://localhost:11434/api/version"
MODEL = "nomic-embed-text"
BATCH_SIZE = 32


def is_reachable(timeout: float = 1.0) -> bool:
    """Cheap liveness check -- used for /health, not the query path itself.

    A short timeout on purpose: this is a diagnostic call, not one worth
    stalling a request over if Ollama is slow to answer.
    """
    try:
        with urllib.request.urlopen(OLLAMA_VERSION_URL, timeout=timeout):
            return True
    except (urllib.error.URLError, OSError):
        return False


def _post(payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.URLError as exc:
        raise RuntimeError(
            "could not reach Ollama at %s -- is the Ollama app running? "
            "(%s)" % (OLLAMA_URL, exc)) from exc


def embed(texts: list[str], model: str = MODEL) -> list[list[float]]:
    """Embed a list of texts, batching to keep individual requests small."""
    out: list[list[float]] = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i:i + BATCH_SIZE]
        result = _post({"model": model, "input": batch})
        out.extend(result["embeddings"])
    return out


def embed_one(text: str, model: str = MODEL) -> list[float]:
    return embed([text], model)[0]


def store_embeddings(conn, vectors: dict[str, list[float]], model: str) -> None:
    for verse_id, vec in vectors.items():
        blob = array.array("f", vec).tobytes()
        conn.execute(
            """INSERT INTO embeddings (verse_id, model, dim, vector)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(verse_id) DO UPDATE SET
                 model=excluded.model, dim=excluded.dim, vector=excluded.vector""",
            (verse_id, model, len(vec), blob),
        )
    conn.commit()


def load_embeddings(conn) -> dict[str, list[float]]:
    out = {}
    for row in conn.execute("SELECT verse_id, vector FROM embeddings"):
        vec = array.array("f")
        vec.frombytes(row["vector"])
        out[row["verse_id"]] = list(vec)
    return out


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class DenseIndex:
    def __init__(self, vectors: dict[str, list[float]],
                 meta: dict[str, dict] | None = None, model: str = MODEL):
        if not vectors:
            raise ValueError("DenseIndex needs at least one embedding")
        self.vectors = vectors
        self.meta = meta or {}
        self.model = model

    def __len__(self) -> int:
        return len(self.vectors)

    def search(self, query: str, k: int = 10) -> list[Hit]:
        qvec = embed_one(query, self.model)
        scored = [(doc_id, _cosine(qvec, vec)) for doc_id, vec in self.vectors.items()]
        scored.sort(key=lambda x: -x[1])
        return [
            Hit(doc_id, score, rank, self.meta.get(doc_id, {}))
            for rank, (doc_id, score) in enumerate(scored[:k], start=1)
        ]
