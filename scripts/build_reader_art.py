"""Optimise the reader's curated artwork and write NOTICE.md entries.

Run once, offline, against the raw downloads -- nothing here touches the
network or a model. Committed output is the resized WebP files plus the
attribution block; the raw originals are NOT committed (they are 2-4MB each
and only the optimised copy is ever served).

    python scripts/build_reader_art.py
"""

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
RAW = Path("/private/tmp/claude-501/-Users-siddharth-Madhav/"
           "9185102b-fe6f-4519-aad3-da2ec4d06d74/scratchpad/art_raw")
OUT = ROOT / "frontend" / "web" / "art"

# Full-bleed behind text on a phone-to-desktop range. 1600px covers a 1600px
# CSS width at 1x or an 800px width at 2x; past that the reader scales the
# image down anyway; the resample cost of shipping 3316px originals bought
# nothing.
MAX_W = 1600

# (raw filename, art slot filename, chapter it opens, whether it actually
# depicts the moment vs. is thematically associated, credit block)
#
# Two of these five are honest mismatches and are labelled as such rather
# than captioned as if they were illustrations of the verse. Assigned per
# CHAPTER, not per verse: applied to every scene in that chapter until a
# later plate targets a specific verse, so one plate covers many screens
# instead of decorating a single one.
PLATES = [
    ("krishna_arjuna_gita.jpg", "bg-2-krishna-teaching.webp", 2, "depicts", {
        "title": "Krishna and Arjuna on the field of Kurukshetra",
        "date": "c. 1830",
        "medium": "Gouache on paper, from an album of seventy paintings of Hindu deities",
        "source": "British Museum, via Wikimedia Commons",
        "url": "https://commons.wikimedia.org/wiki/File:Krishna_Arjuna_Gita.jpg",
        "licence": "Public Domain Mark 1.0 -- published before 1931",
    }),
    ("vishvarupa_print.jpg", "bg-11-vishvarupa.webp", 11, "depicts", {
        "title": "Vishvarupa, the cosmic form",
        "date": "early-to-mid 20th century (≤ 1940)",
        "medium": "Devotional print, style associated with the Ravi Varma press",
        "source": "Columbia University Bhagavad Gita digital collection, via Wikimedia Commons",
        "url": "https://commons.wikimedia.org/wiki/File:Vishvaprint4max.jpg",
        "licence": "Public domain in India and the United States",
    }),
    # Ordered deliberately: Dhritarashtra's court first, matching how chapter
    # 1 itself opens on his question, then Arjuna's collapse for the chapter's
    # second half. See build()'s docstring on why this is a lucky consequence
    # of what was available rather than a claim these were picked for it.
    ("razmnama_dhritarashtra.jpg", "bg-1-dhritarashtra.webp", 1, "associated", {
        "title": "Dhritarashtra Attacks the Statue of Bhima, folio from a Razmnama",
        "date": "c. 1616–17",
        "medium": "Opaque color and gold on paper",
        "source": "The Metropolitan Museum of Art (Howard Hodgkin Collection)",
        "url": "https://www.metmuseum.org/art/collection/search/825591",
        "licence": "Public domain (Met Open Access)",
        "note": ("Depicts a different scene involving Dhritarashtra; used for "
                 "chapter 1, which he opens, not as an illustration of BG.1.1."),
    }),
    # Navagunjara is a DIFFERENT Mahabharata story -- Krishna tests Arjuna's
    # vow of non-violence by appearing as a nine-part chimeric beast, after
    # Arjuna has laid down his weapons. It is not an illustration of BG.1,
    # but it is the same emotional beat (Arjuna, weapons down, undone before
    # Krishna) and the same manuscript tradition. Used for mood, captioned
    # honestly, never claimed as a depiction of the verse it sits behind.
    ("navagunjara.jpg", "bg-1-arjuna.webp", 1, "associated", {
        "title": "Navagunjara, a universal form of Krishna",
        "date": "c. 1835",
        "medium": "Opaque watercolor, ink, and gold on paper",
        "source": "The Metropolitan Museum of Art (Purchase, Evelyn Kranes Kossak Gift, 2006)",
        "url": "https://www.metmuseum.org/art/collection/search/73296",
        "licence": "Public domain (Met Open Access)",
        "note": ("A different Mahabharata episode -- Krishna tests Arjuna's vow "
                 "of non-violence in a nine-animal form -- used here for its "
                 "kindred image of Arjuna disarmed before Krishna, not as an "
                 "illustration of any specific verse."),
    }),
    # Not a Gita scene at all -- kept out of the verse mapping below and
    # listed here only so the file and its licence are documented in one
    # place; build() will not assign it to a chapter.
    ("govardhan.jpg", "unused-govardhan.webp", None, "unused", {
        "title": "Krishna Holds Up Mount Govardhan, folio from a Harivamsa",
        "date": "c. 1590–95",
        "medium": "Ink, opaque watercolor, and gold on paper",
        "source": "The Metropolitan Museum of Art (Purchase, Edward C. Moore Jr. Gift, 1928)",
        "url": "https://www.metmuseum.org/art/collection/search/448183",
        "licence": "Public domain (Met Open Access)",
        "note": ("Downloaded during art research but not used: a Krishna "
                 "story, not a Gita scene, and chapter 18 already has no "
                 "plate that fits it honestly. Kept documented in case a "
                 "future chapter mapping wants it."),
    }),
]


