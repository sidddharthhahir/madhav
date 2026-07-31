"""Reranker and counterpoint tests. No credential, no spending.

The reranker cannot be *evaluated* without API credit -- whether it actually
retrieves better verses is an open question that scripts/eval_sweep.py exists
to settle. What CAN be established offline is everything that must hold
regardless of how good its judgement is:

  - a malformed, partial or hallucinated ordering never loses a verse
  - any failure at all returns the original ranking (fails open)
  - the answer path still works with reranking enabled and the call failing

That distinction matters. These tests prove the reranker is SAFE, not that it
is USEFUL. Nothing here should be read as evidence that it improves recall.

    python scripts/test_rerank.py
"""

import json
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gita.pipeline import Pipeline as _Pipeline  # noqa: E402
from gita.retrieval import counterpoint as CP  # noqa: E402
from gita.retrieval import rerank as RR  # noqa: E402

_TMPDIR = tempfile.mkdtemp(prefix="madhav-rerank-test-")


def Pipeline(*a, **kw):
    kw.setdefault("local_db_path", Path(_TMPDIR) / "local.sqlite3")
    return _Pipeline(*a, **kw)


@dataclass
class _Block:
    type: str
    text: str


@dataclass
class _Usage:
    input_tokens: int = 5000
    output_tokens: int = 200
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class _Message:
    content: list
    usage: _Usage
    stop_reason: str = "end_turn"
    stop_details: object = None


class RerankStub:
    """Returns a fixed `order` payload, or raises, on every create()."""

    def __init__(self, order=None, raises=None, stop_reason="end_turn"):
        self.order = order
        self.raises = raises
        self.stop_reason = stop_reason
        self.calls = 0

    @property
    def messages(self):
        return self

    def create(self, **kwargs):
        self.calls += 1
        if self.raises:
            raise self.raises
        return _Message([_Block("text", json.dumps({"order": self.order}))],
                        _Usage(), stop_reason=self.stop_reason)


failures = 0


def check(label, ok, detail=""):
    global failures
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", label,
                           "" if ok else "  <- " + detail))
    if not ok:
        failures += 1


