"""Query the retrieval index from the command line.

    python scripts/search.py "why do people hate strangers online"
    python scripts/search.py --health
    python scripts/search.py --explain BG.3.37 "desire turns into anger"
    python scripts/search.py --eval

Prints verse ids, scores and matched terms rather than verse text -- the point
is to inspect what retrieval selected, and dumping translations makes that
harder to read, not easier.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gita.retrieval import corpus, normalize  # noqa: E402
from gita.retrieval.bm25 import reciprocal_rank_fusion  # noqa: E402

EVAL_PATH = ROOT / "eval" / "questions.json"


def _hybrid_search(index, dense_index, query: str, k: int):
    bm25_hits = index.search(query, k=k)
    if dense_index is None:
        return bm25_hits
    dense_hits = dense_index.search(query, k=k)
    return reciprocal_rank_fusion(bm25_hits, dense_hits)[:k]


def cmd_health(index, records) -> int:
    health = corpus.index_health(records)
    print("Index health")
    for key, value in health.items():
        print("  %-20s %s" % (key, value))
    print("  %-20s %d" % ("bm25 documents", len(index)))
    print("  %-20s %d" % ("vocabulary", index.vocabulary_size))
    if health["mode"] == "fallback":
        print("\n  NOTE: retrieving over raw translations only. Life-situation "
              "questions\n        will underperform until the enrichment layer is built.")
    return 0


def cmd_search(index, dense_index, query: str, k: int) -> int:
    print("query      : %s" % query)
    print("normalised : %s" % " ".join(normalize.tokenize(query)))
    hits = _hybrid_search(index, dense_index, query, k)
    if not hits:
        print("\nno matches (every query term is out of vocabulary)")
        return 0
    print("\n%-4s %-10s %8s  %s" % ("#", "verse", "score", "matched terms"))
    for hit in hits:
        terms = index.explain(query, hit.doc_id)[:5]
        rendered = ", ".join("%s=%.2f" % (t, s) for t, s in terms)
        flag = "" if hit.meta.get("enriched") else " *"
        print("%-4d %-10s %8.3f  %s%s" % (hit.rank, hit.doc_id, hit.score, rendered, flag))
    if any(not h.meta.get("enriched") for h in hits):
        print("\n* scored from translation/commentary text only (not yet enriched)")
    return 0


def cmd_explain(index, doc_id: str, query: str) -> int:
    print("explain %s for: %s" % (doc_id, query))
    contributions = index.explain(query, doc_id)
    if not contributions:
        print("  no query term occurs in this document")
        return 0
    total = sum(s for _, s in contributions)
    for term, score in contributions:
        print("  %-18s %7.3f  (%4.1f%%)" % (term, score, 100 * score / total))
    print("  %-18s %7.3f" % ("TOTAL", total))
    return 0


def cmd_eval(index, dense_index, k: int, *, real: bool = False) -> int:
    """Recall@k against the hand-labelled question set.

    Retrieval in the actual /ask pipeline never runs on the raw question --
    Pipeline.ask() always calls answer.generate.understand() first, which
    rewrites it toward corpus vocabulary (a "search_query" plus explicit
    "themes") before retrieval happens. Measuring recall on the raw question
    text, as this function did before `real` existed, is a different and
    meaningfully harder task than what the product actually does: on this eval
    set, real query understanding took full recall from 17/106 to 40/106 (that
    comparison run is what surfaced the gap in the first place). Without
    --real this is still useful for isolating retrieval quality itself from
    the understanding stage, but the number it prints is not the product's
    real recall, and reporting it as if it were is how this was mismeasured
    for one iteration of this project.
    """
    if not EVAL_PATH.exists():
        print("no eval set at %s" % EVAL_PATH)
        return 1
    if real:
        from gita.answer import generate as G
        try:
            G._client()
        except Exception as exc:
            print("--real needs a working Anthropic client: %s" % exc)
            return 1
    cases = json.loads(EVAL_PATH.read_text(encoding="utf-8"))
    hit_count = 0
    partial = 0
    mode = "hybrid (BM25 + dense, RRF)" if dense_index is not None else "BM25 only"
    if real:
        mode += ", through real query understanding (costs money)"
    print("Recall@%d over %d questions -- %s\n" % (k, len(cases), mode))
    for case in cases:
        expected = set(case["expected"])
        query = case["question"]
        if real:
            from gita.answer import generate as G
            plan = G.understand(query)
            query = plan.retrieval_query
        got = {h.doc_id for h in _hybrid_search(index, dense_index, query, k)}
        found = expected & got
        if found == expected:
            status, hit_count = "FULL", hit_count + 1
        elif found:
            status, partial = "PART", partial + 1
        else:
            status = "MISS"
        print("  [%s] %s" % (status, case["question"][:66]))
        if found != expected:
            print("         expected %s  found %s"
                  % (sorted(expected), sorted(found) or "none"))
    n = len(cases)
    print("\n  full %d/%d (%.0f%%)   partial %d   miss %d"
          % (hit_count, n, 100 * hit_count / n, partial, n - hit_count - partial))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Query the Gita retrieval index.")
    ap.add_argument("query", nargs="*", help="the question to search for")
    ap.add_argument("-k", type=int, default=8, help="how many hits to return")
    ap.add_argument("--health", action="store_true", help="report index coverage")
    ap.add_argument("--eval", action="store_true", help="run the eval set")
    ap.add_argument("--real", action="store_true",
                     help="with --eval: route each question through real query "
                          "understanding first, like the actual /ask pipeline "
                          "does. Costs real money (~$0.55 for the full 106-"
                          "question set at time of writing) -- the plain --eval "
                          "number measures retrieval in isolation and is free, "
                          "but is not the product's actual recall.")
    ap.add_argument("--explain", metavar="VERSE_ID",
                    help="break down the score for one verse")
    ap.add_argument("--hybrid", action="store_true",
                     help="fuse BM25 with dense (local Ollama) retrieval via RRF")
    ap.add_argument("--db", default=None)
    args = ap.parse_args(argv)

    conn, index, records = corpus.open_index(args.db)

    dense_index = None
    if args.hybrid:
        from gita.retrieval import dense as dense_mod
        vectors = dense_mod.load_embeddings(conn)
        if not vectors:
            ap.error("no embeddings cached -- run scripts/build_embeddings.py first")
        meta = {vid: {"chapter": r.chapter, "verse": r.verse} for vid, r in records.items()}
        dense_index = dense_mod.DenseIndex(vectors, meta)

    if args.health:
        return cmd_health(index, records)
    if args.eval:
        return cmd_eval(index, dense_index, args.k, real=args.real)

    query = " ".join(args.query).strip()
    if not query:
        ap.error("provide a query, or use --health / --eval")
    if args.explain:
        return cmd_explain(index, args.explain, query)
    return cmd_search(index, dense_index, query, args.k)


if __name__ == "__main__":
    sys.exit(main())
