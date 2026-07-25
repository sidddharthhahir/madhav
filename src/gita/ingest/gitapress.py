"""Ingest the Gita Press (Gorakhpur) Hindi text, Jayadayal Goyandka.

Rights: Goyandka died in 1965. India's term is life + 60 counted from the start
of the following year, so it expired 31 December 2025 -- the work has been in
the public domain in India since 1 January 2026. This is specifically NOT Swami
Ramsukhdas's Sadhaka-Sanjivani, also a Gita Press edition, whose author died in
2005 and remains in copyright until 2066 (excluded as `rams` in sources.py).

Source is an archive.org OCR layer over page scans -- there is no API and no
structured release. Two consequences shape this module:

  1. The edition prints bare verse numbers in dandas, not chapter-verse pairs,
     and carries no end-of-chapter colophons. Chapter boundaries therefore have
     to be inferred from the verse counter resetting.
  2. OCR drops markers. Segmentation is validated against canon.GITA_PRESS and
     anything that does not line up is reported rather than stored, so a
     mis-segmented chapter cannot silently poison the corpus.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from .. import canon

SOURCE_KEY = "goyandka"
TRANSLATOR = "Jayadayal Goyandka (Gita Press, Gorakhpur)"
ORIGIN = "archive.org/GitaInHindi"

DEFAULT_TEXT = (Path(__file__).resolve().parents[3]
                / "data" / "sources" / "gitapress_goyandka_hi.txt")

# The Gita proper starts at 1.1; everything before is the Gita-mahatmya and
# front matter, which carries its own verse numbering and must be discarded.
START_ANCHOR = re.compile(r"धर्मक्षेत्रे")

VERSE_MARKER = re.compile(r"॥\s*([०-९]{1,3})\s*॥")
_DEVA_DIGITS = {d: str(i) for i, d in enumerate("०१२३४५६७८९")}


def deva_int(s: str) -> int | None:
    try:
        return int("".join(_DEVA_DIGITS.get(c, c) for c in s))
    except ValueError:
        return None


@dataclass
class Segment:
    chapter: int
    verse: int
    text: str

    @property
    def verse_id(self) -> str:
        return canon.verse_id(self.chapter, self.verse)


@dataclass
class SegmentResult:
    segments: list[Segment] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    observed: dict[int, int] = field(default_factory=dict)
    markers_seen: int = 0

    @property
    def aligned(self) -> list[Segment]:
        """Only segments in chapters whose count matches the canon exactly.

        A chapter whose count is wrong means its internal numbering drifted, so
        every verse in it is suspect -- not just the missing one. Admitting the
        rest would attach Hindi text to the wrong verse ids, which is worse
        than having no Hindi at all.
        """
        good = {ch for ch, n in self.observed.items()
                if n == canon.GITA_PRESS.get(ch)}
        return [s for s in self.segments if s.chapter in good]

    @property
    def clean_chapters(self) -> list[int]:
        return sorted(ch for ch, n in self.observed.items()
                      if n == canon.GITA_PRESS.get(ch))


def clean_ocr(text: str) -> str:
    """Light normalisation only.

    Deliberately conservative: collapse whitespace, drop control characters and
    the obvious scanner artefacts. Nothing that rewrites Devanagari, because a
    clever "fix" that guesses at mangled characters produces plausible-looking
    wrong text, which is the one outcome worth avoiding.
    """
    text = text.replace("‍", "").replace("‌", "")
    text = "".join(ch for ch in text
                   if unicodedata.category(ch)[0] != "C" or ch in "\n\t")
    # Runs of Latin/punctuation noise the scanner injects between columns.
    text = re.sub(r"[|_~^*#=<>\\/]{2,}", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def segment(raw: str) -> SegmentResult:
    """Walk verse markers, inferring chapter boundaries from counter resets."""
    result = SegmentResult()

    anchor = START_ANCHOR.search(raw)
    if not anchor:
        result.problems.append("could not find the chapter 1 anchor; "
                               "front matter cannot be separated")
        return result

    body = raw[anchor.start():]
    markers = list(VERSE_MARKER.finditer(body))
    result.markers_seen = len(markers)
    if not markers:
        result.problems.append("no verse markers after the anchor")
        return result

    chapter = 1
    prev_verse = 0
    cursor = 0

    for m in markers:
        num = deva_int(m.group(1))
        if num is None:
            continue

        # A number that does not advance means the counter reset: new chapter.
        if num <= prev_verse:
            expected = canon.GITA_PRESS.get(chapter)
            if prev_verse != expected:
                result.problems.append(
                    "chapter %d ended at verse %d, canon says %d"
                    % (chapter, prev_verse, expected))
            chapter += 1
            prev_verse = 0
            if chapter > 18:
                result.problems.append(
                    "counter reset past chapter 18; segmentation lost sync")
                break

        if num != prev_verse + 1:
            result.problems.append(
                "chapter %d: jumped from verse %d to %d (%d marker(s) missing)"
                % (chapter, prev_verse, num, num - prev_verse - 1))

        text = clean_ocr(body[cursor:m.start()])
        cursor = m.end()
        prev_verse = num
        if text:
            result.segments.append(Segment(chapter, num, text))

    expected = canon.GITA_PRESS.get(chapter)
    if chapter == 18 and prev_verse != expected:
        result.problems.append("chapter 18 ended at verse %d, canon says %d"
                               % (prev_verse, expected))

    counts: dict[int, int] = {}
    for s in result.segments:
        counts[s.chapter] = counts.get(s.chapter, 0) + 1
    result.observed = counts
    return result


def load(path: Path | str | None = None) -> str:
    p = Path(path or DEFAULT_TEXT)
    if not p.exists():
        raise FileNotFoundError(
            "OCR text not found at %s. Download it first:\n"
            "  Invoke-WebRequest "
            "https://archive.org/download/GitaInHindi/Gita%%20in%%20hindi_djvu.txt "
            "-OutFile %s" % (p, p))
    return p.read_text(encoding="utf-8", errors="replace")


def report(result: SegmentResult) -> str:
    lines = ["markers after anchor : %d  (canon expects 701)" % result.markers_seen,
             "segments produced    : %d" % len(result.segments),
             "",
             "%-6s %-8s %-8s %s" % ("chapter", "ocr", "canon", "status")]
    for ch in canon.CHAPTERS:
        got, want = result.observed.get(ch, 0), canon.GITA_PRESS[ch]
        lines.append("%-6d %-8d %-8d %s"
                     % (ch, got, want, "ok" if got == want else "MISALIGNED"))
    lines += ["",
              "chapters clean        : %d / 18  %s"
              % (len(result.clean_chapters), result.clean_chapters),
              "verses safe to ingest : %d / 701" % len(result.aligned)]
    if result.problems:
        lines += ["", "problems (%d):" % len(result.problems)]
        lines += ["  ! %s" % p for p in result.problems[:25]]
        if len(result.problems) > 25:
            lines.append("  ... and %d more" % (len(result.problems) - 25))
    return "\n".join(lines)
