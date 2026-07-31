""""Show me the opposite" -- the verses that face the other way.

An answer is built from verses that matched the question as asked. That is
what makes it useful and also what makes it one-sided: ask a question shaped
like self-justification and retrieval will happily return the verses that
console you. The counterweight is the point of reading a scripture rather
than a horoscope, and nothing in the pipeline surfaced it.

This finds that counterweight WITHOUT a model call, by exploiting a structural
property of the stance enrichment rather than by guessing at semantics.

Stance lines were generated in an explicit contrast form:

    "a warning to someone who is becoming corrupted by power,
     NOT comfort for the powerless"
    "a diagnosis of the anger itself, NOT justification for it"

MEASURED over the store: 1420 of 2312 stance lines (61.4%) carry an explicit
contrast pivot, and 605 of 700 stanced verses (86.4%) have at least one. So
the right-hand clause is reliably available, and it is exactly the description
of the reader this verse is *not* for -- which is to say, a description of the
verse someone else needs.

So the opposite of a set of verses is: take their right-hand clauses, use them
as a query, and run ordinary free retrieval. The query is written in the
corpus's own vocabulary, which is why this works with BM25 at all.

Note what this deliberately does NOT do. It does not try to detect negation
with an index -- corpus.py records, with numbers, that neither BM25 nor a
pooled embedding can represent "not X", which is why stance is unindexed. This
sidesteps that instead of re-fighting it: the negation is resolved by string
surgery on a known sentence shape, and only the *positive* remainder is ever
handed to a ranker.
"""

import re

# The pivot forms actually present in the generated stance text. Anchored on a
# comma so it cannot fire inside a clause ("someone who has not yet...").
_PIVOT = re.compile(r",\s*(?:and\s+|but\s+)?(?:not|never|rather\s+than)\s+", re.I)

# Leading filler on the extracted clause. Dropping it is cosmetic for BM25
# (these are stopwords anyway) but keeps the clause readable when shown.
_LEAD = re.compile(r"^(?:for|to|as|a|an|the)\s+", re.I)

# A clause shorter than this is a fragment like "for it" or "blame" with the
# subject stranded on the other side of the comma -- too little to retrieve on
# and too little to display.
MIN_CLAUSE_CHARS = 12


def opposing_clauses(stance) -> list[str]:
    """The right-hand side of each contrastive stance line, deduplicated.

    Returns [] for a verse whose stance carries no pivot -- about one verse in
    seven. That is reported rather than patched over; see `counterpoint()`.
    """
    out: list[str] = []
    seen: set[str] = set()
    for line in stance or []:
        parts = _PIVOT.split(line)
        # split() with no groups returns [left, right, ...]; anything past the
        # first pivot is a second contrast in the same sentence, also usable.
        for clause in parts[1:]:
            clause = _LEAD.sub("", clause.strip().rstrip(".")).strip()
            if len(clause) < MIN_CLAUSE_CHARS:
                continue
            key = clause.lower()
            if key not in seen:
                seen.add(key)
                out.append(clause)
    return out


def build_query(records, verse_ids) -> tuple[str, list[str]]:
    """Assemble the opposing-side query from the verses that grounded an answer.

    Returns (query, clauses) so a caller can show the user what was actually
    searched for -- this is a derived query, and silently searching for
    something the user never typed is the kind of thing that should be visible.
    """
    clauses: list[str] = []
    seen: set[str] = set()
    for vid in verse_ids:
        rec = records.get(vid)
        if rec is None or not rec.enrichment:
            continue
        for clause in opposing_clauses(rec.enrichment.get("stance")):
            key = clause.lower()
            if key not in seen:
                seen.add(key)
                clauses.append(clause)
    return " ".join(clauses), clauses


def counterpoint(records, retrieve, verse_ids, *, k: int = 5) -> dict:
    """Verses facing the other way from `verse_ids`. No model call, no cost.

    `retrieve` is the pipeline's own retrieval callable, so this inherits BM25
    + dense fusion and every future improvement to it for free.
    """
    used = set(verse_ids)
    query, clauses = build_query(records, verse_ids)

    if not clauses:
        # Honest empty rather than a fabricated one. Happens when none of the
        # grounding verses carry a contrastive stance -- roughly one verse in
        # seven has none, so a whole set having none is rare but possible.
        return {"ok": False, "reason": "no_contrastive_stance", "query": "",
                "clauses": [], "verses": []}

    # Over-fetch: everything already shown is filtered out afterwards, and in
    # the worst case the opposing query returns the same set it came from.
    hits = retrieve(query, k + len(used))

    verses = []
    for hit in hits:
        if hit.doc_id in used:
            continue
        rec = records.get(hit.doc_id)
        if rec is None:
            continue
        verses.append({
            "verse_id": hit.doc_id,
            "rank": len(verses) + 1,
            "score": round(hit.score, 3),
            "speaker": rec.speaker,
            "summary": (rec.enrichment or {}).get("summary", ""),
            # The stance is why this verse is the counterweight, so it travels
            # with it. It is also the first time this field has been visible
            # anywhere in the app despite being generated for 700 verses.
            "stance": (rec.enrichment or {}).get("stance", []),
        })
        if len(verses) >= k:
            break

    return {"ok": bool(verses), "reason": "" if verses else "no_distinct_verses",
            "query": query, "clauses": clauses, "verses": verses}
