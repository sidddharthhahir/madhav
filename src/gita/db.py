"""SQLite store. Stdlib only -- stage 1 runs with zero installs.

The corpus is ~700 verses. That is small enough that SQLite plus in-process
vectors beats any hosted vector database on latency, cost and operational
burden, and the whole store can be committed alongside the code.
"""

import json
import sqlite3
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parents[2] / "data" / "gita.sqlite3"

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- One row per verse. Sanskrit and transliteration are public domain.
CREATE TABLE IF NOT EXISTS verses (
    verse_id        TEXT PRIMARY KEY,       -- 'BG.2.47'
    chapter         INTEGER NOT NULL,
    verse           INTEGER NOT NULL,
    sanskrit        TEXT,
    transliteration TEXT,
    UNIQUE (chapter, verse)
);

CREATE TABLE IF NOT EXISTS chapters (
    chapter      INTEGER PRIMARY KEY,
    title        TEXT,
    verse_count  INTEGER
);

-- Rights-cleared text only. Anything the policy layer denies never lands here,
-- so the store itself is safe to redistribute.
CREATE TABLE IF NOT EXISTS texts (
    verse_id    TEXT NOT NULL REFERENCES verses(verse_id) ON DELETE CASCADE,
    lang        TEXT NOT NULL,              -- 'en' | 'hi' | 'gu' | 'sa'
    source_key  TEXT NOT NULL,              -- 'purohit', 'siva', 'gandhi', ...
    translator  TEXT NOT NULL,
    kind        TEXT NOT NULL,              -- 'translation' | 'commentary'
    body        TEXT NOT NULL,
    origin      TEXT NOT NULL,              -- 'vedicscriptures' | 'wikisource' | 'derived'
    PRIMARY KEY (verse_id, lang, source_key, kind)
);

CREATE INDEX IF NOT EXISTS texts_by_lang ON texts (lang, kind);
CREATE INDEX IF NOT EXISTS texts_by_verse ON texts (verse_id);

-- The bridge layer: an English description of the human problems a verse
-- speaks to. Retrieval runs against this, not against the verse text, because
-- a user asking about online hate shares no vocabulary with a verse about
-- dvandva-moha. Generated once, offline, and cached here.
CREATE TABLE IF NOT EXISTS enrichment (
    verse_id     TEXT PRIMARY KEY REFERENCES verses(verse_id) ON DELETE CASCADE,
    summary      TEXT,                      -- plain-language gist
    themes       TEXT,                      -- JSON array
    situations   TEXT,                      -- JSON array of modern scenarios
    emotions     TEXT,                      -- JSON array
    stance       TEXT,                      -- JSON array; which side of a
                                            -- feeling the verse speaks to
    keywords     TEXT,                      -- JSON array
    model        TEXT,
    prompt_hash  TEXT,
    generated_at TEXT
);

-- Dense vectors over the enrichment text. BLOB of float32; 700 rows is ~3MB.
CREATE TABLE IF NOT EXISTS embeddings (
    verse_id  TEXT PRIMARY KEY REFERENCES verses(verse_id) ON DELETE CASCADE,
    model     TEXT NOT NULL,
    dim       INTEGER NOT NULL,
    vector    BLOB NOT NULL
);

-- NOTE: history and saved_verses deliberately do NOT live here. They hold
-- what the person using this actually asked, which is personal, while this
-- database is the redistributable corpus and is committed to a public
-- repository. Keeping both in one file meant a single `git add` would
-- publish someone's questions permanently. They live in LOCAL_SCHEMA below,
-- in a separate gitignored file.

-- Tracks Message Batches submitted for enrichment so a run can be resumed.
-- Enrichment is a one-time cost over 701 verses; losing a batch id would mean
-- paying it twice.
CREATE TABLE IF NOT EXISTS enrich_batches (
    batch_id     TEXT PRIMARY KEY,
    model        TEXT NOT NULL,
    prompt_hash  TEXT NOT NULL,
    verse_ids    TEXT NOT NULL,          -- JSON array
    submitted_at TEXT NOT NULL,
    collected_at TEXT,
    status       TEXT NOT NULL,          -- 'submitted' | 'collected' | 'failed'
    stats        TEXT                    -- JSON
);

