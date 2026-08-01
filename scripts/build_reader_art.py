"""Optimise the reader's curated artwork and write NOTICE.md entries.

Run once, offline, against the raw downloads -- nothing here touches the
network or a model. Committed output is the resized WebP files plus the
attribution block; the raw originals are NOT committed.

    python scripts/build_reader_art.py
"""

import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
RAW = Path("/private/tmp/claude-501/-Users-siddharth-Madhav/"
           "9185102b-fe6f-4519-aad3-da2ec4d06d74/scratchpad/art_raw")
OUT = ROOT / "frontend" / "web" / "art"

# Full-bleed behind text on a phone-to-desktop range. 1600px covers a 1600px
# CSS width at 1x or an 800px width at 2x; past that the reader scales the
# image down anyway.
MAX_W = 1600

# The museum-sourced plates from the previous pass (Razmnama, Navagunjara,
# Karna confrontation, two Krishna-teaching miniatures, Vishvarupa print,
# Govardhan) are retired outright, not kept as `unused` -- explicit user
# request ("remove all the images that we have right now"), and their
# research trail lives in git history rather than as permanent NOTICE.md
# archaeology. What ships now is exactly two images, supplied directly by
# the project owner rather than sourced from a museum collection: they are
# AI-generated illustrations, not public-domain scans, and are documented as
# such below -- no museum, artist or PD claim is made for them because none
# would be true.
#
# (raw filename, art slot filename, chapter key, credit block)
# chapter key is an int (that chapter only), "default" (every chapter with
# no more specific entry), or None (documented but not wired up).
PLATES = [
    ("dialogue_sacred_geometry.png", "krishna-arjuna-dialogue.webp", "default", {
        "title": "Krishna and Arjuna in dialogue on the field",
        "kind": "AI-generated illustration",
        "note": ("Supplied directly by the project owner for use in this "
                 "reader. Used as the default background for every chapter "
                 "that has no more specific plate -- i.e. every chapter "
                 "except 11, which gets the Vishvarupa image below."),
    }),
    ("vishvarupa_galaxy.png", "vishvarupa-cosmic-form.webp", 11, {
        "title": "Vishvarupa, the cosmic form",
        "kind": "AI-generated illustration",
        "note": ("Supplied directly by the project owner for use in this "
                 "reader. Chapter 11 only -- the one chapter this specific "
                 "image was made for."),
    }),
]


def build() -> dict[str, list[str]]:
    """chapter key -> ordered list of art files.

    A list because a key can carry more than one plate (not the case today,
    but `artFor()` on the JS side already spreads a list across a chapter's
    verses, so the shape stays ready for that without a format change).
    """
    OUT.mkdir(parents=True, exist_ok=True)
    mapping: dict[str, list[str]] = {}
    for raw_name, out_name, chapter, _credit in PLATES:
        src = RAW / raw_name
        if not src.exists():
            print("  SKIP (not found): %s" % raw_name)
            continue
        im = Image.open(src).convert("RGB")
        if im.width > MAX_W:
            h = round(im.height * MAX_W / im.width)
            im = im.resize((MAX_W, h), Image.LANCZOS)
        dest = OUT / out_name
        im.save(dest, "WEBP", quality=85, method=6)
        kb = dest.stat().st_size / 1024
        where = "chapter %s" % chapter if chapter is not None else "unused"
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
        "Background art in the immersive reader (`GET /read/{chapter}` ->",
        "`frontend/web/art/`). Two AI-generated illustrations, supplied directly",
        "by the project owner for use in this app -- not sourced from a museum",
        "or archive, so no public-domain or attribution claim is made for them.",
        "Resized to %dpx on the long edge and re-encoded as WebP." % MAX_W,
        "",
        "An earlier pass used five museum-sourced public-domain paintings",
        "instead; retired outright on user feedback rather than kept as",
        "`unused` entries here. Their sourcing and licence verification are in",
        "git history (see the commits touching this file before this one), not",
        "duplicated as permanent documentation for art the app no longer ships.",
        "",
    ]
    for raw_name, out_name, chapter, c in PLATES:
        where = ("Chapter %d" % chapter if isinstance(chapter, int)
                 else "Default (every chapter without a more specific plate)"
                 if chapter == "default" else "Not used in the app")
        lines += [
            "**%s**" % c["title"],
            "- %s" % where,
            "- %s" % c["kind"],
            "- File: `frontend/web/art/%s`" % out_name,
        ]
        if "note" in c:
            lines.append("- Note: %s" % c["note"])
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    print("Building reader art (max width %dpx)...\n" % MAX_W)
    mapping = build()

    notice = ROOT / "NOTICE.md"
    text = notice.read_text()
    marker = "## Reader artwork"
    if marker in text:
        text = text[:text.index(marker)]
    notice.write_text(text.rstrip("\n") + "\n" + notice_block())
    print("\nNOTICE.md updated.")

    map_path = OUT / "map.json"
    map_path.write_text(json.dumps(mapping, indent=2, sort_keys=True) + "\n")
    print("Wrote %s (%d entries)." % (map_path, len(mapping)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
