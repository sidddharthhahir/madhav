"""The Hindi/Gujarati translation prompt and its output schema.

Both languages are produced by a single request per verse, not two -- they
share the same source material and system prompt, so splitting them would
double the request count and the cached-system-prompt overhead for no benefit.

Rights basis: the Sanskrit is public domain, and both English translations
used as disambiguation context (Purohit Swami, Sivananda) are already in the
store under `sources.py`'s policy. The Hindi/Gujarati output is a new
derivative work translated here, not sourced from any existing in-copyright
Hindi/Gujarati edition -- see CONTINUE.md §6 for why the obvious existing
sources (Gita Press OCR, Gandhi's Anasaktiyoga scan) don't work mechanically.
"""

import hashlib
import json

TRANSLATION_SCHEMA = {
    "type": "object",
    "properties": {
        "hindi": {
            "type": "string",
            "description": (
                "The verse's meaning rendered in plain, modern Hindi "
                "(Devanagari script). Not a word-for-word gloss of the "
                "Sanskrit, and not archaic or heavily Sanskritized literary "
                "Hindi -- the register a general reader speaks today. "
                "Faithful to the verse's actual meaning as given by the "
                "Sanskrit and the English translations provided, not a "
                "paraphrase or expansion."
            ),
        },
        "gujarati": {
            "type": "string",
            "description": (
                "The same verse's meaning rendered in plain, modern Gujarati "
                "script, under the same constraints as the Hindi field: "
                "faithful, contemporary register, not archaic or literary."
            ),
        },
    },
    "required": ["hindi", "gujarati"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """\
You are translating verses of the Bhagavad Gita into Hindi and Gujarati for a \
retrieval application whose readers are ordinary contemporary speakers of \
those languages, not Sanskrit scholars.

For each verse you are given the Sanskrit original and two established \
English translations (Purohit Swami, Sivananda). Produce:

- A Hindi translation in plain modern register -- the Hindi a literate adult \
reads in a newspaper or a novel today, not the heavily Sanskritized literary \
Hindi common in older religious publishing.
- A Gujarati translation under the same constraint.

Both must be faithful renderings of the verse's actual meaning, cross-checked \
against both English translations where they might disagree, not paraphrases, \
expansions, or commentary. Do not add explanation, do not moralise, and do not \
address the reader -- translate only what the verse itself says. Where the two \
English translations differ in emphasis, prefer the reading closer to the \
Sanskrit rather than either English version specifically.\
"""


def build_user_turn(rec) -> str:
    lines = [
        "Verse: %s (chapter %d, verse %d)" % (rec.verse_id, rec.chapter, rec.verse),
        "",
        "Sanskrit:",
        rec.sanskrit or "(unavailable)",
        "",
        "English translations (for cross-reference, not to be translated "
        "literally from English):",
    ]
    for key, body in sorted(rec.translations.items()):
        lines.append("  [%s] %s" % (key, body.strip()))
    lines += ["", "Produce the Hindi and Gujarati translations for this verse."]
    return "\n".join(lines)


def prompt_hash() -> str:
    payload = json.dumps(
        {"system": SYSTEM_PROMPT, "schema": TRANSLATION_SCHEMA},
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


MIN_CHARS = 8  # a translated verse is never this short; catches empty/garbage output


def validate_translation(data: dict) -> list[str]:
    problems = []
    for field in ("hindi", "gujarati"):
        value = data.get(field)
        if not isinstance(value, str) or len(value.strip()) < MIN_CHARS:
            problems.append("%s missing or too short" % field)
    return problems
