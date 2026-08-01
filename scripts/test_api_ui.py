"""Test the new sidebar endpoints and the static UI mount.

    python scripts/test_api_ui.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from gita.api.app import app  # noqa: E402

failures = 0


def check(label, condition, detail=""):
    global failures
    if not condition:
        failures += 1
    print("  [%s] %s%s" % ("PASS" if condition else "FAIL", label,
                           "" if condition else "  <- " + str(detail)[:180]))


def main() -> int:
    with TestClient(app) as client:
        print("GET /chapters")
        r = client.get("/chapters")
        check("200", r.status_code == 200, r.text)
        ch = r.json()
        check("18 chapters", len(ch) == 18, len(ch))
        check("total is 701", sum(c["verse_count"] for c in ch) == 701,
              sum(c["verse_count"] for c in ch))
        check("chapter 13 has 35", next(c for c in ch if c["chapter"] == 13)["verse_count"] == 35,
              next(c for c in ch if c["chapter"] == 13))
        check("reports per-chapter enrichment", "enriched" in ch[0], list(ch[0]))

        print("\nGET /chapters/{n}")
        r = client.get("/chapters/2")
        check("200", r.status_code == 200, r.text)
        check("72 verses in chapter 2", len(r.json()) == 72, len(r.json()))
        check("404 on chapter 19", client.get("/chapters/19").status_code == 404)

        print("\nsaved verses round-trip")
        client.delete("/saved/BG.2.47")
        r = client.post("/saved", json={"verse_id": "BG.2.47", "note": "core"})
        check("POST 200", r.status_code == 200, r.text)
        listed = client.get("/saved").json()
        check("appears in list", any(s["verse_id"] == "BG.2.47" for s in listed), listed)
        check("carries chapter/verse",
              next(s for s in listed if s["verse_id"] == "BG.2.47")["verse"] == 47, listed)
        check("404 on unknown verse",
              client.post("/saved", json={"verse_id": "BG.2.99"}).status_code == 404)
        check("DELETE 200", client.delete("/saved/BG.2.47").status_code == 200)
        check("removed from list",
              not any(s["verse_id"] == "BG.2.47" for s in client.get("/saved").json()))

        print("\nGET /history")
        r = client.get("/history?limit=5")
        check("200", r.status_code == 200, r.text)
        check("is a list", isinstance(r.json(), list), type(r.json()))

        print("\nstatic UI mount")
        r = client.get("/")
        check("GET / serves html", r.status_code == 200 and "<div id=\"app\">" in r.text,
              r.status_code)
        check("titled Madhav", "Madhav" in r.text)
        for asset in ("/static/styles.css", "/static/app.js"):
            a = client.get(asset)
            check("%s 200" % asset, a.status_code == 200, a.status_code)
        css = client.get("/static/styles.css").text
        # Asserts the contrast-checked muted value for the cosmic palette.
        # The point is not the specific hex -- it is that --gw-muted is a
        # value someone actually measured against every surface it lands on,
        # which is the trap CONTINUE.md records (#8A857D passed on the canvas
        # and failed on the lighter button surface). Worst measured case is
        # 5.31:1, above the 4.5:1 AA floor. If the palette changes again,
        # re-measure with check_contrast.py -- do not delete the check.
        check("css has contrast-checked muted", "#9A9484" in css)
        check("css has focus-visible", "focus-visible" in css)
        check("css respects reduced motion", "prefers-reduced-motion" in css)

        # One theme. The light palettes, the four prahars and the toggle were
        # all removed; these assert the removal stayed complete rather than
        # leaving a half-wired switch behind.
        check("no light palette remains", "prefers-color-scheme: light" not in css)
        check("no theme variants remain", "data-theme" not in css)
        check("colour scheme is pinned dark", ":root { color-scheme: dark; }" in css)

        js = client.get("/static/app.js").text
        check("no theme preference is stored", "madhav-theme" not in js)
        check("no theme toggle remains", "btnTheme" not in js)
        # The verse-number marker is written with Devanagari numerals in the
        # Sanskrit and ASCII in the transliteration, so a \d-only strip cleans
        # one and silently leaves the other sitting mid-poem.
        check("reader strips Devanagari verse markers", "\\u0966-\\u096F" in js)
        check("reader is wired to /read/", "/read/" in js)
        # Regression: the reader used to be visible on every page load, not
        # only after the book button was clicked. `.reader { display: flex }`
        # is an author-stylesheet rule, and origin beats specificity in the
        # cascade -- an author rule of normal importance always wins over the
        # browser's own `[hidden] { display: none }`, regardless of selector
        # weight or source order. Toggling the `hidden` PROPERTY in JS
        # therefore did nothing visible; `.reader[hidden]` must exist to win
        # back the override. Confirmed live: `hidden` was true on the element
        # while computed display was still "flex". Caught by a user report,
        # not by any existing check -- this is a rendering/cascade bug no
        # server-side test can see, so it is pinned here structurally.
        check("reader[hidden] wins the cascade over .reader's own display",
              ".reader[hidden]" in css and
              css.index(".reader[hidden]") > css.index(".reader {"),
              "the .reader{display:flex} rule must be followed by a "
              ".reader[hidden]{display:none} override, or the reader "
              "renders on top of the whole app from page load")
        for path in ("/preview", "/ask", "/verse/", "/chapters", "/health", "/search"):
            check("app.js calls %s" % path, path in js)

    print()
    if failures:
        print("%d FAILURE(S)" % failures)
        return 1
    print("All UI-integration tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
