"""The enrichment prompt and its output schema.

This is the highest-leverage component in the system. Retrieval is not run
against the verse; it is run against what this prompt produces. A verse about
dvandva-moha has to become findable from "why do I hate a stranger online",
and that only happens if the enrichment names the modern situation in the
words a user would actually type.

The system prompt is held constant across all 701 requests so it caches. Only
the per-verse user turn varies.
"""

import hashlib
import json

from .. import textclean

# Field constraints are stated in the prompt, not the schema: the structured
# outputs implementation does not support array-length or string-length
# constraints, so minItems/maxItems would be silently stripped. We validate
# counts client-side instead (see validate_enrichment below).
ENRICHMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": (
                "2-4 plain-English sentences on what this verse is actually "
                "saying about human experience. No Sanskrit terms unless you "
                "immediately gloss them. Written for someone who has never "
                "read the Gita."
            ),
        },
        "themes": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "4-8 abstract concepts this verse addresses, as short noun "
                "phrases. e.g. 'envy', 'unfulfilled desire', 'comparison to "
                "others', 'letting go of outcomes'."
            ),
        },
        "situations": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "5-10 concrete modern situations where a person might reach "
                "for this verse. Full clauses, present tense, naming ordinary "
                "contemporary life. e.g. 'resenting a colleague who got the "
                "promotion you wanted', 'doomscrolling and feeling worse "
                "about your own life', 'being unable to stop thinking about "
                "someone who wronged you'. This field does the most work in "
                "retrieval -- be specific and varied, not abstract."
            ),
        },
        "emotions": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "3-8 emotions or inner states a person would be feeling when "
                "this verse becomes relevant. Plain words: 'jealousy', "
                "'resentment', 'restlessness', 'grief', 'dread'."
            ),
        },
        "keywords": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "8-15 single words or short phrases someone might type into a "
                "search box to find this idea. Everyday vocabulary, not "
                "scholarly. Include informal register where natural."
            ),
        },
    },
    "required": ["summary", "themes", "situations", "emotions", "keywords"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """\
You are building a retrieval index over the Bhagavad Gita for an application \
where people ask personal questions about their own lives and receive an answer \
grounded in specific, cited verses.

Your job for each verse is to write the bridge between the verse and the \
questions real people ask. The application searches your output, not the verse \
text. This matters because the vocabulary almost never overlaps: someone asks \
"why do I resent a stranger on the internet who has never done anything to me", \
and the verse that answers it speaks of desire transmuting into anger. If your \
output does not contain the words that person would use, the verse is \
unreachable and the answer will cite something worse.

So write for findability:

- Name concrete modern situations. Social media, work, family, money, dating, \
grief, illness, burnout, comparison, loneliness. Ordinary life as lived now.
- Use the register people search in. "Can't stop comparing myself to people \
online" beats "the affliction of comparative self-regard".
- Cover the emotional entry points, not just the philosophical content. People \
arrive at a verse through what they are feeling.
- Be faithful. Describe what the verse and its commentary actually say. Do not \
stretch a verse to cover a situation it does not speak to, and do not \
manufacture relevance -- a narrow verse should get a narrow enrichment. A \
precise mapping is worth more than a broad one, because a wrong verse retrieved \
confidently is worse than no verse.
- Do not moralise, do not preach, and do not address the reader. You are \
writing index metadata, not advice.

Draw on the translations and commentary provided. Where commentators differ, \
reflect the common ground rather than picking a side.\
"""


def _clip(text: str | None, limit: int) -> str:
    """Repair OCR damage, then clip.

    Repair happens before clipping so the character budget is spent on usable
    text rather than on stray punctuation -- the commentary carries ~13,000
    misread question marks, which is real budget at 2,500 chars per verse.
    """
    text = textclean.repair(text)
    if not text:
        return ""
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + " ..."


def build_user_turn(rec, *, commentary_chars: int = 2500) -> str:
    """Render one verse into the per-request user turn."""
    lines = [
        "Verse: %s (chapter %d, verse %d)" % (rec.verse_id, rec.chapter, rec.verse),
        "",
        "Sanskrit:",
        rec.sanskrit or "(unavailable)",
        "",
        "English translations:",
    ]
    for key, body in sorted(rec.translations.items()):
        lines.append("  [%s] %s" % (key, body.strip()))

    if rec.commentary:
        lines += ["", "Commentary:"]
        for key, body in sorted(rec.commentary.items()):
            lines.append("  [%s] %s" % (key, _clip(body, commentary_chars)))

    lines += ["", "Produce the enrichment record for this verse."]
    return "\n".join(lines)


def prompt_hash() -> str:
    """Identifies the prompt+schema pair that produced a stored enrichment.

    Stored per row so a prompt revision is detectable -- otherwise a corpus
    half-generated by an old prompt looks identical to a consistent one.
    """
    payload = json.dumps(
        {"system": SYSTEM_PROMPT, "schema": ENRICHMENT_SCHEMA},
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# Client-side equivalents of the constraints the schema cannot express.
# `min` is a correctness floor: below it the record is too thin to be useful.
# `max` is a preference, not a defect -- see normalise_enrichment.
FIELD_BOUNDS = {
    "themes": (3, 12),
    "situations": (4, 14),
    "emotions": (2, 10),
    "keywords": (5, 20),
}

MIN_SUMMARY_CHARS = 40


def normalise_enrichment(data: dict) -> tuple[dict, list[str]]:
    """Coerce a record into shape, returning (record, notes).

    An over-long list is NOT a defect. The earlier version rejected the whole
    record when any field ran over its maximum, which meant one extra keyword
    silently cost that verse its enrichment forever -- paid for and discarded,
    across a 701-verse batch. Overflow is now trimmed and noted instead.

    Only genuine breakage still fails: a missing field, a non-list, too few
    items to be useful, or a summary too short to say anything.
    """
    out: dict = {}
    notes: list[str] = []

    summary = (data.get("summary") or "").strip()
    out["summary"] = summary

    for field, (low, high) in FIELD_BOUNDS.items():
        items = data.get(field)
        if not isinstance(items, list):
            out[field] = []
            continue
        cleaned, seen = [], set()
        for item in items:
            if not isinstance(item, str):
                continue
            value = " ".join(item.split())
            key = value.lower()
            if value and key not in seen:
                seen.add(key)
                cleaned.append(value)
        if len(cleaned) > high:
            notes.append("%s trimmed from %d to %d" % (field, len(cleaned), high))
            cleaned = cleaned[:high]
        out[field] = cleaned

    return out, notes


def validate_enrichment(data: dict) -> list[str]:
    """Return blocking problems only; empty means the record is usable.

    Call on the output of normalise_enrichment, not on raw model output.
    """
    problems = []
    if len(data.get("summary", "")) < MIN_SUMMARY_CHARS:
        problems.append("summary too short (%d chars, need %d)"
                        % (len(data.get("summary", "")), MIN_SUMMARY_CHARS))

    for field, (low, _high) in FIELD_BOUNDS.items():
        items = data.get(field)
        if not isinstance(items, list):
            problems.append("%s missing or not a list" % field)
        elif len(items) < low:
            problems.append("%s has %d items, need at least %d"
                            % (field, len(items), low))
    return problems
