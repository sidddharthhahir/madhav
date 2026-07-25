"""Independent integrity check on the ingested store.

Deliberately does not import the ingest code's own reporting -- it re-derives
every assertion from the database so a bug in the ingester cannot vouch for
itself.

    python scripts/verify_store.py
"""

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gita import canon, db, sources  # noqa: E402

FORBIDDEN = ("prabhu", "tej", "chinmay", "rams", "gambir", "adi", "san")


def main() -> int:
    conn = db.connect()
    failures: list[str] = []

    def check(label, ok, detail=""):
        print("  [%s] %s%s" % ("PASS" if ok else "FAIL", label,
                               "" if ok else "  <- " + detail))
        if not ok:
            failures.append(label)

    print("Store:", db.DEFAULT_DB)
    size_kb = db.DEFAULT_DB.stat().st_size // 1024
    print("Size: %d KB\n" % size_kb)

    print("Structure")
    counts = db.observed_counts(conn)
    total = sum(counts.values())
    recension = canon.identify_recension(counts)
    check("verse count matches a known recension (%d, %s)" % (total, recension),
          recension != "unknown",
          str(canon.diff_against_recensions(counts)))

    # Every chapter contiguous from 1..n with no holes.
    holes = []
    for ch, n in sorted(counts.items()):
        present = {r[0] for r in conn.execute(
            "SELECT verse FROM verses WHERE chapter=?", (ch,))}
        missing = set(range(1, n + 1)) - present
        if missing:
            holes.append("ch%d missing %s" % (ch, sorted(missing)))
    check("no gaps in verse numbering", not holes, "; ".join(holes))

    orphans = conn.execute(
        "SELECT COUNT(*) FROM texts WHERE verse_id NOT IN (SELECT verse_id FROM verses)"
    ).fetchone()[0]
    check("no orphaned texts", orphans == 0, "%d orphans" % orphans)

    print("\nRights policy")
    leaked = conn.execute(
        "SELECT DISTINCT source_key FROM texts WHERE source_key IN (%s)"
        % ",".join("?" * len(FORBIDDEN)), FORBIDDEN
    ).fetchall()
    check("no in-copyright source leaked into store", not leaked,
          str([r[0] for r in leaked]))

    # Re-derive the policy verdict for every row actually present.
    bad_rows = []
    for row in conn.execute("SELECT DISTINCT source_key, lang, kind FROM texts"):
        key, lang, kind = row["source_key"], row["lang"], row["kind"]
        fields = [f for f, l in sources.FIELD_LANG.items()
                  if l == lang and sources.kind_of(f) == kind]
        if not any(sources.permitted(key, f) for f in fields):
            bad_rows.append("%s/%s/%s" % (key, lang, kind))
    check("every stored row is permitted by policy", not bad_rows, str(bad_rows))

    print("\nCoverage")
    for lang, kind, label in (("en", "translation", "English translation"),
                              ("sa", "commentary", "Sanskrit commentary")):
        n = conn.execute(
            "SELECT COUNT(DISTINCT verse_id) FROM texts WHERE lang=? AND kind=?",
            (lang, kind)).fetchone()[0]
        check("%s covers all %d verses (%d)" % (label, total, n), n == total)

    for lang, label in (("hi", "Hindi"), ("gu", "Gujarati")):
        n = conn.execute(
            "SELECT COUNT(DISTINCT verse_id) FROM texts WHERE lang=?", (lang,)
        ).fetchone()[0]
        print("  [ -- ] %s coverage: %d / %d verses (expected 0 at this stage)"
              % (label, n, total))

    empty_sanskrit = conn.execute(
        "SELECT COUNT(*) FROM verses WHERE sanskrit IS NULL OR TRIM(sanskrit)=''"
    ).fetchone()[0]
    check("every verse has Sanskrit", empty_sanskrit == 0,
          "%d empty" % empty_sanskrit)

    print("\nSpot check (the three verses the reel's answer cited)")
    for vid in ("BG.7.27", "BG.3.37", "BG.16.18"):
        ch, v = canon.parse_verse_id(vid)
        row = conn.execute(
            "SELECT sanskrit FROM verses WHERE verse_id=?", (vid,)).fetchone()
        en = conn.execute(
            """SELECT source_key, LENGTH(body) AS n FROM texts
                WHERE verse_id=? AND lang='en' AND kind='translation'
                ORDER BY source_key""", (vid,)).fetchall()
        com = conn.execute(
            """SELECT COUNT(*) AS n, SUM(LENGTH(body)) AS chars FROM texts
                WHERE verse_id=? AND kind='commentary'""", (vid,)).fetchone()
        ok = bool(row and row["sanskrit"]) and len(en) >= 2 and com["n"] > 0
        check("%s: sanskrit + %d english + %d commentaries (%d chars)"
              % (vid, len(en), com["n"], com["chars"] or 0), ok)

    print()
    if failures:
        print("%d CHECK(S) FAILED: %s" % (len(failures), failures))
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
