"""Ask the pipeline a question from the command line.

    python scripts/ask.py "why do I resent people I've never met online"
    python scripts/ask.py --preview "..."     # retrieval only, no API calls, free
    python scripts/ask.py --health

--preview is the one to reach for first when an answer looks wrong: it shows
exactly what was retrieved and what the model was allowed to cite, without
spending anything.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gita.pipeline import Pipeline  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Ask the Gita pipeline.")
    ap.add_argument("question", nargs="*")
    ap.add_argument("-k", type=int, default=None, help="verses to ground on")
    ap.add_argument("--preview", action="store_true",
                    help="retrieval + context only; makes no API calls")
    ap.add_argument("--health", action="store_true")
    ap.add_argument("--json", action="store_true", help="emit raw JSON")
    ap.add_argument("--show-context", action="store_true")
    ap.add_argument("--hybrid", action="store_true",
                    help="fuse BM25 with dense (local Ollama) retrieval via RRF")
    ap.add_argument("--db", default=None)
    args = ap.parse_args(argv)

    pipeline = Pipeline(args.db, use_dense=args.hybrid)

    if args.health:
        print(json.dumps(pipeline.health(), indent=2))
        return 0

    question = " ".join(args.question).strip()
    if not question:
        ap.error("provide a question, or use --health")

    if args.preview:
        out = pipeline.preview(question, k=args.k)
        if args.json:
            print(json.dumps(out, indent=2, ensure_ascii=False))
            return 0
        print("question : %s" % out["question"])
        print("citable  : %s" % out["citable"])
        print("context  : ~%d tokens\n" % out["approx_context_tokens"])
        print("%-4s %-10s %8s  %s" % ("#", "verse", "score", "enriched"))
        for hit in out["retrieved"]:
            print("%-4d %-10s %8.3f  %s"
                  % (hit["rank"], hit["verse_id"], hit["score"], hit["enriched"]))
        if args.show_context:
            print("\n--- grounding context ---")
            print(out["context"])
        return 0

    result = pipeline.ask(question, k=args.k)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        return 0 if result.ok else 1

    if not result.ok:
        print("FAILED (%s)" % result.status)
        print("  %s" % result.detail)
        if result.retrieved:
            print("  retrieved: %s"
                  % ", ".join(h["verse_id"] for h in result.retrieved))
        return 1

    print(result.answer)
    print("\n" + "-" * 60)
    print("language  : %s" % result.language)
    print("citations : %s" % ", ".join(result.citations))
    print("grounded  : %s" % ", ".join(h["verse_id"] for h in result.retrieved))
    print("attempts  : %d" % result.attempts)
    print("validation: %s" % result.detail)
    timings = {s.name: s.ms for s in result.timings}
    print("timings   : %s" % ", ".join("%s=%dms" % kv for kv in timings.items()))
    u = result.usage
    if u:
        print("tokens    : in=%d out=%d cache_read=%d"
              % (u.get("input_tokens", 0), u.get("output_tokens", 0),
                 u.get("cache_read_input_tokens", 0)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
