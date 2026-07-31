"""End-to-end question answering: understand -> retrieve -> ground -> answer.

The index is built once and held in memory. At 701 verses that is a few hundred
kilobytes and a few milliseconds of construction, so there is no cache-warming
story to manage and no vector database to operate.
"""

import datetime as dt
import hashlib
import json
import threading
import time
from dataclasses import asdict, dataclass, field

from . import db
from .answer import context as C
from .answer import generate as G
from .answer import validate as V
from .retrieval import corpus, counterpoint as CP, dense
from .retrieval import dilemma as DL
from .retrieval import rerank as RR
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
    # What the reranker did, if anything. Populated on every result that got
    # as far as retrieval, including {"used": False, "reason": ...} -- a
    # reranker that quietly fell back to the original order would otherwise be
    # indistinguishable from one that worked.
    rerank: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["timings"] = {s["name"]: s["ms"] for s in d["timings"]}
        return d


class Pipeline:
    # 20, not 12. Measured over the 106-question eval against cached real
    # query expansions (scripts/eval_sweep.py): k=12 gives 43 full / 23 miss,
    # k=20 gives 54 full / 11 miss -- a quarter more complete answers and
    # half the misses. The earlier 8 -> 12 move was the same finding at
    # smaller scale: expected verses cluster just past whatever cutoff is in
    # force. Costs ~67% more context tokens per answer (roughly 7k -> 11.5k),
    # which is the real price of this and the reason it is not higher still;
    # the API caps k at 20 regardless. The citation validator still only
    # permits citing what was actually shown, so a wider pool cannot make
    # citations less trustworthy -- only give the model more good material.
    def __init__(self, db_path=None, *, client=None, model: str = G.DEFAULT_MODEL,
                 max_verses: int = 20, threaded: bool = False, use_dense: bool = False,
                 local_db_path=None, use_rerank: bool = False,
                 rerank_pool: int = 30, rerank_model: str = RR.MODEL):
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
        # Reranking is the one retrieval step that costs money, so it is
        # opt-in and never implicit. When on, the shape is "retrieve a deeper
        # pool, let a cheap model pick the best k from it" -- see rerank.py
        # for why that can come out cheaper overall, and for the warning that
        # the benefit is a hypothesis rather than a measured result.
        self.use_rerank = use_rerank
        self.rerank_pool = rerank_pool
        self.rerank_model = rerank_model
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
             "enriched": r.enrichment is not None, "speaker": r.speaker,
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
            "speaker": rec.speaker,
            "other_langs": rec.other_langs,
            "commentary": sorted(rec.commentary), "enriched": rec.enrichment is not None,
            "enrichment": rec.enrichment,
        }

    # -- retrieval only (no API key required) ------------------------------

    # The fusion pool is deliberately deeper than the number of verses that
    # end up in the context. Asking each ranker for exactly k meant fusion
    # could only reorder k candidates -- a verse ranked 15th by BM25 and 3rd
    # by dense was invisible to RRF at k=12, even though agreement across the
    # two rankers is exactly the signal RRF exists to find. Depth here is
    # free: it costs a little local sorting and no tokens, because only the
    # top k are ever sent to the model.
    FUSION_POOL_MIN = 30

    def retrieve(self, query: str, k: int | None = None):
        k = k or self.max_verses
        pool = max(self.FUSION_POOL_MIN, k)
        bm25_hits = self.index.search(query, k=pool)
        if self.dense_index is None:
            return bm25_hits[:k]
        try:
            dense_hits = self.dense_index.search(query, k=pool)
        except RuntimeError:
            # Ollama unreachable at query time -- degrade to BM25 alone rather
            # than fail the whole answer over an optional enhancement.
            return bm25_hits[:k]
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
                 "score": round(v.score, 3), "enriched": v.enriched,
                 "speaker": self.records[v.verse_id].speaker}
                for v in ctx.verses
            ],
            "citable": C.citable_list(ctx),
            "approx_context_tokens": ctx.approx_tokens,
            "context": ctx.text,
        }

    def _ground(self, question: str, plan, k: int | None):
        """Retrieve the grounding set for an answer, reranking if enabled.

        Shared by ask() and ask_stream() so the two cannot drift apart -- they
        must ground on identical verses or the streamed answer and the
        non-streamed answer to the same question stop matching.

        Note which text goes where: retrieval searches the EXPANDED query
        (plan.retrieval_query), because the index is keyed on enrichment
        vocabulary the user never types. Reranking judges against the ORIGINAL
        question, because stance is about who the asker actually is, and the
        expansion has by then flattened that into topic words.
        """
        k = k or self.max_verses
        if not self.use_rerank:
            return self.retrieve(plan.retrieval_query, k), {"used": False,
                                                            "reason": "disabled"}
        # Deeper pool in, same k out: reranking only helps if it is given
        # something the ranking below k could not surface on its own.
        pool = self.retrieve(plan.retrieval_query, max(self.rerank_pool, k))
        return RR.rerank(question, self.records, pool, k=k,
                         client=self.client, model=self.rerank_model)

    def counterpoint(self, verse_ids: list[str], k: int = 5) -> dict:
        """The verses that face the other way. Free -- no model call.

        Takes the verse ids that grounded an answer rather than the question,
        so this costs nothing beyond one more local retrieval: the expensive
        step (query understanding) already happened, and its result is
        irrelevant here anyway -- the opposing query is built from the
        corpus's own stance text, not from anything the user typed.
        """
        return CP.counterpoint(self.records, self.retrieve, verse_ids, k=k)

    def dilemma(self, option_a: str, option_b: str, *, k: int = 5) -> dict:
        """Both sides of an impossible choice. Free -- no model call.

        Note what this does NOT do: expand the options through understand().
        That is the paid step, and a dilemma is two short phrases the user
        wrote deliberately, not one question to be interpreted. Retrieval runs
        on their own words. See retrieval/dilemma.py for the measurement that
        says the two sides genuinely separate.
        """
        return DL.dilemma(self.records, self.retrieve, option_a, option_b, k=k)

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
            plan, _cached = self.understand_cached(question)
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
        hits, rerank_info = self._ground(question, plan, k)
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
             "score": round(v.score, 3), "enriched": v.enriched,
             "speaker": self.records[v.verse_id].speaker}
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
                timings=timings, plan=asdict(plan), rerank=rerank_info,
            )

        return AnswerResult(
            question, generated.text, plan.language, generated.citations,
            retrieved=retrieved, ok=True, status="ok",
            detail=generated.validation.summary(), attempts=generated.attempts,
            usage=generated.usage, timings=timings, plan=asdict(plan),
            rerank=rerank_info,
        )

    def delete_history(self, entry_id: int) -> bool:
        with self._conn_lock:
            cur = self.local.execute("DELETE FROM history WHERE id=?", (entry_id,))
            self.local.commit()
        return cur.rowcount > 0

    def clear_history(self) -> int:
        """Also drops the answer cache: leaving cached answers behind after
        someone clears their history would quietly keep the text they asked to
        remove, which is not what 'clear' means to the person clicking it."""
        with self._conn_lock:
            n = self.local.execute("SELECT COUNT(*) FROM history").fetchone()[0]
            self.local.execute("DELETE FROM history")
            self.local.execute("DELETE FROM answer_cache")
            self.local.execute("DELETE FROM plan_cache")
            self.local.commit()
        return n

    # -- caches ------------------------------------------------------------

    @staticmethod
    def _norm(question: str) -> str:
        """Whitespace and case folded so trivial edits still hit the cache."""
        return " ".join(question.lower().split())

    def _plan_key(self, question: str) -> str:
        return hashlib.sha256(
            ("%s|%s" % (self.model, self._norm(question))).encode()).hexdigest()

    def _answer_key(self, question: str, k: int) -> str:
        from .answer import prompts as _P
        # The prompt text is part of the key: an answer written to older
        # instructions must not be served after the prompt changes.
        stamp = hashlib.sha256(
            (_P.ANSWER_SYSTEM + _P.QUERY_SYSTEM).encode()).hexdigest()[:16]
        return hashlib.sha256(
            ("%s|%s|%d|%s" % (self.model, stamp, k, self._norm(question))).encode()
        ).hexdigest()

    def cached_plan(self, question: str):
        with self._conn_lock:
            row = self.local.execute(
                "SELECT plan FROM plan_cache WHERE key=?",
                (self._plan_key(question),)).fetchone()
        if not row:
            return None
        return G.QueryPlan(**json.loads(row["plan"]))

    def store_plan(self, question: str, plan) -> None:
        with self._conn_lock:
            self.local.execute(
                """INSERT INTO plan_cache (key, question, plan, cached_at)
                   VALUES (?, ?, ?, ?) ON CONFLICT(key) DO NOTHING""",
                (self._plan_key(question), question, json.dumps(asdict(plan)),
                 dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")))
            self.local.commit()

    def cached_answer(self, question: str, k: int):
        with self._conn_lock:
            row = self.local.execute(
                "SELECT result FROM answer_cache WHERE key=?",
                (self._answer_key(question, k),)).fetchone()
        return json.loads(row["result"]) if row else None

    def store_answer(self, question: str, k: int, result: "AnswerResult") -> None:
        if not result.ok:
            return                      # never cache a failure
        with self._conn_lock:
            self.local.execute(
                """INSERT INTO answer_cache (key, question, result, cached_at)
                   VALUES (?, ?, ?, ?) ON CONFLICT(key) DO NOTHING""",
                (self._answer_key(question, k), question,
                 json.dumps(result.to_dict()),
                 dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")))
            self.local.commit()

    def understand_cached(self, question: str):
        """understand(), served from cache when the same question repeats."""
        hit = self.cached_plan(question)
        if hit is not None:
            return hit, True
        plan = G.understand(question, client=self.client, model=self.model)
        self.store_plan(question, plan)
        return plan, False

    # -- streaming ---------------------------------------------------------

    def ask_stream(self, question: str, *, k: int | None = None):
        """ask() as a sequence of events, so the UI can show work in progress.

        Emits (event_name, payload) pairs:
            stage      {"name": ...}          which phase is running
            retrieved  {"verses": [...]}      the grounding set, already
                                              verified, safe to show at once
            delta      {"text": ...}          PROVISIONAL answer text
            reset      {"reason": ...}        discard every delta so far
            done       AnswerResult           validated; safe to present
            failed     AnswerResult           withheld, same as ask()

        The caller MUST render deltas as unverified and MUST clear them on
        reset. See answer_stream() for why streaming cannot validate as it
        goes, and what is preserved regardless.
        """
        question = (question or "").strip()
        timings: list[Stage] = []
        if not question:
            yield ("failed", AnswerResult(question, "", "en", [], ok=False,
                                          status="empty_question",
                                          detail="no question was provided"))
            return

        yield ("stage", {"name": "understanding"})
        t0 = time.perf_counter()
        try:
            plan, _cached = self.understand_cached(question)
        except G.MissingCredentialsError as exc:
            yield ("failed", AnswerResult(question, "", "en", [], ok=False,
                                          status="no_credentials", detail=str(exc)))
            return
        except G.RefusedError as exc:
            yield ("failed", AnswerResult(question, "", "en", [], ok=False,
                                          status="refused", detail=str(exc)))
            return
        timings.append(Stage("understand", int((time.perf_counter() - t0) * 1000)))

        if not plan.on_topic:
            yield ("failed", AnswerResult(
                question, "", plan.language, [], ok=False, status="off_topic",
                detail="the Gita does not speak to this question",
                plan=asdict(plan), timings=timings))
            return

        yield ("stage", {"name": "retrieving"})
        t0 = time.perf_counter()
        hits, rerank_info = self._ground(question, plan, k)
        ctx = C.build(self.records, hits, max_verses=k or self.max_verses)
        timings.append(Stage("retrieve", int((time.perf_counter() - t0) * 1000)))

        if not ctx.verses:
            yield ("failed", AnswerResult(
                question, "", plan.language, [], ok=False, status="no_verses",
                detail="retrieval returned nothing for this question",
                plan=asdict(plan), timings=timings))
            return

        retrieved = [
            {"verse_id": v.verse_id, "rank": v.rank,
             "score": round(v.score, 3), "enriched": v.enriched,
             "speaker": self.records[v.verse_id].speaker}
            for v in ctx.verses
        ]
        yield ("retrieved", {"verses": retrieved})

        yield ("stage", {"name": "writing"})
        t0 = time.perf_counter()
        generated = None
        try:
            for kind, payload in G.answer_stream(
                question, ctx, language=plan.language, valid_ids=self.valid_ids,
                client=self.client, model=self.model,
            ):
                if kind == "delta":
                    yield ("delta", {"text": payload})
                elif kind == "reset":
                    # A draft was rejected. Tell the client to drop it before
                    # the retry starts writing.
                    yield ("reset", {"reason": payload})
                    yield ("stage", {"name": "rewriting"})
                else:
                    generated = payload
        except G.MissingCredentialsError as exc:
            yield ("failed", AnswerResult(question, "", plan.language, [], ok=False,
                                          status="no_credentials", detail=str(exc),
                                          plan=asdict(plan), timings=timings))
            return
        except G.RefusedError as exc:
            yield ("failed", AnswerResult(question, "", plan.language, [], ok=False,
                                          status="refused", detail=str(exc),
                                          plan=asdict(plan), timings=timings))
            return
        timings.append(Stage("answer", int((time.perf_counter() - t0) * 1000)))

        if generated is None or not generated.ok:
            result = AnswerResult(
                question, "", plan.language, [], retrieved=retrieved, ok=False,
                status="citation_validation_failed",
                detail=" | ".join(generated.rejected) if generated else "no output",
                attempts=generated.attempts if generated else 0,
                usage=generated.usage if generated else {},
                timings=timings, plan=asdict(plan), rerank=rerank_info)
            self.record_history(result)
            yield ("failed", result)
            return

        result = AnswerResult(
            question, generated.text, plan.language, generated.citations,
            retrieved=retrieved, ok=True, status="ok",
            detail=generated.validation.summary(), attempts=generated.attempts,
            usage=generated.usage, timings=timings, plan=asdict(plan),
            rerank=rerank_info)
        self.record_history(result)
        yield ("done", result)
