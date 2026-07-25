"""Ingest CLI.

    python -m gita.ingest.run --smoke     # chapter 2 only, fast sanity check
    python -m gita.ingest.run             # full corpus

Exits non-zero if the ingest is structurally unsound (missing verses, verse
count matching no known recension, or unreviewed upstream sources). A silent
partial ingest is worse than a loud failure: it produces a chatbot that cites
verses whose text it never actually loaded.
"""

import argparse
import datetime as dt
import sys

from .. import canon, db, sources
from . import vedicscriptures as vs

ORIGIN = "vedicscriptures"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def run(*, chapters=None, use_cache=True, db_path=None) -> int:
    started = _now()
    conn = db.connect(db_path or db.DEFAULT_DB)
    problems: list[str] = []

    print("Reading chapter metadata ...")
    all_counts = vs.chapter_verse_counts(use_cache=use_cache)
    recension = canon.identify_recension(all_counts)
    total = sum(all_counts.values())
    print("  upstream reports %d verses across 18 chapters -> recension: %s"
          % (total, recension))

    if recension == "unknown":
        diffs = canon.diff_against_recensions(all_counts)
        for name, delta in diffs.items():
            if delta:
                detail = ", ".join(
                    "ch%d observed %d expected %d" % (ch, obs, exp)
                    for ch, (obs, exp) in sorted(delta.items())
                )
                problems.append("counts differ from %s: %s" % (name, detail))
        print("  WARNING: verse counts match no known recension")

    targets = sorted(chapters or canon.CHAPTERS)
    seen_keys: set[str] = set()
    verses_written = 0
    texts_written = 0
    missing_sanskrit: list[str] = []
    no_english: list[str] = []

    for ch in targets:
        n = all_counts[ch]
        conn.execute(
            """INSERT INTO chapters (chapter, title, verse_count) VALUES (?, ?, ?)
               ON CONFLICT(chapter) DO UPDATE SET
                 title = excluded.title, verse_count = excluded.verse_count""",
            (ch, canon.CHAPTER_TITLES[ch], n),
        )

        for v in range(1, n + 1):
            vid = canon.verse_id(ch, v)
            try:
                payload = vs.fetch_verse(ch, v, use_cache=use_cache)
            except vs.FetchError as exc:
                problems.append("fetch failed for %s: %s" % (vid, exc))
                continue

            verse_row, texts, keys = vs.extract(payload)
            seen_keys |= keys

            db.upsert_verse(conn, vid, ch, v,
                            verse_row["sanskrit"], verse_row["transliteration"])
            verses_written += 1
            if not verse_row["sanskrit"]:
                missing_sanskrit.append(vid)

            for t in texts:
                db.upsert_text(conn, vid, t["lang"], t["source_key"],
                               t["translator"], t["kind"], t["body"], ORIGIN)
                texts_written += 1

            if not any(t["lang"] == "en" and t["kind"] == "translation" for t in texts):
                no_english.append(vid)

        conn.commit()
        print("  chapter %2d: %2d verses" % (ch, n))

    # Any commentator we have not explicitly reviewed must be surfaced, never
    # silently ingested or silently dropped.
    unreviewed = sources.unknown_keys(seen_keys)
    if unreviewed:
        problems.append("unreviewed upstream sources: %s" % sorted(unreviewed))

    if missing_sanskrit:
        problems.append("%d verses missing Sanskrit: %s%s" % (
            len(missing_sanskrit), missing_sanskrit[:10],
            " ..." if len(missing_sanskrit) > 10 else ""))
    if no_english:
        problems.append("%d verses with no permitted English translation: %s%s" % (
            len(no_english), no_english[:10],
            " ..." if len(no_english) > 10 else ""))

    stats = {
        "verses_written": verses_written,
        "texts_written": texts_written,
        "upstream_total": total,
        "chapters": targets,
        "sources_seen": sorted(seen_keys),
        "sources_excluded": sorted(
            k for k in seen_keys
            if k in sources.POLICIES and not sources.POLICIES[k].allow
        ),
    }
    db.record_run(conn, ORIGIN, started, _now(), recension, stats, problems)
    conn.commit()

    print("\n--- rights-cleared coverage in store ---")
    for row in db.coverage(conn):
        print("  %-3s %-11s %-9s %-34s %4d verses" % (
            row["lang"], row["kind"], row["source_key"],
            row["translator"][:34], row["verses"]))

    excluded = stats["sources_excluded"]
    if excluded:
        print("\n--- excluded by rights policy (never written to DB) ---")
        for key in excluded:
            p = sources.POLICIES[key]
            print("  %-9s %-36s %s" % (key, p.translator[:36],
                                       sorted(p.deny) or "-"))

    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print("  ! %s" % p)
        return 1

    print("\nIngest clean: %d verses, %d rights-cleared texts."
          % (verses_written, texts_written))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Ingest the Gita corpus.")
    ap.add_argument("--smoke", action="store_true",
                    help="chapter 2 only, for a fast sanity check")
    ap.add_argument("--chapter", type=int, action="append", dest="chapters",
                    help="limit to specific chapter(s); repeatable")
    ap.add_argument("--no-cache", action="store_true",
                    help="bypass the on-disk cache and refetch")
    ap.add_argument("--db", default=None, help="path to the SQLite file")
    args = ap.parse_args(argv)

    chapters = [2] if args.smoke else args.chapters
    return run(chapters=chapters, use_cache=not args.no_cache, db_path=args.db)


if __name__ == "__main__":
    sys.exit(main())
