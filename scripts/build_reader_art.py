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
# Assigned per CHAPTER, not per verse: applied to every scene in that chapter
# until a later plate targets a specific verse, so one plate covers many
# screens instead of decorating a single one.
#
# Chapter 1 was rebuilt on direct user feedback: the two original plates
# (Razmnama, Navagunjara -- still below, now UNUSED) were replaced rather than
# supplemented. The Razmnama folio genuinely carries Persian calligraphy --
# it is a Mughal-era Persian translation of the Mahabharata, not a rendering
# fault -- which read as "I don't know where that is and what the language is
# about" to someone opening a Sanskrit Gita reader: accurate to its own
# tradition, wrong context here. Navagunjara depicts Krishna as a nine-animal
# composite beast from an unrelated Mahabharata episode, which reads as
# bizarre rather than devotional without the story behind it. Both were
# honestly labelled ASSOCIATED at the time, and that label was the warning
# sign that they were the wrong choice, not a hedge that made them fine to
# ship.
PLATES = [
    ("karna_confront_full.jpg", "bg-1-karna-confrontation.webp", 1, "depicts", {
        "title": "Arjuna and His Charioteer Krishna Confront Karna",
        "date": "c. 1820",
        "medium": "Opaque watercolor on cloth",
        "source": "Philadelphia Museum of Art, via Wikimedia Commons",
        "url": "https://commons.wikimedia.org/wiki/File:Arjuna_and_His_Charioteer_Krishna_Confront_Karna.jpg",
        "licence": "Public Domain Mark 1.0 / PD-Art (PD-old-100) / PD-India",
        "note": ("The two armies arrayed and the chariots drawn up between "
                 "them, essentially illustrating BG 1.20-27 -- the moment "
                 "Krishna draws the chariot into the middle of the field at "
                 "Arjuna's request, just before the despair the chapter is "
                 "named for."),
    }),
    ("krishna_arjuna_gita.jpg", "bg-2-krishna-teaching.webp", 2, "depicts", {
        "title": "Krishna and Arjuna on the field of Kurukshetra",
        "date": "c. 1830",
        "medium": "Gouache on paper, from an album of seventy paintings of Hindu deities",
        "source": "British Museum, via Wikimedia Commons",
        "url": "https://commons.wikimedia.org/wiki/File:Krishna_Arjuna_Gita.jpg",
        "licence": "Public Domain Mark 1.0 -- published before 1931",
    }),
    ("kashmir_gita_upadesh.jpg", "bg-2-gita-upadesh.webp", 2, "depicts", {
        "title": "Sri Krishna preaching Gita Upadesh to Arjun",
        "date": "c. 1875-1900",
        "medium": "Pahari miniature painting (Kashmir school), natural pigments on paper",
        "source": "Google Cultural Institute, via Wikimedia Commons",
        "url": "https://commons.wikimedia.org/wiki/File:Sri_Krishna_preaching_Gita_Upadesh_to_Arjun_-_Unknown,_Kashmir_School_-_Google_Cultural_Institute.jpg",
        "licence": "Public Domain Mark 1.0",
    }),
    ("vishvarupa_print.jpg", "bg-11-vishvarupa.webp", 11, "depicts", {
        "title": "Vishvarupa, the cosmic form",
        "date": "early-to-mid 20th century (≤ 1940)",
        "medium": "Devotional print, style associated with the Ravi Varma press",
        "source": "Columbia University Bhagavad Gita digital collection, via Wikimedia Commons",
        "url": "https://commons.wikimedia.org/wiki/File:Vishvaprint4max.jpg",
        "licence": "Public domain in India and the United States",
    }),
    # UNUSED as of the chapter-1 rebuild above -- kept documented, not deleted,
    # for the same reason govardhan is kept: the research and the licence
    # verification are real even where the editorial fit was not.
    ("razmnama_dhritarashtra.jpg", "unused-razmnama-dhritarashtra.webp", None, "unused", {
        "title": "Dhritarashtra Attacks the Statue of Bhima, folio from a Razmnama",
        "date": "c. 1616–17",
        "medium": "Opaque color and gold on paper",
        "source": "The Metropolitan Museum of Art (Howard Hodgkin Collection)",
        "url": "https://www.metmuseum.org/art/collection/search/825591",
        "licence": "Public domain (Met Open Access)",
        "note": ("Retired from chapter 1 on direct user feedback: the folio "
                 "carries genuine Persian calligraphy (a Mughal-era Persian "
                 "translation of the Mahabharata), which read as unrelated "
                 "and confusing behind a Sanskrit Gita reader rather than as "
                 "the different-manuscript-tradition nuance it actually is."),
    }),
    ("navagunjara.jpg", "unused-navagunjara.webp", None, "unused", {
        "title": "Navagunjara, a universal form of Krishna",
        "date": "c. 1835",
        "medium": "Opaque watercolor, ink, and gold on paper",
        "source": "The Metropolitan Museum of Art (Purchase, Evelyn Kranes Kossak Gift, 2006)",
        "url": "https://www.metmuseum.org/art/collection/search/73296",
        "licence": "Public domain (Met Open Access)",
        "note": ("Retired from chapter 1 on direct user feedback: a nine-"
                 "animal composite creature from an unrelated Mahabharata "
                 "episode reads as bizarre rather than devotional without "
                 "the story behind it -- the ASSOCIATED label this shipped "
                 "with was the warning sign, not a hedge that made it fine."),
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
