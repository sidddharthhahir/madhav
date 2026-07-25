"""Repair OCR damage in the ingested commentary.

The Sivananda commentary reached the upstream dataset via OCR of printed pages,
and the scanner systematically misread comma and semicolon glyphs as question
marks: 694 of 701 rows are affected, ~13,000 stray marks in total. That text is
input to the enrichment prompt, so the damage propagates into the layer that
retrieval actually searches.

Design constraint: only repair what is unambiguous. A cleaner that guesses at
mangled words produces plausible-looking wrong text, which is worse than
visible damage -- a reader can see a stray '?' and discount it, but cannot see
a silently invented word. So this module fixes punctuation position and spacing
only, and never rewrites letters.
"""

import re

# A question mark that is really a comma sits between two words, with no
# sentence boundary around it. A genuine question mark follows a clause and is
# followed by a capital letter or end of string. The distinction is positional,
# which is why it can be repaired safely.
_MID_WORD_Q = re.compile(r"(?<=[a-zऀ-ॿ])\s*\?\s*(?=[a-zऀ-ॿ])")

# Two or more question marks in a row are never real punctuation here.
_RUN_Q = re.compile(r"\?{2,}")

# The scanner also emits '?' immediately before a closing bracket or a period.
_STRANDED_Q = re.compile(r"\s*\?\s*(?=[.);\]])")

# Devanagari danda followed by a stray Latin question mark.
_DANDA_Q = re.compile(r"([।॥])\s*\?")

_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.;:!])")
_MULTISPACE = re.compile(r"[ \t]{2,}")


def repair(text: str | None) -> str:
    """Fix OCR punctuation damage. Returns '' for None."""
    if not text:
        return ""

    text = _RUN_Q.sub(",", text)
    text = _DANDA_Q.sub(r"\1", text)
    text = _STRANDED_Q.sub("", text)
    # Run the mid-word pass repeatedly: overlapping matches mean a single pass
    # leaves alternating marks behind in dense runs.
    for _ in range(3):
        new = _MID_WORD_Q.sub(", ", text)
        if new == text:
            break
        text = new

    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = _MULTISPACE.sub(" ", text)
    return text.strip()


def damage_score(text: str | None) -> int:
    """Stray question marks remaining. Used to verify a repair actually worked."""
    if not text:
        return 0
    return len(_MID_WORD_Q.findall(text)) + len(_RUN_Q.findall(text))
