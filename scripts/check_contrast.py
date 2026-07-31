"""WCAG AA contrast check over every text/surface pair in the palette.

This exists because of a specific trap, recorded in CONTINUE.md: a muted grey
was verified against the page background, passed, and shipped -- while failing
against the slightly lighter button surface that actually sat under it. Checking
text against "the background" is not enough when an app has eleven surfaces.

So this enumerates the surfaces each text token can genuinely land on and
checks the whole cross product. It parses the real stylesheet rather than a
copy, so it cannot drift from what ships.

The app has one theme. This script previously covered four time-of-day
palettes; that check is gone with them, but it is exactly the check that
caught a 3.07:1 failure in the light theme before it was removed, so it stays
pointed at the one palette that remains.

    python scripts/check_contrast.py
    python scripts/check_contrast.py --verbose    # print every pair
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / "frontend" / "web" / "styles.css"

AA = 4.5          # normal text
AA_LARGE = 3.0    # >=18.66px bold or >=24px

# Which surfaces each text colour actually appears on. Derived by reading the
# rules, not guessed -- if a pairing is added in CSS it must be added here too,
# which is the point: the list is the claim being tested.
PAIRS = {
    "--gw-text":   ["--gw-page", "--gw-bg", "--gw-panel", "--gw-card",
                    "--gw-card-alt", "--gw-input", "--gw-btn", "--gw-btn-hover",
                    "--gw-hover-row", "--gw-palette", "--gw-warn-bg"],
    "--gw-text-2": ["--gw-page", "--gw-bg", "--gw-panel", "--gw-card", "--gw-card-alt"],
    "--gw-text-3": ["--gw-page", "--gw-bg", "--gw-panel", "--gw-card",
                    "--gw-card-alt", "--gw-btn", "--gw-hover-row", "--gw-warn-bg"],
    "--gw-text-4": ["--gw-page", "--gw-bg", "--gw-panel", "--gw-card"],
    "--gw-muted":  ["--gw-page", "--gw-bg", "--gw-panel", "--gw-card",
                    "--gw-card-alt", "--gw-btn", "--gw-hover-row", "--gw-palette",
                    "--gw-warn-bg"],
    # The accent carries links, citation pills, verse refs and the chapter
    # heading -- small text in every case, so it is held to full AA.
    "--gw-accent": ["--gw-page", "--gw-bg", "--gw-panel", "--gw-card",
                    "--gw-card-alt", "--gw-btn", "--gw-hover-row",
                    "--gw-pill-bg", "--gw-warn-bg"],
    "--gw-teal":   ["--gw-page", "--gw-bg", "--gw-panel", "--gw-card"],
    # Text printed ON the accent fill (the "New question" button).
    "--gw-on-accent": ["--gw-accent-fill"],
}

THEMES = [("the palette (:root)", [":root"])]


def srgb(c):
    c = c / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hexstr):
    h = hexstr.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * srgb(r) + 0.7152 * srgb(g) + 0.0722 * srgb(b)


def ratio(fg, bg):
    a, b = luminance(fg), luminance(bg)
    lo, hi = sorted((a, b))
    return (hi + 0.05) / (lo + 0.05)


def parse_block(css, pattern):
    m = re.search(pattern, css, re.S | re.M)
    if not m:
        return None
    return dict(re.findall(r"(--[\w-]+)\s*:\s*(#[0-9A-Fa-f]{3,8})\s*(?:;|$)",
                           m.group(1), re.M))


def main() -> int:
    verbose = "--verbose" in sys.argv
    css = CSS.read_text()

    blocks = {":root": parse_block(css, r"^:root\s*\{(.*?)^\}")}
    for name, blk in blocks.items():
        if not blk:
            print("FAIL: could not parse the %s palette from styles.css" % name)
            return 1

    failures = []
    for label, layers in THEMES:
        pal = {}
        for layer in layers:
            pal.update(blocks[layer])
        print("\n%s" % label)
        worst = (999, "")
        checked = 0
        for fg, backgrounds in PAIRS.items():
            if fg not in pal:
                failures.append("%s: %s is not defined" % (label, fg))
                continue
            for bg in backgrounds:
                if bg not in pal:
                    failures.append("%s: %s is not defined" % (label, bg))
                    continue
                r = ratio(pal[fg], pal[bg])
                checked += 1
                ok = r >= AA
                if r < worst[0]:
                    worst = (r, "%s on %s" % (fg, bg))
                if verbose or not ok:
                    print("  [%s] %-16s on %-20s %5.2f:1  (%s on %s)"
                          % ("PASS" if ok else "FAIL", fg, bg, r,
                             pal[fg], pal[bg]))
                if not ok:
                    failures.append("%s: %s on %s = %.2f:1 (need %.1f)"
                                    % (label, fg, bg, r, AA))
        print("  %d pairs checked, worst %.2f:1 (%s)" % (checked, worst[0], worst[1]))

    print()
    if failures:
        print("%d CONTRAST FAILURE(S):" % len(failures))
        for f in failures:
            print("  - %s" % f)
        return 1
    print("Palette passes WCAG AA (%.1f:1) on every text/surface pair." % AA)
    return 0


if __name__ == "__main__":
    sys.exit(main())
