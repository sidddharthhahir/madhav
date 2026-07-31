"""Who is speaking in each verse.

The Gita is a dialogue inside a dialogue: Sanjaya narrates the whole thing to
the blind king Dhritarashtra, and inside that narration Arjuna and Krishna
speak. Retrieval and answering treated all 701 verses as one undifferentiated
body of counsel, which is wrong in a way that matters for a citing app --
BG.1.29 ("my limbs give way, my mouth is dry, my body trembles") is not
advice. It is a man having a panic attack, and the remaining seventeen
chapters are the reply to it. Grounding an answer in it as though it were
teaching misrepresents the text.

Derived, not stored. A speaker is declared by an `uvaca` ("said") marker on
its own line, and holds until the next marker -- so the whole attribution
follows from 59 markers in the Sanskrit already in the corpus. Nothing is
added to the database and there is no migration; this cannot drift from the
text because it IS the text.

THE TRAP, because it silently produces a plausible wrong answer: Sanskrit
sandhi fuses `bhagavan` + `uvaca` into `bhagavanuvaca`, which turns the
independent vowel U+0909 (उ) into the dependent sign U+0941 (ु) attached to
the preceding consonant. So a substring search for "उवाच" matches Arjuna,
Sanjaya and Dhritarashtra -- and misses every one of Krishna's 574 verses,
the ones that matter most. It does not error; it just quietly attributes all
of Krishna's teaching to whoever spoke last. Match the full markers below,
never the bare verb.
"""

KRISHNA = "Krishna"
ARJUNA = "Arjuna"
SANJAYA = "Sanjaya"
DHRITARASHTRA = "Dhritarashtra"

# Full speech markers, including the sandhi-fused form for Krishna. Order is
# irrelevant -- they cannot co-occur on one line -- but Krishna is first
# because he is 82% of the text.
MARKERS = (
    ("श्रीभगवानुवाच", KRISHNA),
    ("अर्जुन उवाच", ARJUNA),
    ("सञ्जय उवाच", SANJAYA),
    ("धृतराष्ट्र उवाच", DHRITARASHTRA),
)

# What each speaker's words ARE, for the answer stage. The distinction the
# model needs is not who talks but what the words are evidence of.
ROLE = {
    KRISHNA: "the teaching itself",
    ARJUNA: "the student's question, doubt or distress -- what is being answered, not the answer",
    SANJAYA: "narration of the scene, not instruction",
    DHRITARASHTRA: "the blind king's opening question, not instruction",
}

# Only Krishna's words are counsel. Used where that distinction has to be
# made mechanically rather than by prose.
TEACHING = frozenset({KRISHNA})

# The one place a speaker changes with NO marker.
#
# BG.1.28 is half narration and half speech: "kripaya parayavishto vishidann
# idam abravit" -- "overcome with pity, sorrowing, he said this" -- and then
# Arjuna begins. There is no `arjuna uvaca` line, because the verse itself
# says he spoke. Marker-following alone therefore keeps attributing 1.29
# through 1.46 to Sanjaya, which is how the app came to treat "my limbs fail
# me, my throat is parched" as narration rather than as the distress the rest
# of the text answers.
#
# This is the only such transition, and the arithmetic confirms it rather
# than merely permitting it: marker-following alone yields Arjuna 67 /
# Sanjaya 59, and moving exactly 1.29-1.46 gives 85 / 41 -- the counts the
# traditional Gita Mahatmya quotes. Two independent things agreeing on the
# same 18 verses is the evidence for hard-coding them.
#
# 1.28 itself stays with Sanjaya: verses are atomic here, and its first line
# is the narrator's.
UNMARKED = {("BG.1.%d" % v): ARJUNA for v in range(29, 47)}

# Derived over the committed corpus and asserted by scripts/test_speakers.py,
# so a corpus change that moves these is loud rather than silent. All four
# match the traditional counts once UNMARKED is applied.
EXPECTED = {KRISHNA: 574, ARJUNA: 85, SANJAYA: 41, DHRITARASHTRA: 1}


def marker_in(sanskrit: str) -> str | None:
    """The speaker declared by this verse's opening line, if any.

    Only the first line is considered. `uvaca` also occurs mid-verse as an
    ordinary past-tense verb ("he said"), which is speech being reported
    inside a verse, not a change of speaker.
    """
    if not sanskrit:
        return None
    head = sanskrit.split("\n")[0]
    for marker, name in MARKERS:
        if marker in head:
            return name
    return None


def attribute(rows) -> dict[str, str]:
    """Map verse_id -> speaker over an ordered sequence of verses.

    `rows` must be in canonical order (chapter, then verse); a speaker holds
    until the next marker, so order is the whole mechanism. Each row needs
    `verse_id` and `sanskrit` (attribute or mapping access).
    """
    out: dict[str, str] = {}
    current: str | None = None
    for row in rows:
        vid = row["verse_id"] if hasattr(row, "keys") else row.verse_id
        sanskrit = row["sanskrit"] if hasattr(row, "keys") else row.sanskrit
        declared = marker_in(sanskrit)
        if declared:
            current = declared
        # Before the first marker there is no speaker. In the shipped corpus
        # that never happens -- BG.1.1 opens with Dhritarashtra -- but a
        # partial corpus should not be silently attributed to nobody.
        out[vid] = UNMARKED.get(vid) or current or DHRITARASHTRA
        # An unmarked stretch does not end the speech it interrupts: after
        # 1.46 the narration resumes, and 1.47 re-declares Sanjaya anyway.
        # `current` is deliberately left untouched by UNMARKED so the marker
        # chain stays the single source of truth for everything else.
    return out


def is_teaching(speaker: str | None) -> bool:
    return speaker in TEACHING