-- Same pattern as enrich_batches, for the Hindi/Gujarati translation batch.
-- Kept as a separate table rather than reusing enrich_batches so the two
-- one-time jobs don't share a resume/status namespace.
CREATE TABLE IF NOT EXISTS translate_batches (
    batch_id     TEXT PRIMARY KEY,
    model        TEXT NOT NULL,
    prompt_hash  TEXT NOT NULL,
    verse_ids    TEXT NOT NULL,          -- JSON array
    submitted_at TEXT NOT NULL,
    collected_at TEXT,
    status       TEXT NOT NULL,          -- 'submitted' | 'collected' | 'failed'
    stats        TEXT                    -- JSON
);

-- Provenance and reproducibility for every ingest run.
CREATE TABLE IF NOT EXISTS ingest_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    origin      TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    recension   TEXT,
    stats       TEXT,                       -- JSON
    problems    TEXT                        -- JSON array; empty means clean
);
"""


DEFAULT_LOCAL_DB = Path(__file__).resolve().parents[2] / "data" / "local.sqlite3"

# Personal, per-machine state: what was asked and what was kept. Never
# committed (see .gitignore) and never redistributed. saved_verses carries no
# foreign key to verses because the corpus lives in a different file now --
# Pipeline.save_verse already rejects unknown ids against the in-memory
# records, which is where that check belonged anyway.
LOCAL_SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    question   TEXT NOT NULL,
    language   TEXT,
    status     TEXT,
    citations  TEXT,                    -- JSON array of verse_ids
    answer     TEXT,                    -- full answer text, so a reload can
                                        -- restore the last conversation
                                        -- without paying to re-generate it
    asked_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS history_recent ON history (asked_at DESC);

CREATE TABLE IF NOT EXISTS saved_verses (
    verse_id  TEXT PRIMARY KEY,
    note      TEXT,
    saved_at  TEXT NOT NULL
);

-- Response caches. Local, disposable, and personal-adjacent (they are keyed by
-- what was asked), so they live here rather than in the corpus.
--
-- understand() runs on every question and is the slowest stage as well as a
-- per-call charge, so asking the same thing twice paid twice and waited twice
-- for an answer that cannot differ. Keyed on the normalised question plus the
-- model, since a different model would plan differently.
CREATE TABLE IF NOT EXISTS plan_cache (
    key        TEXT PRIMARY KEY,      -- sha256(model + normalised question)
    question   TEXT NOT NULL,
    plan       TEXT NOT NULL,         -- JSON QueryPlan
    cached_at  TEXT NOT NULL
);

-- Whole-answer cache. Keyed on the retrieval inputs AND the prompt hash, so a
-- prompt change invalidates every entry rather than serving text written to
-- older instructions.
CREATE TABLE IF NOT EXISTS answer_cache (
    key         TEXT PRIMARY KEY,     -- sha256(model + prompt + k + question)
    question    TEXT NOT NULL,
    result      TEXT NOT NULL,        -- JSON AnswerResult
    cached_at   TEXT NOT NULL
);
"""