def main() -> int:
    pipe = Pipeline(use_dense=False)
    hits = pipe.retrieve("anger and desire", 8)
    ids = [h.doc_id for h in hits]
    print("Candidates:", len(ids))

    print("\n1. a well-formed reversal is applied")
    stub = RerankStub(order=list(range(len(hits), 0, -1)))
    out, info = RR.rerank("q", pipe.records, hits, client=stub)
    check("reranker reports it ran", info["used"], str(info))
    check("order is reversed", [h.doc_id for h in out] == ids[::-1],
          str([h.doc_id for h in out]))
    check("no verse lost", len(out) == len(hits), "%d" % len(out))

    print("\n2. k truncates AFTER reranking, not before")
    stub = RerankStub(order=list(range(len(hits), 0, -1)))
    out, _ = RR.rerank("q", pipe.records, hits, k=3, client=stub)
    check("returns k", len(out) == 3, "%d" % len(out))
    check("returns the model's top 3, not retrieval's",
          [h.doc_id for h in out] == ids[::-1][:3], str([h.doc_id for h in out]))

    print("\n3. a partial ordering keeps the dropped verses")
    # The model returns only the first two. The rest must survive, in their
    # original relative order, rather than vanishing from the grounding set.
    stub = RerankStub(order=[3, 1])
    out, info = RR.rerank("q", pipe.records, hits, client=stub)
    check("every verse still present",
          sorted(h.doc_id for h in out) == sorted(ids), str(len(out)))
    check("model's picks lead", [h.doc_id for h in out][:2] == [ids[2], ids[0]],
          str([h.doc_id for h in out][:2]))
    check("dropped count reported", info["dropped_by_model"] == len(hits) - 2,
          str(info["dropped_by_model"]))

    print("\n4. hostile orderings cannot corrupt the grounding set")
    for label, order in (
        ("out-of-range indices", [99, 1, -4, 0]),
        ("duplicates", [1, 1, 1, 1]),
        ("wrong types", ["2", None, 3.5, 2]),
        ("empty", []),
    ):
        stub = RerankStub(order=order)
        out, _ = RR.rerank("q", pipe.records, hits, client=stub)
        check("%s: all verses present exactly once" % label,
              sorted(h.doc_id for h in out) == sorted(ids),
              str([h.doc_id for h in out]))

    print("\n5. every failure mode falls back to the original order")
    for label, stub in (
        ("network error", RerankStub(raises=RuntimeError("connection reset"))),
        ("no credential", RerankStub(raises=TypeError("authentication method"))),
        ("refusal", RerankStub(order=[1], stop_reason="refusal")),
    ):
        out, info = RR.rerank("q", pipe.records, hits, client=stub)
        check("%s: reports it did not run" % label, not info["used"], str(info))
        check("%s: original order intact" % label,
              [h.doc_id for h in out] == ids, str([h.doc_id for h in out]))

    print("\n6. fewer than two candidates is a no-op, and costs nothing")
    stub = RerankStub(order=[1])
    out, info = RR.rerank("q", pipe.records, hits[:1], client=stub)
    check("no model call made", stub.calls == 0, "%d calls" % stub.calls)
    check("reason given", info["reason"] == "nothing_to_rank", str(info))

    print("\n7. reranking is off unless asked for")
    plain = Pipeline(use_dense=False)
    check("pipeline default is off", plain.use_rerank is False,
          str(plain.use_rerank))
    _, info = plain._ground("q", _plan("anger"), 5)
    check("_ground reports disabled", info == {"used": False, "reason": "disabled"},
          str(info))
    plain.close()

    print("\n8. with reranking on but the call failing, grounding still works")
    broken = Pipeline(use_dense=False, use_rerank=True,
                      client=RerankStub(raises=RuntimeError("no credit")))
    got, info = broken._ground("q", _plan("anger"), 5)
    check("still returns k verses", len(got) == 5, "%d" % len(got))
    check("failure is reported, not hidden", not info["used"], str(info))
    broken.close()

    print("\n9. counterpoint extracts the negated side of a stance")
    clauses = CP.opposing_clauses([
        "a warning to someone corrupted by power, not comfort for the powerless",
        "addressed to someone actively harming others, not someone being harmed",
        "addressed to someone about to act on their anger",
    ])
    check("one clause per contrastive line", len(clauses) == 2, str(clauses))
    check("takes the right-hand side", clauses[0] == "comfort for the powerless",
          str(clauses))
    check("non-contrastive lines contribute nothing",
          all("about to act" not in c for c in clauses), str(clauses))

    print("\n10. counterpoint returns verses that were not already shown")
    hits20 = pipe.retrieve("i feel worthless next to everyone", 20)
    shown = [h.doc_id for h in hits20]
    res = pipe.counterpoint(shown, k=5)
    check("produced verses", res["ok"] and res["verses"], str(res["reason"]))
    check("none repeat the grounding set",
          not ({v["verse_id"] for v in res["verses"]} & set(shown)),
          str([v["verse_id"] for v in res["verses"]]))
    check("the derived query is disclosed", bool(res["query"]), str(res)[:120])
    check("stance travels with each verse",
          all("stance" in v for v in res["verses"]), "")

    print("\n11. counterpoint reports honestly when it has nothing to work from")
    res = pipe.counterpoint(["BG.999.1"], k=5)
    check("no fabricated result", not res["ok"], str(res))
    check("reason is specific", res["reason"] == "no_contrastive_stance",
          res["reason"])

    pipe.close()
    shutil.rmtree(_TMPDIR, ignore_errors=True)

    print()
    if failures:
        print("%d FAILURE(S)" % failures)
        return 1
    print("All rerank/counterpoint tests passed.")
    return 0


def _plan(query):
    from gita.answer.generate import QueryPlan
    return QueryPlan(language="en", search_query=query, themes=[],
                     on_topic=True, restated=query)


if __name__ == "__main__":
    sys.exit(main())
