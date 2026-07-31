"""End-to-end question answering: understand -> retrieve -> ground -> answer.

The index is built once and held in memory. At 701 verses that is a few hundred
kilobytes and a few milliseconds of construction, so there is no cache-warming
story to manage and no vector database to operate.
"""

import datetime as dt
import json
import threading
import time
from dataclasses import asdict, dataclass, field

from . import db
from .answer import context as C
from .answer import generate as G
from .answer import validate as V
from .retrieval import corpus, dense
from .retrieval.bm25 import reciprocal_rank_fusion


@dataclass
class Stage:
    name: str
    ms: int


@dataclass
class AnswerResult:
    question: str
    answer: str
    language: str
    citations: list[str]
    retrieved: list[dict] = field(default_factory=list)
    ok: bool = True
    status: str = "ok"
    detail: str = ""
    attempts: int = 1
    usage: dict = field(default_factory=dict)
    timings: list[Stage] = field(default_factory=list)
    plan: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["timings"] = {s["name"]: s["ms"] for s in d["timings"]}
        return d


class Pipeline:
    # 12, not 8: many eval misses turned out to be verses ranked 8-12,
    # displaced just past the old cutoff by a thematically adjacent but
    # differently-specific verse (e.g. "attached to outcomes" pulls in the
    # famous nishkama-karma cluster ahead of the more specific dwelling ->
    # attachment -> craving chain in BG.2.62, which sits at dense rank 42).
    # Widening the pool recovered a third of full recall on the free
    # raw-text eval (17/106 -> 26/106) for ~50% more context tokens per
    # answer -- a real cost increase, but a small one, and the citation
    # validator still only allows citing what the model was actually shown.
    def __init__(self, db_path=None, *, client=None, model: str = G.DEFAULT_MODEL,
                 max_verses: int = 12, threaded: bool = False, use_dense: bool = False,
                 local_db_path=None):
        self.conn = db.connect(db_path or db.DEFAULT_DB,
                               check_same_thread=not threaded)
        # Personal state (what was asked, what was kept) lives in its own
        # gitignored file so the corpus stays safe to commit. Anything
        # already sitting in the corpus from before the split is moved here
        # on open.
        self.local = db.connect_local(local_db_path or db.DEFAULT_LOCAL_DB,
                                      check_same_thread=not threaded)
        db.migrate_local_out_of_corpus(self.conn, self.local)
        self.index, self.records = corpus.build_index(self.conn)
        # Off by default: dense retrieval calls out to a local Ollama server,
        # and Pipeline() must keep working (API server, test suites) whether
        # or not that's running. Opt in explicitly once embeddings are built
        # via scripts/build_embeddings.py.
        self.dense_index = None
        if use_dense:
            vectors = dense.load_embeddings(self.conn)
            if vectors:
                meta = {vid: {"chapter": r.chapter, "verse": r.verse}
                         for vid, r in self.records.items()}
                self.dense_index = dense.DenseIndex(vectors, meta)
        self.valid_ids = V.known_verse_ids(self.conn)
        # Snapshotted at construction so nothing on the request path touches
        # SQLite. Everything answering a question -- index, records, valid ids,
        # languages -- lives in memory, which keeps the connection out of the
        # threadpool entirely rather than relying on check_same_thread.
        self.corpus_languages = sorted(
            r[0] for r in self.conn.execute("SELECT DISTINCT lang FROM texts")
        )
        self.client = client
        self.model = model
        self.max_verses = max_verses
        # chapters/history/saved reads and the history/saved-verse writes are
        # the only runtime SQLite access. Under a threaded server they arrive
        # from arbitrary worker threads on a connection opened with
        # check_same_thread=False, which disables SQLite's own guard but does
        # not make concurrent access safe -- two threads calling execute() on
        # the same connection at once can corrupt memory (SIGSEGV inside
        # libsqlite3, not a Python exception), so every access -- reads
        # included -- must go through this lock.
        self._conn_lock = threading.Lock()

    def close(self) -> None:
        self.conn.close()
        self.local.close()

    # -- sidebar state -----------------------------------------------------

    def chapters(self) -> list[dict]:
        with self._conn_lock:
            rows = self.conn.execute(
                """SELECT c.chapter, c.title, c.verse_count,
                          (SELECT COUNT(*) FROM enrichment e
                             JOIN verses v ON v.verse_id = e.verse_id
                            WHERE v.chapter = c.chapter) AS enriched
                     FROM chapters c ORDER BY c.chapter"""
            ).fetchall()
        return [dict(r) for r in rows]

    def chapter_verses(self, chapter: int) -> list[dict]:
        return [
            {"verse_id": r.verse_id, "chapter": r.chapter, "verse": r.verse,
             "enriched": r.enrichment is not None,
             "preview": next(iter(r.translations.values()), "")[:140]}
            for r in self.records.values() if r.chapter == chapter
        ]

    def record_history(self, result: "AnswerResult") -> None:
        with self._conn_lock:
            self.local.execute(
                """INSERT INTO history (question, language, status, citations,
                                        answer, asked_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (result.question, result.language, result.status,
                 json.dumps(result.citations), result.answer,
                 dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")),
            )
            self.local.commit()

    def history(self, limit: int = 30) -> list[dict]:
        with self._conn_lock:
            rows = self.local.execute(
                "SELECT * FROM history ORDER BY asked_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r, citations=json.loads(r["citations"] or "[]")) for r in rows]

    def saved(self) -> list[dict]:
        with self._conn_lock:
            rows = self.local.execute(
                "SELECT verse_id, note, saved_at FROM saved_verses ORDER BY saved_at DESC"
            ).fetchall()
        out = []
        for r in rows:
            rec = self.records.get(r["verse_id"])
            out.append({
                "verse_id": r["verse_id"], "note": r["note"], "saved_at": r["saved_at"],
                "chapter": rec.chapter if rec else None,
                "verse": rec.verse if rec else None,
            })
        return out

    def save_verse(self, verse_id: str, note: str | None = None) -> bool:
        if verse_id not in self.records:
            return False
        with self._conn_lock:
            self.local.execute(
                """INSERT INTO saved_verses (verse_id, note, saved_at) VALUES (?, ?, ?)
                   ON CONFLICT(verse_id) DO UPDATE SET note=excluded.note""",
                (verse_id, note,
                 dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")),
            )
            self.local.commit()
        return True

    def unsave_verse(self, verse_id: str) -> None:
        with self._conn_lock:
            self.local.execute(
                "DELETE FROM saved_verses WHERE verse_id=?", (verse_id,))
            self.local.commit()

    # -- introspection -----------------------------------------------------

    def health(self) -> dict:
        h = corpus.index_health(self.records)
        h.update({
            "bm25_documents": len(self.index),
            "vocabulary": self.index.vocabulary_size,
            "model": self.model,
            "languages_in_corpus": self.corpus_languages,
            "dense_index": {
                "configured": self.dense_index is not None,
                "model": self.dense_index.model if self.dense_index else None,
                "documents": len(self.dense_index) if self.dense_index else 0,
                # Live check, not just "embeddings were loaded at startup" --
                # Ollama can go down independently of the pipeline's lifetime.
                "ollama_reachable": dense.is_reachable() if self.dense_index else False,
            },
        })
        return h

    def verse(self, verse_id: str) -> dict | None:
        rec = self.records.get(verse_id)
        if rec is None:
            return None
        return {
            "verse_id": rec.verse_id, "chapter": rec.chapter, "verse": rec.verse,
            "sanskrit": rec.sanskrit, "translations": rec.translations,
            "commentary": sorted(rec.commentary), "enriched": rec.enrichment is not None,
            "enrichment": rec.enrichment,
        }

    # -- retrieval only (no API key required) ------------------------------

    def retrieve(self, query: str, k: int | None = None):
        k = k or self.max_verses
        bm25_hits = self.index.search(query, k=k)
        if self.dense_index is None:
            return bm25_hits
        try:
            dense_hits = self.dense_index.search(query, k=k)
        except RuntimeError:
            # Ollama unreachable at query time -- degrade to BM25 alone rather
            # than fail the whole answer over an optional enhancement.
            return bm25_hits
        return reciprocal_rank_fusion(bm25_hits, dense_hits)[:k]

    def preview(self, question: str, k: int | None = None) -> dict:
        """Retrieval + context assembly with no model calls.

        Lets the grounding material be inspected without spending anything --
        the fastest way to tell a retrieval problem from a generation problem.
        """
        hits = self.retrieve(question, k)
        ctx = C.build(self.records, hits, max_verses=k or self.max_verses)
        return {
            "question": question,
            "retrieved": [
                {"verse_id": v.verse_id, "rank": v.rank,
                 "score": round(v.score, 3), "enriched": v.enriched}
                for v in ctx.verses
            ],
            "citable": C.citable_list(ctx),
            "approx_context_tokens": ctx.approx_tokens,
            "context": ctx.text,
        }

    # -- full pipeline -----------------------------------------------------

    def ask(self, question: str, *, k: int | None = None) -> AnswerResult:
        question = (question or "").strip()
        timings: list[Stage] = []

        if not question:
            return AnswerResult(question, "", "en", [], ok=False,
                                status="empty_question",
                                detail="no question was provided")

        t0 = time.perf_counter()
        try:
            plan = G.understand(question, client=self.client, model=self.model)
        except G.MissingCredentialsError as exc:
            return AnswerResult(question, "", "en", [], ok=False,
                                status="no_credentials", detail=str(exc))
        except G.RefusedError as exc:
            return AnswerResult(question, "", "en", [], ok=False,
                                status="refused", detail=str(exc))
        timings.append(Stage("understand", int((time.perf_counter() - t0) * 1000)))

        if not plan.on_topic:
            return AnswerResult(
                question, "", plan.language, [], ok=False, status="off_topic",
                detail="the Gita does not speak to this question",
                plan=asdict(plan), timings=timings,
            )

        t0 = time.perf_counter()
        hits = self.retrieve(plan.retrieval_query, k)
        ctx = C.build(self.records, hits, max_verses=k or self.max_verses)
        timings.append(Stage("retrieve", int((time.perf_counter() - t0) * 1000)))

        if not ctx.verses:
            return AnswerResult(
                question, "", plan.language, [], ok=False, status="no_verses",
                detail="retrieval returned nothing for this question",
                plan=asdict(plan), timings=timings,
            )

        t0 = time.perf_counter()
        try:
            generated = G.answer(
                question, ctx, language=plan.language, valid_ids=self.valid_ids,
                client=self.client, model=self.model,
            )
        except G.MissingCredentialsError as exc:
            return AnswerResult(question, "", plan.language, [], ok=False,
                                status="no_credentials", detail=str(exc),
                                plan=asdict(plan), timings=timings)
        except G.RefusedError as exc:
            return AnswerResult(question, "", plan.language, [], ok=False,
                                status="refused", detail=str(exc),
                                plan=asdict(plan), timings=timings)
        timings.append(Stage("answer", int((time.perf_counter() - t0) * 1000)))

        retrieved = [
            {"verse_id": v.verse_id, "rank": v.rank,
             "score": round(v.score, 3), "enriched": v.enriched}
            for v in ctx.verses
        ]

        if not generated.ok:
            # Validation failed after every retry. The text exists but is not
            # trustworthy, so it is reported as a failure rather than returned
            # as an answer.
            return AnswerResult(
                question, "", plan.language, [], retrieved=retrieved, ok=False,
                status="citation_validation_failed",
                detail=" | ".join(generated.rejected),
                attempts=generated.attempts, usage=generated.usage,
                timings=timings, plan=asdict(plan),
            )

        return AnswerResult(
            question, generated.text, plan.language, generated.citations,
            retrieved=retrieved, ok=True, status="ok",
            detail=generated.validation.summary(), attempts=generated.attempts,
            usage=generated.usage, timings=timings, plan=asdict(plan),
        )
