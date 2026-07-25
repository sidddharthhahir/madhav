"""Honest cost bounds for the enrichment job. No API calls, no spend.

The estimator in generate.py uses chars/4 as a token proxy. That heuristic is
calibrated on English prose and is wrong here in three separate ways:

  1. The prompts contain Devanagari. Non-Latin scripts tokenize far less
     efficiently than 4 chars/token -- closer to 1-2 -- so input is UNDER-counted.
  2. Thinking is on by default on claude-opus-5, and thinking tokens bill at
     the OUTPUT rate. The 900-token output guess counted only the JSON, so
     output is UNDER-counted, and output costs 5x input.
  3. Prompt caching is not credited. The system block is identical across all
     701 requests, so it should bill once at write rates and 700x at read
     rates, which pushes input DOWN.

(1) and (2) dominate. This script produces a low/expected/high range instead of
a single number that reads more precise than it is.
"""

import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gita.enrich import generate as G  # noqa: E402
from gita.enrich import prompt as P  # noqa: E402

# Tokens per character. Latin prose is the familiar ~0.25 (4 chars/token).
# Devanagari is far denser in tokens; 0.45-0.75 brackets the plausible range.
LATIN_TPC = 0.25
DEVA_TPC = {"low": 0.45, "expected": 0.60, "high": 0.75}

# Output = the JSON record, plus thinking tokens (billed at the output rate).
JSON_OUT = 900
THINKING = {"low": 300, "expected": 1200, "high": 3000}

PRICE_IN, PRICE_OUT = 5.00, 25.00      # claude-opus-5, per MTok
BATCH = 0.5                            # Batch API discount
CACHE_WRITE, CACHE_READ = 1.25, 0.1    # multipliers vs base input rate


def classify(text: str) -> tuple[int, int]:
    """Split a string into (devanagari_chars, other_chars)."""
    deva = sum(1 for ch in text if "DEVANAGARI" in unicodedata.name(ch, ""))
    return deva, len(text) - deva


def main() -> int:
    conn, records = G.load_records()
    verse_ids = list(records)

    sys_deva, sys_other = classify(P.SYSTEM_PROMPT)
    user_deva = user_other = 0
    for vid in verse_ids:
        d, o = classify(P.build_user_turn(records[vid]))
        user_deva += d
        user_other += o

    n = len(verse_ids)
    print("Enrichment cost audit -- claude-opus-5, Batch API")
    print("=" * 62)
    print("requests                : %d" % n)
    print("system prompt chars     : %d (%d devanagari)" % (sys_deva + sys_other, sys_deva))
    print("user turn chars (total) : %d" % (user_deva + user_other))
    print("  devanagari            : %d (%.1f%%)"
          % (user_deva, 100 * user_deva / (user_deva + user_other)))
    print("  latin/other           : %d" % user_other)
    print()

    print("%-10s %12s %12s %10s %10s" % ("scenario", "in_tokens", "out_tokens", "no-cache", "cached"))
    print("-" * 62)
    results = {}
    for band in ("low", "expected", "high"):
        tpc = DEVA_TPC[band]
        sys_tok = sys_deva * tpc + sys_other * LATIN_TPC
        user_tok = user_deva * tpc + user_other * LATIN_TPC
        in_tok = user_tok + sys_tok * n
        out_tok = n * (JSON_OUT + THINKING[band])

        plain = (in_tok / 1e6 * PRICE_IN + out_tok / 1e6 * PRICE_OUT) * BATCH

        # With caching the system block is written once, read n-1 times.
        cached_in = (
            user_tok
            + sys_tok * CACHE_WRITE
            + sys_tok * (n - 1) * CACHE_READ
        )
        cached = (cached_in / 1e6 * PRICE_IN + out_tok / 1e6 * PRICE_OUT) * BATCH

        results[band] = cached
        print("%-10s %12d %12d %9.2f %9.2f"
              % (band, int(in_tok), int(out_tok), plain, cached))

    print()
    print("Realistic range (with prompt caching): $%.2f - $%.2f, expect ~$%.2f"
          % (results["low"], results["high"], results["expected"]))
    print("generate.py's naive estimate         : $9.34")
    print()
    print("Dominant uncertainty is thinking tokens: they bill at the output")
    print("rate ($25/MTok, $12.50 batched) and are 5x input. The only way to")
    print("pin this down is to run one real batch of ~20 verses and read")
    print("usage.output_tokens off the results -- roughly $0.30 to find out.")
    print()
    print("Nothing has been spent. This script makes no API calls.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
