"""Model-based reranking of retrieval candidates.

WHY THIS EXISTS. corpus.py records, with measurements, that the `stance` field
cannot be indexed usefully: BM25 is bag-of-words and a pooled embedding
averages its tokens, so neither can represent "not X" -- a stance line written
to REPEL a query ("a warning to the arrogant, not comfort for the humble")
instead attracts it. The conclusion there was that stance is "the right input
for a reranker", because a model reading the sentence can act on the negation
that no index can. This is that reranker.

COST AND WHY IT IS OFF BY DEFAULT. This is the only component in the retrieval
path that spends money -- roughly $0.006 per query on Haiku for 30 candidates
(~5k input tokens, ~200 out). That is small, but it is not zero, and every
other retrieval surface in this project is free and stays free. So it is
opt-in: `MADHAV_RERANK=1` or `Pipeline(use_rerank=True)`.

NOT YET MEASURED. Every previous "this should obviously help" change in this
project was tested before being believed, and two of them (multi-query fusion,
indexing stance) turned out to be worth nothing. This one has NOT been run
against the 106-question eval set, because doing so requires API credit that
was not available when it was written. Treat its benefit as a hypothesis, not
a result. `scripts/eval_sweep.py --rerank` is the harness to settle it.

WHAT *IS* MEASURED is the ceiling, and it cost nothing to establish. Reranking
can only win a question whose expected verses sit inside the candidate pool
but outside k -- no reordering can surface a verse the pool never contained.
Over the 106 cached eval questions:

    pool=30 -> k=12    20 questions winnable
    pool=30 -> k=20    10 questions winnable
    pool=60 -> k=20    29 questions winnable

So the headroom is real rather than imagined, which is why this was worth
building. It is still an upper bound assuming perfect judgement, and the
honest expectation is some fraction of it. Note the shape: widening the pool
buys more than tightening k does, and roughly doubles the per-query cost.

FAILS OPEN, ALWAYS. Any error -- no credential, rate limit, malformed output,
hallucinated verse id -- returns the original ranking unchanged. A reranker is
an optimisation on top of a ranking that already works; it must never be able
to turn a working answer into a failed one.
"""

import json

from ..answer import generate as G

MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 900

# Cap on what is described to the model. The summary is the verse's gist and
# the stance is the part an index cannot use, so both go; nothing else does.
# Sending translations here would multiply the cost of the cheap stage for
# information the expensive stage already receives.
SUMMARY_CHARS = 240
STANCE_LINES = 3

SYSTEM = """\
You rank Bhagavad Gita verses by how well each one actually answers a specific \
person's question.

You are given a question and a numbered list of candidate verses. Each \
candidate has a summary of what it says, and a "stance" describing WHO it is \
addressed to and who it is NOT addressed to.

The stance is the reason you exist. A keyword index cannot tell "I feel \
worthless next to everyone" from "I think I am better than everyone" -- both \
are about comparison and status, and both retrieve the same verses. You can. A \
verse whose stance says it is "a warning to the arrogant, not comfort for the \
humble" is a BAD match for someone who feels worthless, however well its \
vocabulary matches. Demote it.

Rank by fit to the asker's actual situation, in this order:
1. Does the stance point AT this person, or away from them?
2. Does the verse speak to what they are living through, not merely to the \
topic they named?
3. Does it offer something they can act on?

Return the candidate numbers in your new best-to-worst order. Include every \
candidate number exactly once. Return nothing else."""

SCHEMA = {
    "type": "object",
    "properties": {
        "order": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "candidate numbers, best first, each exactly once",
        }
    },
    "required": ["order"],
    "additionalProperties": False,
}


def _describe(records, hits) -> str:
    lines = []
    for i, hit in enumerate(hits, 1):
        rec = records.get(hit.doc_id)
        enr = (rec.enrichment if rec else None) or {}
        summary = (enr.get("summary") or "").strip()[:SUMMARY_CHARS]
        stance = enr.get("stance") or []
        lines.append("[%d] %s\nsummary: %s" % (i, hit.doc_id, summary or "(none)"))
        for s in stance[:STANCE_LINES]:
            lines.append("stance: %s" % s)
        lines.append("")
    return "\n".join(lines)


def rerank(question: str, records, hits, *, k=None, client=None, model=MODEL):
    """Reorder `hits` by model judgement. Returns (hits, info).

    `info` always reports what happened -- `used` is False whenever the
    original order survived, with `reason` saying why, so a silently
    ineffective reranker cannot look like a working one.
    """
    k = k or len(hits)
    if len(hits) < 2:
        return hits[:k], {"used": False, "reason": "nothing_to_rank"}

    try:
        message = G._create(
            G._client(client),
            model=model,
            max_tokens=MAX_TOKENS,
            system=[{"type": "text", "text": SYSTEM,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content":
                       "Question: %s\n\nCandidates:\n%s" % (question, _describe(records, hits))}],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        )
        if message.stop_reason == "refusal":
            return hits[:k], {"used": False, "reason": "refused"}
        order = json.loads(G._text_of(message))["order"]
    except Exception as exc:                                  # noqa: BLE001
        # Deliberately broad. Everything from a missing key to a network blip
        # to malformed JSON lands here, and the correct response to all of it
        # is the same: keep the ranking we already had.
        return hits[:k], {"used": False, "reason": type(exc).__name__,
                          "detail": str(exc)[:200]}

    # Trust nothing about the returned order. Take each valid, in-range,
    # not-yet-seen index in the order given, then append anything the model
    # dropped in its original relative position -- so a truncated or partial
    # response degrades to "partially reranked" instead of "verses missing".
    seen: set[int] = set()
    out = []
    for n in order:
        if isinstance(n, int) and 1 <= n <= len(hits) and n not in seen:
            seen.add(n)
            out.append(hits[n - 1])
    dropped = [h for i, h in enumerate(hits, 1) if i not in seen]
    out.extend(dropped)

    usage = G._usage_of(message)
    return out[:k], {
        "used": True,
        "reason": "",
        "candidates": len(hits),
        "reordered": sum(1 for a, b in zip(hits, out) if a.doc_id != b.doc_id),
        "dropped_by_model": len(dropped),
        "usage": usage,
        "model": model,
    }
