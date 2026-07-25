"""Exercise the HTTP API in-process via FastAPI's TestClient.

Covers the routes that need no credential (health, preview, search, verse) plus
the failure contract on /ask when none is configured -- a 200 with ok=false and
a machine-readable status, not a 500.

    python scripts/test_api.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from gita.api.app import _state, app  # noqa: E402

failures = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global failures
    if not condition:
        failures += 1
    print("  [%s] %s%s" % ("PASS" if condition else "FAIL", label,
                           "" if condition else "  <- " + str(detail)[:160]))


def main() -> int:
    with TestClient(app) as client:
        print("GET /health")
        r = client.get("/health")
        check("200", r.status_code == 200, r.text)
        body = r.json()
        check("701 verses", body.get("verses") == 701, body)
        check("reports enrichment mode", "mode" in body, body)

        print("\nGET /search")
        r = client.get("/search", params={"q": "fear of dying", "k": 5})
        check("200", r.status_code == 200, r.text)
        hits = r.json()["hits"]
        check("returns hits", len(hits) > 0, r.text)
        check("hits carry matched terms", all("terms" in h for h in hits), hits)

        print("\nPOST /preview")
        r = client.post("/preview", json={"question": "why am I so angry", "k": 4})
        check("200", r.status_code == 200, r.text)
        body = r.json()
        check("<= 4 verses", len(body["retrieved"]) <= 4, body["retrieved"])
        check("citable allowlist present", body["citable"].startswith("[BG"),
              body["citable"])
        check("context is non-empty", len(body["context"]) > 500,
              len(body["context"]))

        print("\nGET /verse/{id}")
        r = client.get("/verse/BG.2.47")
        check("200", r.status_code == 200, r.text)
        body = r.json()
        check("has sanskrit", bool(body.get("sanskrit")), body.keys())
        check("has >=2 english translations", len(body["translations"]) >= 2,
              list(body["translations"]))

        print("\nGET /verse/{id} for a verse outside the recension")
        r = client.get("/verse/BG.13.36")
        check("404", r.status_code == 404, r.status_code)

        print("\nPOST /ask with no credential configured")
        import os
        had = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            r = client.post("/ask", json={"question": "why am I so angry"})
            check("200 not 500", r.status_code == 200, "%s %s" % (r.status_code, r.text))
            body = r.json()
            check("ok is false", body["ok"] is False, body)
            check("status is no_credentials",
                  body["status"] == "no_credentials", body.get("status"))
            check("plan not leaked in response", "plan" not in body, list(body))
        finally:
            if had:
                os.environ["ANTHROPIC_API_KEY"] = had
            # /ask now logs every attempt via record_history() (see the fix
            # for the dead history feature) -- this test runs against the
            # real committed corpus via TestClient(app), not a fixture, so it
            # has to remove the row it just created rather than leaving a
            # "why am I so angry" / no_credentials entry in data/gita.sqlite3
            # every time this suite runs.
            pipeline = _state.get("pipeline")
            if pipeline is not None:
                pipeline.conn.execute(
                    "DELETE FROM history WHERE question=? AND status='no_credentials'",
                    ("why am I so angry",),
                )
                pipeline.conn.commit()

        print("\nPOST /ask input validation")
        r = client.post("/ask", json={"question": ""})
        check("422 on empty question", r.status_code == 422, r.status_code)
        r = client.post("/ask", json={"question": "hi", "k": 99})
        check("422 on out-of-range k", r.status_code == 422, r.status_code)

        print("\nGET /openapi.json")
        r = client.get("/openapi.json")
        check("200", r.status_code == 200, r.status_code)
        paths = r.json()["paths"]
        for route in ("/ask", "/preview", "/health", "/search", "/verse/{verse_id}"):
            check("documents %s" % route, route in paths, list(paths))

    print()
    if failures:
        print("%d FAILURE(S)" % failures)
        return 1
    print("All API tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
