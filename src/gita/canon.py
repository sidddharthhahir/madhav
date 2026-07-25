"""Canonical structure of the Bhagavad Gita, used to validate every ingest.

The verse count is NOT universally agreed. Chapter 13 has 34 verses in the
Shankara recension and 35 in the Gita Press recension, which is why the text is
described as having either 700 or 701 verses depending on the edition. Any
pipeline that hardcodes one and joins sources from the other silently
misaligns every verse from 13.x onward, so we treat the count as a property of
the source and check it rather than assume it.
"""

# Gita Press recension (chapter 13 = 35 verses, total 701).
GITA_PRESS = {
    1: 47, 2: 72, 3: 43, 4: 42, 5: 29, 6: 47, 7: 30, 8: 28, 9: 34,
    10: 42, 11: 55, 12: 20, 13: 35, 14: 27, 15: 20, 16: 24, 17: 28, 18: 78,
}

# Shankara recension (chapter 13 = 34 verses, total 700).
SHANKARA = {**GITA_PRESS, 13: 34}

RECENSIONS = {"gita_press": GITA_PRESS, "shankara": SHANKARA}

CHAPTERS = tuple(range(1, 19))

CHAPTER_TITLES = {
    1: "Arjuna Vishada Yoga",
    2: "Sankhya Yoga",
    3: "Karma Yoga",
    4: "Jnana Karma Sanyasa Yoga",
    5: "Karma Sanyasa Yoga",
    6: "Dhyana Yoga",
    7: "Jnana Vijnana Yoga",
    8: "Aksara Brahma Yoga",
    9: "Raja Vidya Raja Guhya Yoga",
    10: "Vibhuti Yoga",
    11: "Visvarupa Darsana Yoga",
    12: "Bhakti Yoga",
    13: "Ksetra Ksetrajna Vibhaga Yoga",
    14: "Gunatraya Vibhaga Yoga",
    15: "Purusottama Yoga",
    16: "Daivasura Sampad Vibhaga Yoga",
    17: "Sraddhatraya Vibhaga Yoga",
    18: "Moksa Sanyasa Yoga",
}


def verse_id(chapter: int, verse: int) -> str:
    """Stable citation key. 'BG.2.47' renders to the user as [BG 2.47]."""
    return "BG.%d.%d" % (chapter, verse)


def parse_verse_id(vid: str) -> tuple[int, int]:
    prefix, chapter, verse = vid.split(".")
    if prefix != "BG":
        raise ValueError("not a Bhagavad Gita verse id: %r" % vid)
    return int(chapter), int(verse)


def identify_recension(counts: dict[int, int]) -> str:
    """Name the recension a set of observed per-chapter counts matches."""
    for name, ref in RECENSIONS.items():
        if counts == ref:
            return name
    return "unknown"


def diff_against_recensions(counts: dict[int, int]) -> dict[str, dict[int, tuple[int, int]]]:
    """Per-recension map of chapter -> (observed, expected) for mismatches only."""
    out = {}
    for name, ref in RECENSIONS.items():
        delta = {
            ch: (counts.get(ch, 0), ref[ch])
            for ch in CHAPTERS
            if counts.get(ch, 0) != ref[ch]
        }
        out[name] = delta
    return out