def build() -> dict[str, list[str]]:
    """chapter -> ordered list of art files for that chapter.

    A list, not one file, because chapter 1 has two honest plates and picking
    only one would throw away real art. The reader spreads a chapter's list
    evenly across its verses, so 1 gets Dhritarashtra's court early and
    Arjuna's collapse later -- which happens to match how the chapter itself
    moves, though that is a lucky consequence of using what was available,
    not a claim the plates were chosen to tell that story.
    """
    OUT.mkdir(parents=True, exist_ok=True)
    mapping: dict[str, list[str]] = {}
    for raw_name, out_name, chapter, _relation, _credit in PLATES:
        src = RAW / raw_name
        if not src.exists():
            print("  SKIP (not found): %s" % raw_name)
            continue
        im = Image.open(src).convert("RGB")
        if im.width > MAX_W:
            h = round(im.height * MAX_W / im.width)
            im = im.resize((MAX_W, h), Image.LANCZOS)
        dest = OUT / out_name
        im.save(dest, "WEBP", quality=82, method=6)
        kb = dest.stat().st_size / 1024
        where = "chapter %d" % chapter if chapter else "unused"
        print("  %-32s %4dx%-4d  %6.0f KB  -> %s" %
              (raw_name, im.width, im.height, kb, where))
        if chapter is not None:
            mapping.setdefault(str(chapter), []).append(out_name)
    return mapping


def notice_block() -> str:
    lines = [
        "",
        "## Reader artwork",
        "",
        "Curated public-domain paintings used as background art in the immersive",
        "reader (`GET /read/{chapter}` -> `frontend/web/art/`). Each was individually",
        "verified public domain before download; none were AI-generated. Resized to",
        "%dpx on the long edge and re-encoded as WebP -- the museum-issued originals" % MAX_W,
        "are not redistributed here.",
        "",
        "Two of the five are captioned as ASSOCIATED rather than DEPICTS: the",
        "closest available public-domain art for that chapter's mood, not an",
        "illustration of any specific verse in it. The app must not claim otherwise.",
        "",
    ]
    for raw_name, out_name, chapter, relation, c in PLATES:
        where = "Chapter %d" % chapter if chapter else "Not used in the app"
        lines += [
            "**%s** (%s)" % (c["title"], c["date"]),
            "- %s -- %s" % (where, relation.upper()),
            "- Medium: %s" % c["medium"],
            "- Source: [%s](%s)" % (c["source"], c["url"]),
            "- Licence: %s" % c["licence"],
            "- File: `frontend/web/art/%s`" % out_name,
        ]
        if "note" in c:
            lines.append("- Note: %s" % c["note"])
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    print("Building reader art (max width %dpx)...\n" % MAX_W)
    mapping = build()
    built = sum(len(v) for v in mapping.values())
    unused = sum(1 for *_p, chapter, _r, _c in PLATES if chapter is None)
    if built + unused != len(PLATES):
        print("\n%d of %d plates were built (%d intentionally unused) -- "
              "check the SKIP lines above." % (built, len(PLATES), unused))

    notice = ROOT / "NOTICE.md"
    text = notice.read_text()
    marker = "## Reader artwork"
    if marker in text:
        text = text[:text.index(marker)]
    notice.write_text(text.rstrip("\n") + "\n" + notice_block())
    print("\nNOTICE.md updated.")

    map_path = ROOT / "frontend" / "web" / "art" / "map.json"
    import json
    map_path.write_text(json.dumps(mapping, indent=2, sort_keys=True) + "\n")
    print("Wrote %s (%d entries)." % (map_path, len(mapping)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
