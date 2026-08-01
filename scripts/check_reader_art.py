"""Consistency check between the reader's art directory, its map, and NOTICE.md.

Three files have to agree and nothing enforces that automatically:
frontend/web/art/*.webp (what actually exists), map.json (what the reader will
request), and NOTICE.md (what is documented and licensed). It is easy for
these to drift -- deleting an unused plate during cleanup left exactly one
dangling NOTICE.md reference during development, caught only by eye. This
makes that check repeatable.

    python scripts/check_reader_art.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "frontend" / "web" / "art"
NOTICE = ROOT / "NOTICE.md"

failures = 0


def check(label, ok, detail=""):
    global failures
    if not ok:
        failures += 1
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", label,
                           "" if ok else "  <- " + str(detail)[:200]))


def main() -> int:
    if not ART.exists():
        print("No frontend/web/art/ directory -- nothing to check "
              "(the reader works with zero art; this is not a failure).")
        return 0

    map_path = ART / "map.json"
    check("map.json exists", map_path.exists())
    if not map_path.exists():
        return 1
    mapping = json.loads(map_path.read_text())

    files_on_disk = {p.name for p in ART.glob("*.webp")}
    files_in_map = {f for files in mapping.values() for f in files}
    notice_text = NOTICE.read_text() if NOTICE.exists() else ""

    print("Chapters mapped")
    for ch, files in sorted(mapping.items(), key=lambda kv: int(kv[0])):
        check("chapter %s is 1-18" % ch, 1 <= int(ch) <= 18, ch)
        check("chapter %s has at least one plate" % ch, len(files) > 0, files)

    print("\nEvery file map.json points at exists on disk")
    for f in sorted(files_in_map):
        check(f, f in files_on_disk)

    print("\nEvery .webp on disk is documented in NOTICE.md")
    # Matched on the relative path as written in the credit block, not the
    # bare filename -- a name appearing anywhere in the file (e.g. in this
    # script's own docstring, if that were ever pasted in) would be a false
    # pass.
    for f in sorted(files_on_disk):
        ref = "frontend/web/art/%s" % f
        check(f, ref in notice_text, "no NOTICE.md entry references %s" % ref)

    print("\nEvery documented file actually exists")
    # Catches the inverse: a NOTICE.md entry for art that was since deleted,
    # which would misrepresent the repo as containing licensed art it does not.
    import re
    for ref in re.findall(r"frontend/web/art/(\S+\.webp)", notice_text):
        check(ref, ref in files_on_disk, "NOTICE.md references a missing file")

    print()
    if failures:
        print("%d FAILURE(S)" % failures)
        return 1
    print("Art, map.json and NOTICE.md agree.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
