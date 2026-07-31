"""Speaker attribution checks. No credential, no spending.

Attribution is derived from the Sanskrit at load time rather than stored, so
these run against the real corpus and would catch a change in the text, in the
markers, or in the ordering assumption.

    python scripts/test_speakers.py
"""

import collections
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gita import db, speakers  # noqa: E402
from gita.answer import context as C  # noqa: E402
from gita.retrieval import corpus  # noqa: E402

failures = 0


def check(label, ok, detail=""):
    global failures
    if not ok:
        failures += 1
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", label,
                           "" if ok else "  <- " + str(detail)[:200]))


def main() -> int:
    conn = db.connect()
    records = corpus.load_verses(conn)
    counts = collections.Counter(r.speaker for r in records.values())

    print("1. counts match the traditional attribution")
    for name, expected in speakers.EXPECTED.items():
        check("%s speaks %d verses" % (name, expected),
              counts[name] == expected, "got %d" % counts[name])
    check("every verse has a speaker", sum(counts.values()) == 701,
          sum(counts.values()))

    print("\n2. the sandhi trap stays fixed")
    # bhagavan + uvaca fuses, turning the independent vowel U+0909 into the
    # dependent sign U+0941. A bare "उवाच" search silently loses all 574 of
    # Krishna's verses -- the failure this asserts against is not an error,
    # it is a plausible wrong answer.
    krishna_marker = speakers.MARKERS[0][0]
    check("Krishna's marker does not contain the bare verb",
          "उवाच" not in krishna_marker, krishna_marker)
    check("a bare-verb search would lose Krishna",
          not any("उवाच" in (r.sanskrit or "").split("\n")[0]
                  for r in records.values() if r.verse_id == "BG.2.2"))
    check("BG.2.2 is Krishna despite that", records["BG.2.2"].speaker == "Krishna",
          records["BG.2.2"].speaker)

    print("\n3. known boundaries")
    for vid, who in (("BG.1.1", "Dhritarashtra"), ("BG.1.2", "Sanjaya"),
                     ("BG.1.21", "Arjuna"), ("BG.1.24", "Sanjaya"),
                     # 1.28 is the narrator introducing the speech; 1.29 is
                     # Arjuna, with no marker of his own.
                     ("BG.1.28", "Sanjaya"), ("BG.1.29", "Arjuna"),
                     ("BG.1.46", "Arjuna"), ("BG.1.47", "Sanjaya"),
                     ("BG.2.2", "Krishna"), ("BG.2.47", "Krishna"),
                     # The Gita ends in Sanjaya's voice, not Krishna's.
                     ("BG.18.78", "Sanjaya")):
        check("%s is %s" % (vid, who), records[vid].speaker == who,
              records[vid].speaker)

    print("\n4. the unmarked stretch does not leak")
    check("the exception covers exactly 1.29-1.46",
          len(speakers.UNMARKED) == 18, len(speakers.UNMARKED))
    check("it does not change the marker chain after it",
          records["BG.2.1"].speaker == "Sanjaya", records["BG.2.1"].speaker)

    print("\n5. the model is told who is speaking")
    block = C.render_verse(records["BG.1.29"], include_commentary=False)
    check("the context block names the speaker", "spoken by: Arjuna" in block,
          block[:160])
    check("and says what that makes the words",
          "not the answer" in block, block[:240])
    krishna_block = C.render_verse(records["BG.2.47"], include_commentary=False)
    check("Krishna's verses are marked as the teaching",
          "spoken by: Krishna -- the teaching itself" in krishna_block,
          krishna_block[:160])

    print("\n6. speaker is NOT in the retrieval index")
    # Every verse would otherwise contribute "krishna" to 82% of the corpus:
    # pure noise for BM25, and it pulls every embedding toward one point.
    #
    # Asserted with a sentinel rather than by looking for the speaker's name.
    # The names occur legitimately in the translations and in the enrichment
    # summaries ("Arjuna is overwhelmed by..."), so a name search proves
    # nothing -- only a value that exists nowhere else can show whether the
    # FIELD is being concatenated.
    rec = records["BG.1.29"]
    original = rec.speaker
    rec.speaker = "ZZSENTINELZZ"
    try:
        check("searchable_text omits the speaker field",
              "ZZSENTINELZZ" not in corpus.searchable_text(rec))
        check("dense_text omits the speaker field",
              "ZZSENTINELZZ" not in corpus.dense_text(rec))
    finally:
        rec.speaker = original
    check("speaker is not an indexed field",
          "speaker" not in corpus.INDEXED_FIELDS, corpus.INDEXED_FIELDS)

    print()
    if failures:
        print("%d FAILURE(S)" % failures)
        return 1
    print("All speaker tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
