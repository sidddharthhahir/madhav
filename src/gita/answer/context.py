"""Assemble retrieved verses into the grounding block for answer generation.

Two rules govern this module.

First, the context IS the citable universe. The validator rejects any citation
to a verse that was not in the context, so whatever this builds defines what a
correct answer is allowed to reference. Adding a verse here grants permission to
cite it; dropping one revokes that permission.

Second, order matters more than volume. Attention is not uniform across a long
context, so the best-scoring verses go first and the tail gets trimmed rather
than the head. Passing 30 verses does not beat passing 6 good ones -- it dilutes
them and invites the model to cite something marginal.
"""

from dataclasses import dataclass, field

from .. import speakers

# Commentary is the richest interpretive material available (Sivananda on all
# 701 verses) but it is long and, in places, carries OCR damage from the source
# scans. Cap it so it supports the answer without dominating the context.
COMMENTARY_CHARS = 1200
SITUATIONS_SHOWN = 6


@dataclass
class ContextVerse:
    verse_id: str
    chapter: int
    verse: int
    score: float
    rank: int
    enriched: bool
    block: str


@dataclass
class Context:
    verses: list[ContextVerse] = field(default_factory=list)
    text: str = ""

    @property
    def verse_ids(self) -> set[str]:
        """The set the citation validator checks against."""
        return {v.verse_id for v in self.verses}

    @property
    def approx_tokens(self) -> int:
        return len(self.text) // 4


def render_verse(rec, hit=None, *, include_commentary: bool = True) -> str:
    """One verse as the model sees it."""
    lines = ["<verse id=\"%s\">" % rec.verse_id,
             "reference: BG %d.%d" % (rec.chapter, rec.verse)]

    # Who is speaking, and what that makes the words. Without this the model
    # sees Arjuna's "my limbs fail me, my throat is parched" as material of
    # exactly the same kind as Krishna's reply to it, and will quote despair
    # back to someone in despair as though it were counsel.
    speaker = getattr(rec, "speaker", None)
    if speaker:
        lines.append("spoken by: %s -- %s"
                     % (speaker, speakers.ROLE.get(speaker, "")))

    if rec.sanskrit:
        lines.append("sanskrit: %s" % rec.sanskrit.replace("\n", " / "))

    for key, body in sorted(rec.translations.items()):
        lines.append("translation (%s): %s" % (key, body.strip()))

    if rec.enrichment:
        e = rec.enrichment
        if e.get("summary"):
            lines.append("plain meaning: %s" % e["summary"].strip())
        if e.get("themes"):
            lines.append("themes: %s" % ", ".join(e["themes"]))
        if e.get("situations"):
            lines.append("speaks to: %s" % "; ".join(e["situations"][:SITUATIONS_SHOWN]))

    if include_commentary and rec.commentary:
        for key, body in sorted(rec.commentary.items()):
            excerpt = body.strip()[:COMMENTARY_CHARS]
            lines.append("commentary (%s): %s" % (key, excerpt))

    lines.append("</verse>")
    return "\n".join(lines)


def build(records, hits, *, max_verses: int = 8, token_budget: int = 24000) -> Context:
    """Turn ranked hits into a Context, trimming from the tail when over budget."""
    ctx = Context()
    blocks: list[str] = []
    used = 0

    for hit in hits[:max_verses]:
        rec = records.get(hit.doc_id)
        if rec is None:
            continue

        block = render_verse(rec, hit)
        cost = len(block) // 4

        # Always admit the top hit; without it there is nothing to ground on.
        if blocks and used + cost > token_budget:
            break

        blocks.append(block)
        used += cost
        ctx.verses.append(ContextVerse(
            verse_id=rec.verse_id, chapter=rec.chapter, verse=rec.verse,
            score=hit.score, rank=hit.rank,
            enriched=rec.enrichment is not None, block=block,
        ))

    ctx.text = "\n\n".join(blocks)
    return ctx


def citable_list(ctx: Context) -> str:
    """Explicit allowlist echoed into the prompt.

    Stating the permitted ids as a flat list, separately from the verse blocks,
    measurably reduces citations to verses that merely got mentioned inside a
    commentary passage rather than being retrieved in their own right.
    """
    return ", ".join("[BG %d.%d]" % (v.chapter, v.verse) for v in ctx.verses)
