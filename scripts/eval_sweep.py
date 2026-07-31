"""Offline retrieval experiments against realistic (expanded) queries.

The honest eval (`search.py --eval --real`) costs ~$0.55 a run because it
calls understand() for all 106 questions. That is far too expensive to
iterate against, and the free raw-text eval measures a task the product never
performs. So this caches the expansions once and then sweeps retrieval
variants over them for nothing.

    python scripts/eval_sweep.py --cache      # ~$0.55, once
    python scripts/eval_sweep.py --ranks      # where do expected verses sit?
    python scripts/eval_sweep.py --sweep      # compare configurations
    python scripts/eval_sweep.py --rerank     # measure the reranker (COSTS MONEY)

--cache and --rerank are the only modes that spend anything; --ranks and
--sweep replay the cache and are free to run as often as you like.

A note on the target. recall@k rises to 100% at k=701 by returning the whole
corpus, so a number quoted without its k is meaningless, and "get it to
106/106" is satisfied trivially and uselessly. What matters is recall at a k
small enough that the answer stage still gets a focused context. --ranks
exists to show where the ceiling actually is: if an expected verse sits at
rank 300, no reachable k finds it and the problem is the index, not the
cutoff.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gita.retrieval import corpus, dense  # noqa: E402
from gita.retrieval.bm25 import reciprocal_rank_fusion, Hit  # noqa: E402

EVAL_PATH = ROOT / "eval" / "questions.json"
CACHE_PATH = ROOT / "eval" / "plans.cache.json"


def load_cases():
    return json.loads(EVAL_PATH.read_text(encoding="utf-8"))


def build_cache():
    from gita.answer import generate as G
    cases = load_cases()
    out = {}
    for i, case in enumerate(cases, 1):
        plan = G.understand(case["question"])
        out[case["question"]] = {
            "retrieval_query": plan.retrieval_query,
            "search_query": plan.search_query,
            "themes": plan.themes,
        }
        if i % 20 == 0:
            print("  cached %d/%d" % (i, len(cases)))
    CACHE_PATH.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print("wrote %s (%d plans)" % (CACHE_PATH.name, len(out)))


def load_cache():
    if not CACHE_PATH.exists():
        sys.exit("no plan cache; run --cache first (~$0.55)")
    return json.loads(CACHE_PATH.read_text(encoding="utf-8"))


_QVEC: dict[str, list] = {}


def dense_rank(di, query, pool):
    """Dense hits with the query embedding memoised.

    Every sweep configuration re-runs the same 106 queries; embedding each
    one per configuration would dominate the runtime for no new information,
    since the vector only depends on the query text.
    """
    import math
    if query not in _QVEC:
        _QVEC[query] = dense.embed_one(query, di.model)
    qv = _QVEC[query]
    scored = [(vid, dense._cosine(qv, vec)) for vid, vec in di.vectors.items()]
    scored.sort(key=lambda x: -x[1])
    return [Hit(vid, s, i, {}) for i, (vid, s) in enumerate(scored[:pool], 1)]


def fuse(index, di, query, k, *, w_bm25=1.0, w_dense=1.0, pool=60):
    """RRF over a deep pool, optionally weighting the two rankers.

    reciprocal_rank_fusion has no weight parameter, so a weight is applied by
    repeating a ranking -- RRF sums 1/(k+rank) per appearance, so listing a
    ranking twice doubles its contribution. Integer weights only, which is
    all this sweep needs.
    """
    rankings = []
    b = index.search(query, k=pool)
    rankings += [b] * int(w_bm25)
    if di is not None:
        d = dense_rank(di, query, pool)
        rankings += [d] * int(w_dense)
    return reciprocal_rank_fusion(*rankings)[:k]


def fuse_multi(index, di, plan, question, k, *, pool=60,
               themes=True, raw=True, pack=False):
    """Fuse several focused queries instead of one concatenated blob.

    retrieval_query is search_query + every theme joined into one string. That
    hurts both rankers for the same underlying reason: BM25 length-normalises,
    so a long query dilutes the weight of the terms that actually matter, and
    a single embedding of many themes lands at their centroid -- a point that
    may sit near nothing in particular. Querying each facet separately and
    fusing the rankings keeps each one sharp, and RRF only needs a verse to
    rank well for ONE facet to pull it up.
    """
    queries = [plan["search_query"]]
    if raw:
        queries.append(question)
    if themes:
        queries += plan["themes"]
    if pack:
        queries.append(plan["retrieval_query"])

    rankings = []
    for q in queries:
        if not q:
            continue
        rankings.append(index.search(q, k=pool))
        if di is not None:
            rankings.append(dense_rank(di, q, pool))
    return reciprocal_rank_fusion(*rankings)[:k]


def score_multi(index, di, cache, cases, k, **kw):
    full = partial = 0
    misses = []
    for case in cases:
        plan = cache.get(case["question"])
        if not plan:
            continue
        got = {h.doc_id for h in
               fuse_multi(index, di, plan, case["question"], k, **kw)}
        expected = set(case["expected"])
        found = expected & got
        if found == expected:
            full += 1
        elif found:
            partial += 1
        else:
            misses.append(case["question"])
    return full, partial, misses


def score(index, di, cache, cases, k, **kw):
    full = partial = 0
    misses = []
    for case in cases:
        plan = cache.get(case["question"])
        if not plan:
            continue
        got = {h.doc_id for h in fuse(index, di, plan["retrieval_query"], k, **kw)}
        expected = set(case["expected"])
        found = expected & got
        if found == expected:
            full += 1
        elif found:
            partial += 1
        else:
            misses.append(case["question"])
    return full, partial, misses


def cmd_rerank(index, di, cache, cases, records, pool, k, limit):
    """Does reranking a deep pool down to k beat plain retrieval at k?

    THE ONLY THING THAT SETTLES THE QUESTION. retrieval/rerank.py ships
    unmeasured because it needs API credit to run at all; this is the harness
    that turns that hypothesis into a number.

    Two configurations are compared on identical questions:
        baseline   retrieve k directly            (free)
        reranked   retrieve `pool`, model picks k (~$0.006 per question)

    So a full 106-question run costs about $0.65 on Haiku. Use --limit to try
    a slice first -- if the reranker is not clearly ahead at 25 questions it
    is unlikely to pay for itself at 106.

    Reranking can only ever help if the expected verse is INSIDE the pool but
    OUTSIDE k. That set is printed first, and it is the ceiling: no ordering
    of the pool can find a verse the pool does not contain.
    """
    from gita.retrieval import rerank as RR

    live = [c for c in cases if cache.get(c["question"])][:limit]

    reachable = 0
    for case in live:
        plan = cache[case["question"]]
        deep = {h.doc_id for h in fuse(index, di, plan["retrieval_query"], pool)}
        near = {h.doc_id for h in fuse(index, di, plan["retrieval_query"], k)}
        if set(case["expected"]) <= deep and not set(case["expected"]) <= near:
            reachable += 1
    print("questions: %d   pool=%d  k=%d" % (len(live), pool, k))
    print("headroom: %d question(s) have every expected verse inside the pool "
          "but not inside k." % reachable)
    print("          that is the most reranking can possibly win. If it is 0, "
          "stop here.\n")
    if not reachable:
        return

    base_full = rr_full = 0
    changed = failed = 0
    for case in live:
        plan = cache[case["question"]]
        expected = set(case["expected"])
        if expected <= {h.doc_id for h in
                        fuse(index, di, plan["retrieval_query"], k)}:
            base_full += 1
        deep = fuse(index, di, plan["retrieval_query"], pool)
        got, info = RR.rerank(case["question"], records, deep, k=k)
        if not info["used"]:
            failed += 1
            continue
        changed += bool(info.get("reordered"))
        if expected <= {h.doc_id for h in got}:
            rr_full += 1

    print("%-34s %6s" % ("configuration", "full"))
    print("-" * 42)
    print("%-34s %6d" % ("baseline: retrieve k=%d" % k, base_full))
    print("%-34s %6d" % ("reranked: %d -> %d" % (pool, k), rr_full))
    print("\nreranker changed the order on %d of %d; %d call(s) failed."
          % (changed, len(live), failed))
    if failed:
        print("failed calls fall back to retrieval order, so they count as "
              "baseline -- rerun if that number is large.")


def cmd_ranks(index, di, cache, cases):
    """Where does each expected verse actually sit in the fused ranking?"""
    import statistics
    ranks = []
    unreachable = []
    for case in cases:
        plan = cache.get(case["question"])
        if not plan:
            continue
        # pool=701, not the default 60: this asks "where is the verse in the
        # WHOLE ranking", so a shallow pool would report everything past it
        # as unreachable and invent a ceiling that isn't there.
        order = [h.doc_id for h in
                 fuse(index, di, plan["retrieval_query"], 701, pool=701)]
        pos = {v: (order.index(v) + 1 if v in order else None)
               for v in case["expected"]}
        worst = max((p for p in pos.values() if p), default=None)
        if any(p is None for p in pos.values()):
            unreachable.append((case["question"], pos))
        elif worst:
            ranks.append(worst)

    ranks.sort()
    n = len(ranks)
    print("Rank of the WORST-placed expected verse, per question (n=%d)\n" % n)
    for pct in (25, 50, 75, 90, 95, 100):
        idx = min(n - 1, int(n * pct / 100) - 1 if pct < 100 else n - 1)
        print("  p%-3d  rank %d" % (pct, ranks[idx]))
    print("\n  mean %.1f   median %d" % (statistics.mean(ranks), statistics.median(ranks)))
    print("\nrecall@k implied by these ranks (both verses inside k):")
    for k in (8, 12, 16, 20, 30, 50, 100, 200):
        got = sum(1 for r in ranks if r <= k)
        print("  k=%-4d %3d/%d full (%.0f%%)" % (k, got, len(cases), 100 * got / len(cases)))
    if unreachable:
        print("\n%d question(s) have an expected verse outside the ranking entirely:"
              % len(unreachable))
        for q, pos in unreachable[:5]:
            print("  %s -> %s" % (q[:58], pos))


def cmd_sweep(index, di, cache, cases):
    print("%-42s %6s %8s %6s" % ("configuration", "full", "partial", "miss"))
    print("-" * 66)
    configs = []
    for k in (8, 12, 16, 20):
        configs.append(("hybrid 1:1, k=%d" % k, dict(k=k)))
    for k in (12, 20):
        configs.append(("dense-weighted 1:2, k=%d" % k, dict(k=k, w_dense=2)))
        configs.append(("bm25-weighted 2:1, k=%d" % k, dict(k=k, w_bm25=2)))
    configs.append(("bm25 only, k=12", dict(k=12, w_dense=0)))
    configs.append(("dense only, k=12", dict(k=12, w_bm25=0)))
    for label, kw in configs:
        k = kw.pop("k")
        di_use = None if kw.pop("w_dense", 1) == 0 else di
        if kw.get("w_bm25") == 0:
            kw["w_bm25"] = 0
        full, partial, misses = score(index, di_use, cache, cases, k, **kw)
        print("%-42s %6d %8d %6d" % (label, full, partial, len(misses)))

    print()
    multi = [
        ("multi-query (sq+raw+themes), k=8", dict(k=8)),
        ("multi-query (sq+raw+themes), k=12", dict(k=12)),
        ("multi-query (sq+raw+themes), k=16", dict(k=16)),
        ("multi-query (sq+raw+themes), k=20", dict(k=20)),
        ("multi-query, no raw question, k=12", dict(k=12, raw=False)),
        ("multi-query, no themes, k=12", dict(k=12, themes=False)),
        ("multi-query + packed blob, k=12", dict(k=12, pack=True)),
        ("multi-query + packed blob, k=20", dict(k=20, pack=True)),
    ]
    for label, kw in multi:
        k = kw.pop("k")
        full, partial, misses = score_multi(index, di, cache, cases, k, **kw)
        print("%-42s %6d %8d %6d" % (label, full, partial, len(misses)))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache", action="store_true", help="build the plan cache (costs money)")
    ap.add_argument("--ranks", action="store_true")
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--rerank", action="store_true",
                    help="measure the reranker (COSTS MONEY, ~$0.006/question)")
    ap.add_argument("--pool", type=int, default=30, help="candidates to rerank")
    ap.add_argument("--k", type=int, default=12, help="verses kept after reranking")
    ap.add_argument("--limit", type=int, default=106,
                    help="questions to run; use a slice before paying for all 106")
    args = ap.parse_args()

    if args.cache:
        build_cache()
        return 0

    conn, index, records = corpus.open_index()
    vectors = dense.load_embeddings(conn)
    di = dense.DenseIndex(vectors) if vectors else None
    cache, cases = load_cache(), load_cases()

    if args.ranks:
        cmd_ranks(index, di, cache, cases)
    elif args.sweep:
        cmd_sweep(index, di, cache, cases)
    elif args.rerank:
        cmd_rerank(index, di, cache, cases, records,
                   args.pool, args.k, args.limit)
    else:
        ap.error("pick --cache, --ranks, --sweep or --rerank")
    return 0


if __name__ == "__main__":
    sys.exit(main())
