"""Verify the two pre-enrichment fixes: OCR repair and clamp-not-discard.

    python scripts/test_prefixes.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gita import db, textclean  # noqa: E402
from gita.enrich import prompt as P  # noqa: E402
from gita.retrieval import corpus  # noqa: E402

failures = 0


def check(label, ok, detail=""):
    global failures
    if not ok:
        failures += 1
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", label,
                           "" if ok else "  <- " + str(detail)[:150]))


def main() -> int:
    print("FIX 1 -- OCR repair on the real corpus")
    conn = db.connect()
    rows = [r[0] for r in conn.execute(
        "SELECT body FROM texts WHERE lang='en' AND kind='commentary'")]

    before = sum(textclean.damage_score(b) for b in rows)
    after = sum(textclean.damage_score(textclean.repair(b)) for b in rows)
    print("      stray marks before : %d" % before)
    print("      stray marks after  : %d" % after)
    check("repair removes >95%% of damage",
          after < before * 0.05, "before=%d after=%d" % (before, after))

    dirty_rows = sum(1 for b in rows if textclean.damage_score(b) > 3)
    clean_rows = sum(1 for b in rows
                     if textclean.damage_score(textclean.repair(b)) > 3)
    print("      damaged rows before: %d / %d" % (dirty_rows, len(rows)))
    print("      damaged rows after : %d / %d" % (clean_rows, len(rows)))
    check("nearly all rows clean after repair", clean_rows <= 5, clean_rows)

    # Repair must not eat real content.
    lengths_ok = all(
        len(textclean.repair(b)) > len(b) * 0.85 for b in rows if len(b) > 200)
    check("repair preserves >85%% of text length", lengths_ok)

    # A genuine question must survive.
    q = "What is the nature of the Self? Krishna answers this directly."
    check("real question mark preserved", "Self?" in textclean.repair(q),
          textclean.repair(q))

    # Devanagari must be untouched.
    deva = "धर्मक्षेत्रे कुरुक्षेत्रे समवेता युयुत्सवः"
    check("Devanagari unchanged", textclean.repair(deva) == deva)

    print("\n      repair reaches the enrichment prompt")
    _, records = corpus.build_index(conn)
    rec = records["BG.2.47"]
    turn = P.build_user_turn(rec)
    raw_commentary = next(iter(rec.commentary.values()), "")
    check("raw commentary is damaged", textclean.damage_score(raw_commentary) > 3,
          textclean.damage_score(raw_commentary))
    check("prompt text is clean", textclean.damage_score(turn) <= 1,
          textclean.damage_score(turn))

    print("\nFIX 2 -- overflow is trimmed, not discarded")
    overflow = {
        "summary": "A sufficiently long summary of what this verse is actually "
                   "saying about ordinary human experience and behaviour.",
        "themes": ["t%d" % i for i in range(20)],        # max 12
        "situations": ["s%d" % i for i in range(25)],    # max 14
        "emotions": ["e%d" % i for i in range(15)],      # max 10
        "keywords": ["k%d" % i for i in range(40)],      # max 20
    }
    record, notes = P.normalise_enrichment(overflow)
    problems = P.validate_enrichment(record)
    check("over-long record now VALIDATES", not problems, problems)
    check("4 fields reported as trimmed", len(notes) == 4, notes)
    check("themes clamped to 12", len(record["themes"]) == 12)
    check("situations clamped to 14", len(record["situations"]) == 14)
    check("keywords clamped to 20", len(record["keywords"]) == 20)

    print("\n      genuinely broken records still fail")
    for label, bad in (
        ("too-short summary", {"summary": "Short.", "themes": ["a", "b", "c"],
                               "situations": ["a", "b", "c", "d"],
                               "emotions": ["a", "b"], "keywords": list("abcde")}),
        ("too few situations", {"summary": "x" * 60, "themes": ["a", "b", "c"],
                               "situations": ["only one"], "emotions": ["a", "b"],
                               "keywords": list("abcde")}),
        ("missing field", {"summary": "x" * 60, "themes": ["a", "b", "c"],
                           "emotions": ["a", "b"], "keywords": list("abcde")}),
    ):
        rec2, _ = P.normalise_enrichment(bad)
        check("%s rejected" % label, bool(P.validate_enrichment(rec2)))

    print("\n      duplicates are collapsed")
    dupes = {"summary": "x" * 60,
             "themes": ["Envy", "envy", "ENVY", "pride", "anger"],
             "situations": ["a", "a", "b", "c", "d"],
             "emotions": ["fear", "Fear", "dread"],
             "keywords": ["k1", "k1", "k2", "k3", "k4", "k5"]}
    rec3, _ = P.normalise_enrichment(dupes)
    check("case-insensitive dedupe on themes", len(rec3["themes"]) == 3,
          rec3["themes"])

    print()
    if failures:
        print("%d FAILURE(S)" % failures)
        return 1
    print("All pre-enrichment fix checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
