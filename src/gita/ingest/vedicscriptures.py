"""Ingest Sanskrit, transliteration and rights-cleared commentary from
vedicscriptures.github.io.

Every payload is cached to disk on first fetch so re-runs cost nothing and the
rights policy can be re-applied without re-downloading. The cache holds the raw
upstream response including denied fields; only the SQLite store is filtered.
That separation is deliberate -- it lets us re-audit what we chose to exclude
without a second crawl, while keeping the redistributable artifact clean.
"""

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from .. import canon, sources

BASE = "https://vedicscriptures.github.io"
USER_AGENT = "gita-wisdom-ingest/0.1 (+local research build)"
CACHE = Path(__file__).resolve().parents[3] / "data" / "cache" / "vedicscriptures"

# Upstream is GitHub Pages static JSON, so it is cheap to read, but we stay
# polite anyway. Cached verses skip the sleep entirely.
DELAY_SECONDS = 0.15


class FetchError(RuntimeError):
    pass


def _get(url: str, retries: int = 3) -> dict:
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise FetchError("%s failed after %d attempts: %s" % (url, retries, last))


def fetch_chapter(chapter: int, *, use_cache: bool = True) -> dict:
    path = CACHE / "chapter" / ("%d.json" % chapter)
    if use_cache and path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    data = _get("%s/chapter/%d" % (BASE, chapter))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    time.sleep(DELAY_SECONDS)
    return data


def fetch_verse(chapter: int, verse: int, *, use_cache: bool = True) -> dict:
    path = CACHE / "slok" / ("%d_%d.json" % (chapter, verse))
    if use_cache and path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    data = _get("%s/slok/%d/%d" % (BASE, chapter, verse))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    time.sleep(DELAY_SECONDS)
    return data


def chapter_verse_counts(*, use_cache: bool = True) -> dict[int, int]:
    """Ask upstream how many verses each chapter has.

    We do not assume 700 -- see canon.py. Whatever upstream reports becomes the
    shape we ingest, and the caller validates it against the known recensions.
    """
    counts = {}
    for ch in canon.CHAPTERS:
        meta = fetch_chapter(ch, use_cache=use_cache)
        n = meta.get("verses_count") or meta.get("verseCount") or meta.get("verses")
        if not isinstance(n, int):
            raise FetchError(
                "chapter %d: could not read verse count from keys %s"
                % (ch, sorted(meta))
            )
        counts[ch] = n
    return counts


def _clean(value) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.replace(" ", " ").strip()
    return text or None


def extract(payload: dict) -> tuple[dict, list[dict], set[str]]:
    """Split one upstream verse payload into (verse, permitted texts, seen keys).

    Denied fields are dropped here and never reach the database.
    """
    verse_row = {
        "sanskrit": _clean(payload.get("slok")),
        "transliteration": _clean(payload.get("transliteration")),
    }

    texts: list[dict] = []
    seen: set[str] = set()

    for key, block in payload.items():
        if not isinstance(block, dict):
            continue
        seen.add(key)
        policy = sources.POLICIES.get(key)
        for fld in ("et", "ht", "ec", "hc", "sc"):
            body = _clean(block.get(fld))
            if body is None:
                continue
            if not sources.permitted(key, fld):
                continue
            texts.append({
                "lang": sources.FIELD_LANG[fld],
                "source_key": key,
                "translator": (policy.translator if policy
                               else block.get("author") or key),
                "kind": sources.kind_of(fld),
                "body": body,
            })

    return verse_row, texts, seen
