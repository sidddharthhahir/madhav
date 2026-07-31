"""Build the searchable document set from the store.

Retrieval runs against the *enrichment* layer when it exists, and falls back to
the raw translation plus commentary when it does not. That fallback is the
honest-but-weak path: a user asking "why do people hate strangers online"
shares almost no vocabulary with a verse about dvandva-moha, so lexical search
over verse text alone will miss. The enrichment layer exists precisely to close
that gap, and `index_health()` reports how much of the corpus still lacks it.
"""

import json
from dataclasses import dataclass, field

from .. import db
from .bm25 import BM25, Doc

# Commentary is long and repetitive; including all of it swamps BM25 length
# normalisation and buries the verse's actual subject. Cap the contribution.
COMMENTARY_CHARS = 1500


@dataclass
class VerseRecord:
    verse_id: str
    chapter: int
    verse: int
    sanskrit: str | None
    translations: dict[str, str]     # source_key -> body (English)
    commentary: dict[str, str]       # source_key -> body
    enrichment: dict | None
    # lang -> body, for non-English translations (hi, gu). Deliberately a
    # SEPARATE field from `translations` rather than more entries in it:
    # searchable_text() and dense_text() both read `translations`, so folding
    # Hindi and Gujarati in there would put them straight into the BM25 index
    # and the embeddings. Retrieval runs on English enrichment against English
    # queries, and mixing scripts into it would add noise to every search to
    # no benefit. These are for display only.
    other_langs: dict[str, str] = field(default_factory=dict)


def load_verses(conn) -> dict[str, VerseRecord]:
    records: dict[str, VerseRecord] = {}
    for row in conn.execute(
        "SELECT verse_id, chapter, verse, sanskrit FROM verses ORDER BY chapter, verse"
    ):
        records[row["verse_id"]] = VerseRecord(
            verse_id=row["verse_id"], chapter=row["chapter"], verse=row["verse"],
            sanskrit=row["sanskrit"], translations={}, commentary={}, enrichment=None,
        )

    # 'hi' and 'gu' were previously excluded here, which is why 1,402
    # generated translations existed in the store but could not be reached by
    # any surface of the app.
    for row in conn.execute(
        "SELECT verse_id, lang, source_key, kind, body FROM texts "
        "WHERE lang IN ('en','sa','hi','gu')"
    ):
        rec = records.get(row["verse_id"])
        if rec is None:
            continue
        if row["kind"] == "translation" and row["lang"] == "en":
            rec.translations[row["source_key"]] = row["body"]
        elif row["kind"] == "commentary" and row["lang"] == "en":
            rec.commentary[row["source_key"]] = row["body"]
        elif row["kind"] == "translation" and row["lang"] in ("hi", "gu"):
            rec.other_langs[row["lang"]] = row["body"]

    for row in conn.execute(
        """SELECT verse_id, summary, themes, situations, emotions, keywords
             FROM enrichment"""
    ):
        rec = records.get(row["verse_id"])
        if rec is None:
            continue
        rec.enrichment = {
            "summary": row["summary"] or "",
            "themes": json.loads(row["themes"] or "[]"),
            "situations": json.loads(row["situations"] or "[]"),
            "emotions": json.loads(row["emotions"] or "[]"),
            "keywords": json.loads(row["keywords"] or "[]"),
        }
    return records


def searchable_text(rec: VerseRecord) -> str:
    """The text BM25 actually indexes for one verse."""
    parts: list[str] = []

    if rec.enrichment:
        e = rec.enrichment
        parts.append(e["summary"])
        # Themes/situations/emotions/keywords repeated once each is enough --
        # BM25 saturates term frequency, so duplicating them to "boost" the
        # signal buys almost nothing and distorts length normalisation.
        for key in ("themes", "situations", "emotions", "keywords"):
            parts.extend(e[key])

    parts.extend(rec.translations.values())
    for body in rec.commentary.values():
        parts.append(body[:COMMENTARY_CHARS])

    return "\n".join(p for p in parts if p)


def dense_text(rec: VerseRecord) -> str:
    """The text dense retrieval embeds for one verse.

    A single pooled embedding vector over a long, heterogeneous document (the
    enrichment prose plus literal translations plus word-by-word Sanskrit
    glosses) dilutes the semantic signal the enrichment layer exists to carry.
    BM25 does not have this problem -- each term scores independently -- but
    dense retrieval does, so it gets a narrower, more concentrated input:
    enrichment only, falling back to the translation when unenriched.
    """
    if rec.enrichment:
        e = rec.enrichment
        parts = [e["summary"]]
        for key in ("themes", "situations", "emotions", "keywords"):
            parts.extend(e[key])
        return "\n".join(p for p in parts if p)
    return "\n".join(rec.translations.values())


def build_index(conn) -> tuple[BM25, dict[str, VerseRecord]]:
    records = load_verses(conn)
    docs = [
        Doc(
            doc_id=rec.verse_id,
            text=searchable_text(rec),
            meta={"chapter": rec.chapter, "verse": rec.verse,
                  "enriched": rec.enrichment is not None},
        )
        for rec in records.values()
    ]
    return BM25(docs), records


def index_health(records: dict[str, VerseRecord]) -> dict:
    total = len(records)
    enriched = sum(1 for r in records.values() if r.enrichment)
    return {
        "verses": total,
        "enriched": enriched,
        "unenriched": total - enriched,
        "enrichment_coverage": round(enriched / total, 4) if total else 0.0,
        "mode": "enrichment" if enriched == total
                else "fallback" if enriched == 0
                else "mixed",
    }


def open_index(db_path=None):
    conn = db.connect(db_path or db.DEFAULT_DB)
    index, records = build_index(conn)
    return conn, index, records
