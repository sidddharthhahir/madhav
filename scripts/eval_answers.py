"""Regression checks on generated answers.

The retrieval eval is rigorous; the thing people actually read was never
measured at all. Two answer-prompt changes shipped in one session -- a length
cut, then a demand for a concrete practice -- each validated by reading a
single output. A regression in length, citation density, or structure would
have been invisible.

    python scripts/eval_answers.py --n 8          # ~25c, real API calls
    python scripts/eval_answers.py --n 8 --json out.json
    python scripts/eval_answers.py --compare a.json b.json   # free

This does NOT score whether an answer is wise or true -- that is a judgement
call and pretending a script can make it would be worse than not measuring.
It checks objective properties the prompt explicitly asks for, so a change
that breaks one shows up as a number instead of a vibe:

  * length inside the stated 120-200 word band
  * at least two citations, all validated (the pipeline guarantees this, so a
    failure here means the guarantee itself broke)
  * two or three paragraphs, not a wall
  * a second paragraph exists (where the prompt asks for the concrete practice)
  * answered in the requested language
  * no headers or bullet lists, which the prompt forbids
  * first attempt accepted, i.e. the model is not routinely being retried

Costs real money: one question is two model calls. Keep --n small and use
--compare, which is free, to diff two saved runs.
"""

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# A fixed, deliberately varied sample: concrete-situation questions, abstract
# ones, and one that should be refused as off-topic. Fixed so two runs are
# comparable; varied so a prompt that only suits one kind of question shows up.
PROBE = [
    "why do I get angry at people I love",
    "I keep putting things off even when they matter",
    "how do I know if I am on the right path",
    "does anything I do actually matter",
    "I am afraid of losing the people I love",
    "how do I work hard without burning out",
    "why do I feel empty after getting what I wanted",
    "what is the weather in Ahmedabad",           # expected: off_topic
]

WORD_MIN, WORD_MAX = 120, 200


def measure(result) -> dict:
    text = result.answer or ""
    words = len(text.split())
    paras = [p for p in re.split(r"\n{2,}", text.strip()) if p.strip()]
    return {
        "question": result.question,
        "ok": result.ok,
        "status": result.status,
        "words": words,
        "in_band": WORD_MIN <= words <= WORD_MAX,
        "paragraphs": len(paras),
        "citations": len(result.citations),
        "attempts": result.attempts,
        "language": result.language,
        "has_second_para": len(paras) >= 2,
        "has_markup": bool(re.search(r"^\s*(#|[-*]\s|\d+\.\s)", text, re.M)),
    }


def run(n: int) -> list[dict]:
    from gita.pipeline import Pipeline
    p = Pipeline(use_dense=True)
    rows = []
    try:
        for q in PROBE[:n]:
            r = p.ask(q)
            rows.append(measure(r))
            print("  %-52s %s" % (q[:52], r.status))
            # This suite runs against the real store; do not leave probes in
            # the person's history.
            p.local.execute("DELETE FROM history WHERE question = ?", (q,))
            p.local.commit()
    finally:
        p.close()
    return rows


def report(rows: list[dict]) -> int:
    answered = [r for r in rows if r["ok"]]
    offtopic = [r for r in rows if r["status"] == "off_topic"]
    failures = 0

    def check(label, cond, detail=""):
        nonlocal failures
        if not cond:
            failures += 1
        print("  [%s] %s%s" % ("PASS" if cond else "FAIL", label,
                               "" if cond else "  <- " + str(detail)))

    print("\n%d answered, %d off-topic, %d other\n"
          % (len(answered), len(offtopic), len(rows) - len(answered) - len(offtopic)))

    if not answered:
        print("  no answers generated; nothing to measure")
        return 1

    words = [r["words"] for r in answered]
    print("  words   min %d  median %d  max %d"
          % (min(words), statistics.median(words), max(words)))
    print("  cites   %s" % [r["citations"] for r in answered])
    print("  paras   %s" % [r["paragraphs"] for r in answered])
    print()

    in_band = sum(r["in_band"] for r in answered)
    check("most answers inside %d-%d words (%d/%d)" % (WORD_MIN, WORD_MAX, in_band, len(answered)),
          in_band >= len(answered) * 0.6, [r["words"] for r in answered])
    check("every answer cites at least twice",
          all(r["citations"] >= 2 for r in answered),
          [r["citations"] for r in answered])
    check("no answer exceeds 3 paragraphs",
          all(r["paragraphs"] <= 3 for r in answered),
          [r["paragraphs"] for r in answered])
    check("every answer has a second paragraph (the practice)",
          all(r["has_second_para"] for r in answered))
    check("no headers or bullet lists",
          not any(r["has_markup"] for r in answered))
    check("all answered in English",
          all(r["language"] == "en" for r in answered))
    check("accepted on the first attempt",
          all(r["attempts"] == 1 for r in answered),
          [r["attempts"] for r in answered])
    if any(r["question"].startswith("what is the weather") for r in rows):
        check("off-topic question was refused",
              any(r["status"] == "off_topic" for r in rows),
              [r["status"] for r in rows])

    print()
    if failures:
        print("%d FAILURE(S)" % failures)
        return 1
    print("All answer checks passed.")
    return 0


def compare(a_path: str, b_path: str) -> int:
    a = json.loads(Path(a_path).read_text())
    b = json.loads(Path(b_path).read_text())
    by_q = {r["question"]: r for r in a}
    print("%-44s %14s %14s" % ("question", Path(a_path).stem, Path(b_path).stem))
    print("-" * 74)
    for rb in b:
        ra = by_q.get(rb["question"])
        if not ra:
            continue
        print("%-44s %6dw %3dc %6dw %3dc%s"
              % (rb["question"][:44], ra["words"], ra["citations"],
                 rb["words"], rb["citations"],
                 "   <-- shorter" if rb["words"] < ra["words"] * 0.8 else
                 "   <-- longer" if rb["words"] > ra["words"] * 1.25 else ""))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=len(PROBE))
    ap.add_argument("--json", help="write raw measurements here")
    ap.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"))
    args = ap.parse_args()

    if args.compare:
        return compare(*args.compare)

    print("Asking %d probe questions (real API calls)...\n" % min(args.n, len(PROBE)))
    rows = run(args.n)
    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=1, ensure_ascii=False))
        print("\nwrote %s" % args.json)
    return report(rows)


if __name__ == "__main__":
    sys.exit(main())