def connect_local(path: Path | str = DEFAULT_LOCAL_DB, *,
                  check_same_thread: bool = True) -> sqlite3.Connection:
    """Open the personal store, creating it if this is the first run."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.executescript(LOCAL_SCHEMA)
    return conn


def migrate_local_out_of_corpus(corpus: sqlite3.Connection,
                                local: sqlite3.Connection) -> dict:
    """Move any history/saved rows still sitting in the corpus file.

    Existing installs (including the one this was written on) already have
    personal rows inside data/gita.sqlite3. Moving them is the whole point of
    the split, so it happens automatically on open rather than waiting for
    someone to run a script they don't know exists. Rows are copied first and
    only deleted once the copy has committed.
    """
    moved = {"history": 0, "saved_verses": 0}
    names = {r[0] for r in corpus.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}

    if "history" in names:
        cols = [r["name"] for r in corpus.execute("PRAGMA table_info(history)")]
        keep = [c for c in cols if c != "id"]
        rows = corpus.execute(
            "SELECT %s FROM history" % ", ".join(keep)).fetchall()
        if rows:
            local.executemany(
                "INSERT INTO history (%s) VALUES (%s)"
                % (", ".join(keep), ", ".join("?" * len(keep))),
                [tuple(r[c] for c in keep) for r in rows])
            local.commit()
            corpus.execute("DELETE FROM history")
            corpus.commit()
            moved["history"] = len(rows)

    if "saved_verses" in names:
        rows = corpus.execute(
            "SELECT verse_id, note, saved_at FROM saved_verses").fetchall()
        if rows:
            local.executemany(
                """INSERT INTO saved_verses (verse_id, note, saved_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(verse_id) DO NOTHING""",
                [(r["verse_id"], r["note"], r["saved_at"]) for r in rows])
            local.commit()
            corpus.execute("DELETE FROM saved_verses")
            corpus.commit()
            moved["saved_verses"] = len(rows)

    return moved


def connect(path: Path | str = DEFAULT_DB, *,
            check_same_thread: bool = True) -> sqlite3.Connection:
    """Open the store.

    `check_same_thread=False` is needed when the caller is a threaded server:
    FastAPI dispatches sync endpoints to a worker threadpool, so a connection
    opened during startup is used from a different thread than it was created
    on, which SQLite refuses by default. Pass it only where access is
    serialised or read-only -- the flag disables a real safety check rather
    than making the connection thread-safe.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn) -> None:
    """Additive column migrations for databases created before they existed.

    `CREATE TABLE IF NOT EXISTS` in SCHEMA is a no-op against an existing
    table, so a column added there later needs an explicit ALTER TABLE here
    or every already-committed copy of the database silently lacks it.

    The history table is guarded rather than assumed: it no longer exists in
    a freshly-created corpus (it moved to LOCAL_SCHEMA), but it is still
    present in every already-committed copy, where its rows have yet to be
    migrated out. PRAGMA on a missing table returns no rows, which would
    otherwise look identical to "column missing" and try to ALTER a table
    that isn't there.
    """
    has_history = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='history'"
    ).fetchone()
    if not has_history:
        return
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(history)")}
    if "answer" not in cols:
        conn.execute("ALTER TABLE history ADD COLUMN answer TEXT")
        conn.commit()

    ecols = {r["name"] for r in conn.execute("PRAGMA table_info(enrichment)")}
    if ecols and "stance" not in ecols:
        conn.execute("ALTER TABLE enrichment ADD COLUMN stance TEXT")
        conn.commit()


def upsert_verse(conn, verse_id, chapter, verse, sanskrit, transliteration):
    conn.execute(
        """INSERT INTO verses (verse_id, chapter, verse, sanskrit, transliteration)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(verse_id) DO UPDATE SET
             sanskrit        = excluded.sanskrit,
             transliteration = excluded.transliteration""",
        (verse_id, chapter, verse, sanskrit, transliteration),
    )


def upsert_text(conn, verse_id, lang, source_key, translator, kind, body, origin):
    conn.execute(
        """INSERT INTO texts (verse_id, lang, source_key, translator, kind, body, origin)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(verse_id, lang, source_key, kind) DO UPDATE SET
             body       = excluded.body,
             translator = excluded.translator,
             origin     = excluded.origin""",
        (verse_id, lang, source_key, translator, kind, body, origin),
    )


def observed_counts(conn) -> dict[int, int]:
    rows = conn.execute(
        "SELECT chapter, COUNT(*) AS n FROM verses GROUP BY chapter"
    ).fetchall()
    return {r["chapter"]: r["n"] for r in rows}


def coverage(conn) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT lang, kind, source_key, translator, COUNT(*) AS verses
             FROM texts
            GROUP BY lang, kind, source_key
            ORDER BY lang, kind, verses DESC"""
    ).fetchall()


def record_run(conn, origin, started_at, finished_at, recension, stats, problems):
    conn.execute(
        """INSERT INTO ingest_runs
             (origin, started_at, finished_at, recension, stats, problems)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (origin, started_at, finished_at, recension,
         json.dumps(stats, ensure_ascii=False),
         json.dumps(problems, ensure_ascii=False)),
    )
