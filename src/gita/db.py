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

-- Sidebar state for the desktop app. Local-only, single user; there is no
-- account model here and deliberately so.
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
    verse_id  TEXT PRIMARY KEY REFERENCES verses(verse_id) ON DELETE CASCADE,
    note      TEXT,
    saved_at  TEXT NOT NULL
);

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
    """
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(history)")}
    if "answer" not in cols:
        conn.execute("ALTER TABLE history ADD COLUMN answer TEXT")
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
