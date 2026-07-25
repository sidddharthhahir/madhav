"""Validate the eval set against the corpus before trusting any number from it.

An eval set that references a verse the corpus does not hold produces a
permanent, unfixable miss and quietly caps the score below 100%. Checking that
is cheap; discovering it after an enrichment run is not.

    python scripts/validate_eval.py
"""

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gita import canon, db  # noqa: E402

failures = 0


def check(label, ok, detail=""):
    global failures
    if not ok:
        failures += 1
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", label,
                           "" if ok else "  <- " + str(detail)[:220]))


def main() -> int:
    cases = json.loads((ROOT / "eval" / "questions.json").read_text(encoding="utf-8"))
    conn = db.connect()
    known = {r[0] for r in conn.execute("SELECT verse_id FROM verses")}

    print("Eval set: %d questions" % len(cases))

    # 1. Structure
    bad_shape = [i for i, c in enumerate(cases)
                 if not c.get("question") or not isinstance(c.get("expected"), list)
                 or not c["expected"]]
    check("every case has a question and non-empty expected list",
          not bad_shape, bad_shape)

    # 2. Every expected verse exists
    all_expected = [v for c in cases for v in c["expected"]]
    missing = sorted({v for v in all_expected if v not in known})
    check("every expected verse exists in the corpus", not missing, missing)

    # 3. Verse ids are well formed and inside the recension
    malformed = []
    for v in set(all_expected):
        try:
            ch, ve = canon.parse_verse_id(v)
            if not (1 <= ch <= 18) or not (1 <= ve <= canon.GITA_PRESS[ch]):
                malformed.append(v)
        except Exception:
            malformed.append(v)
    check("all verse ids well formed and within the recension",
          not malformed, malformed)

    # 4. No duplicate questions
    dupes = [q for q, n in Counter(c["question"].strip().lower()
                                   for c in cases).items() if n > 1]
    check("no duplicate questions", not dupes, dupes)

    # 5. No expected verse repeated inside one case
    inner = [c["question"][:40] for c in cases
             if len(c["expected"]) != len(set(c["expected"]))]
    check("no repeated verse within a single case", not inner, inner)

    print("\nCoverage")
    print("  questions              : %d" % len(cases))
    print("  distinct verses cited  : %d" % len(set(all_expected)))
    print("  expected-verse slots   : %d" % len(all_expected))
    print("  avg verses per question: %.1f" % (len(all_expected) / len(cases)))

    by_ch = Counter(canon.parse_verse_id(v)[0] for v in set(all_expected))
    print("\n  chapters represented   : %d / 18" % len(by_ch))
    thin = [ch for ch in canon.CHAPTERS if by_ch.get(ch, 0) == 0]
    if thin:
        print("  chapters never tested  : %s" % thin)

    themes = Counter(c.get("theme", "untagged") for c in cases)
    print("\n  themes (%d):" % len(themes))
    for t, n in themes.most_common():
        print("    %-15s %d" % (t, n))

    # A verse cited by many questions is a weak signal -- it will be retrieved
    # for almost anything and inflate the score.
    over = [(v, n) for v, n in Counter(all_expected).most_common() if n >= 6]
    print()
    check("no verse is the answer to 6+ questions", not over, over)

    print()
    if failures:
        print("%d FAILURE(S) -- fix before using this eval set" % failures)
        return 1
    print("Eval set is valid and safe to measure against.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
