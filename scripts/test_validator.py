"""Exercise the citation validator against the real corpus.

    python scripts/test_validator.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gita import db  # noqa: E402
from gita.answer import validate_module as V  # noqa: E402

# BG.13.35 exists only in the Gita Press recension (chapter 13 = 35 verses).
# BG.2.99 and BG.19.1 exist in no recension.
CASES = [
    ("plain valid citation",
     "Krishna addresses this in [BG 3.37].", {"BG.3.37"}, True),
    ("multiple valid citations",
     "See [BG 3.37], and also [BG 7.27] and [BG 16.18].",
     {"BG.3.37", "BG.7.27", "BG.16.18"}, True),
    ("colon form",
     "As in [BG 2:47].", {"BG.2.47"}, True),
    ("bare form without brackets",
     "This echoes BG 6.5 directly.", {"BG.6.5"}, True),
    ("hallucinated verse number",
     "The answer is in [BG 2.99].", {"BG.2.99"}, False),
    ("hallucinated chapter",
     "See [BG 19.1] for this.", {"BG.19.1"}, False),
    ("recension-boundary verse that does exist here",
     "Consider [BG 13.35].", {"BG.13.35"}, True),
    ("valid verse but not retrieved for this question",
     "Consider [BG 18.66].", {"BG.3.37"}, False),
    ("no citation at all",
     "Hate comes from within, not from the other person.", set(), False),
]


def main() -> int:
    conn = db.connect()
    valid_ids = V.known_verse_ids(conn)
    print("corpus contains %d verse ids\n" % len(valid_ids))

    failures = 0
    for label, answer, context_ids, should_pass in CASES:
        report = V.validate(answer, valid_ids=valid_ids, context_ids=context_ids)
        ok = report.ok == should_pass
        if not ok:
            failures += 1
        print("[%s] %s" % ("PASS" if ok else "FAIL", label))
        print("        %s" % report.summary())

    print("\nsanity checks on the corpus itself")
    for vid, expected in (("BG.13.35", True), ("BG.13.36", False),
                          ("BG.18.78", True), ("BG.18.79", False),
                          ("BG.1.47", True), ("BG.1.48", False)):
        present = vid in valid_ids
        ok = present == expected
        if not ok:
            failures += 1
        print("  [%s] %-10s present=%s expected=%s"
              % ("PASS" if ok else "FAIL", vid, present, expected))

    print()
    if failures:
        print("%d FAILURE(S)" % failures)
        return 1
    print("All validator checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
