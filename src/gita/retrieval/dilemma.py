"""Dharma-sankata -- holding both sides of an impossible choice.

The Mahabharata's subject is not war, it is choices with no clean answer.
Yudhishthira's half-truth that kills Drona. Bhishma's vow that binds him to
the wrong side. Karna's loyalty to a man he knows is wrong. Arjuna, asked to
kill his own teachers, putting down his bow.

The app already answers questions. This makes it hold a tension instead: two
options in, verses for each, and -- the part that matters -- the verses that
apply WHICHEVER you choose. Krishna never tells Arjuna which way to go. He
changes what the choice means, and then says "do as you will" (BG 18.63).

FREE. Two local retrievals, no model call. Same cost basis as counterpoint.py.

MEASURED, and this is what makes the feature honest rather than decorative:
the two sides really do retrieve differently. Over five realistic dilemmas at
k=10 the Jaccard overlap was 0.00-0.05 -- essentially disjoint. So a split
screen is showing genuinely different counsel on each side, not the same list
twice with different headings.

The corollary is that shared ground is SCARCE and has to be dug for. At k=10
the sides shared 0-1 verses; the overlap only becomes usable deeper in the
ranking (3 at k=20, 5 at k=30, 13 at k=50). So both sides are retrieved to a
deep pool and the intersection is mined from it, rather than intersecting the
handful of verses each side actually displays -- which would almost always be
empty and would make the best panel look broken.
"""

from .bm25 import reciprocal_rank_fusion

# Deep enough that the intersection is populated (measured above), shallow
# enough that the tail is still on-topic. Only the top few of each bucket are
# ever shown; this depth exists to find shared ground, not to display it.
POOL = 50


def _norm(text: str) -> str:
    return " ".join((text or "").split())


def dilemma(records, retrieve, option_a: str, option_b: str, *, k: int = 5,
            shared_k: int = 3) -> dict:
    """Retrieve for both sides of a choice, plus the counsel common to both.

    `retrieve` is the pipeline's own retrieval callable, so this inherits BM25
    + dense fusion and anything that later improves it.
    """
    option_a, option_b = _norm(option_a), _norm(option_b)
    if not option_a or not option_b:
        return {"ok": False, "reason": "both_options_required",
                "a": {}, "b": {}, "shared": []}
    if option_a.lower() == option_b.lower():
        return {"ok": False, "reason": "options_identical",
                "a": {}, "b": {}, "shared": []}

    hits_a = retrieve(option_a, POOL)
    hits_b = retrieve(option_b, POOL)
    rank_a = {h.doc_id: i for i, h in enumerate(hits_a)}
    rank_b = {h.doc_id: i for i, h in enumerate(hits_b)}
    both = set(rank_a) & set(rank_b)

    def pack(hit, side):
        rec = records.get(hit.doc_id)
        enr = (rec.enrichment or {}) if rec else {}
        return {
            "verse_id": hit.doc_id,
            "score": round(hit.score, 3),
            "speaker": rec.speaker if rec else None,
            "summary": enr.get("summary", ""),
            # Stance is what stops a verse being read as endorsement of the
            # option it was retrieved for. It says who the verse is actually
            # addressed to, which is exactly the question someone weighing a
            # choice should be asking.
            "stance": enr.get("stance", []),
            "side": side,
        }

    only_a = [pack(h, "a") for h in hits_a if h.doc_id not in both][:k]
    only_b = [pack(h, "b") for h in hits_b if h.doc_id not in both][:k]

    # Shared verses are ranked by agreement across the two sides rather than
    # by either side's score alone -- RRF over the two rankings, which is the
    # same fusion the retriever already uses and rewards a verse that both
    # sides rate highly over one that side A loves and side B barely returned.
    shared = sorted(both, key=lambda v: 1 / (60 + rank_a[v]) + 1 / (60 + rank_b[v]),
                    reverse=True)[:shared_k]
    shared_out = []
    for vid in shared:
        hit = hits_a[rank_a[vid]]
        item = pack(hit, "both")
        item["rank_a"] = rank_a[vid] + 1
        item["rank_b"] = rank_b[vid] + 1
        shared_out.append(item)

    return {
        "ok": bool(only_a or only_b),
        "reason": "" if (only_a or only_b) else "nothing_retrieved",
        "a": {"text": option_a, "verses": only_a},
        "b": {"text": option_b, "verses": only_b},
        "shared": shared_out,
        # Disclosed because it is the feature's own evidence: if this is high,
        # the two sides are not really different questions and the split
        # screen is telling the user less than it appears to.
        "overlap": round(len(both) / len(set(rank_a) | set(rank_b)), 3),
    }
