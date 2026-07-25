"""Citation validation.

The entire trust model of this product rests on `[BG 3.37]` pointing at a verse
that really exists and really says what the answer claims. A model that invents
a plausible-looking citation produces output indistinguishable from correct
output, so the check cannot be advisory -- it runs on every generated answer
before the user sees it.

Two failure modes, and they are different:

  NONEXISTENT   the verse is not in the corpus (e.g. [BG 2.99]). Always a bug.
  OUT_OF_CONTEXT the verse exists but was never retrieved for this question, so
                the model is citing from memory rather than from the grounding
                material. The verse text may even support the claim -- but the
                answer was not derived from it, and at that point the citation
                is decoration, not evidence.
"""

import re
from dataclasses import dataclass, field
from enum import Enum

from .. import canon

# Matches [BG 3.37], [BG 3:37], [BG. 3.37], and the bare BG 3.37 form.
CITATION = re.compile(r"\[?\bBG\.?\s*(\d{1,2})\s*[.:]\s*(\d{1,3})\b\]?")


class Verdict(str, Enum):
    OK = "ok"
    NONEXISTENT = "nonexistent"
    OUT_OF_CONTEXT = "out_of_context"


@dataclass
class Citation:
    raw: str
    verse_id: str
    verdict: Verdict
    span: tuple[int, int]


@dataclass
class Report:
    citations: list[Citation] = field(default_factory=list)
    uncited: bool = False

    @property
    def ok(self) -> bool:
        return not self.uncited and all(
            c.verdict is Verdict.OK for c in self.citations)

    @property
    def bad(self) -> list[Citation]:
        return [c for c in self.citations if c.verdict is not Verdict.OK]

    def summary(self) -> str:
        if self.uncited:
            return "REJECT: answer contains no citations"
        if self.ok:
            return "OK: %d citation(s), all valid and in context" % len(self.citations)
        parts = [
            "%s (%s)" % (c.verse_id, c.verdict.value) for c in self.bad
        ]
        return "REJECT: %d bad citation(s): %s" % (len(self.bad), ", ".join(parts))


def known_verse_ids(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT verse_id FROM verses")}


def extract(text: str) -> list[tuple[str, str, tuple[int, int]]]:
    """Pull every citation out of an answer as (raw, verse_id, span)."""
    out = []
    for match in CITATION.finditer(text):
        chapter, verse = int(match.group(1)), int(match.group(2))
        out.append((match.group(0), canon.verse_id(chapter, verse), match.span()))
    return out


def validate(
    text: str,
    *,
    valid_ids: set[str],
    context_ids: set[str] | None = None,
    require_citation: bool = True,
) -> Report:
    """Check every citation in `text`.

    `context_ids` is the set of verses actually passed to the model for this
    question. Omitting it downgrades the check to existence only, which is
    weaker -- pass it whenever the retrieval set is available.
    """
    report = Report()
    found = extract(text)

    if require_citation and not found:
        report.uncited = True
        return report

    for raw, verse_id, span in found:
        if verse_id not in valid_ids:
            verdict = Verdict.NONEXISTENT
        elif context_ids is not None and verse_id not in context_ids:
            verdict = Verdict.OUT_OF_CONTEXT
        else:
            verdict = Verdict.OK
        report.citations.append(Citation(raw, verse_id, verdict, span))
    return report


def cited_verse_ids(text: str) -> list[str]:
    """Deduplicated citations in order of first appearance."""
    seen, out = set(), []
    for _, verse_id, _ in extract(text):
        if verse_id not in seen:
            seen.add(verse_id)
            out.append(verse_id)
    return out
