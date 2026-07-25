"""Enrichment CLI.

    python -m gita.enrich.run --dry-run --limit 2   # render a prompt, cost only
    python -m gita.enrich.run --submit              # create the batch
    python -m gita.enrich.run --status              # poll
    python -m gita.enrich.run --collect             # write results

--dry-run needs no credentials and no network; use it to inspect the exact
prompt and the cost estimate before spending anything.
"""

import argparse
import json
import sys

from . import generate as G
from . import prompt as P


def _latest_batch(conn):
    return conn.execute(
        """SELECT batch_id, status, submitted_at, model FROM enrich_batches
            ORDER BY submitted_at DESC LIMIT 1"""
    ).fetchone()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build the enrichment layer.")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true",
                      help="render prompts and estimate cost; no API calls")
    mode.add_argument("--submit", action="store_true")
    mode.add_argument("--status", action="store_true")
    mode.add_argument("--collect", action="store_true")
    ap.add_argument("--limit", type=int, help="cap the number of verses")
    ap.add_argument("--model", default=G.DEFAULT_MODEL)
    ap.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"],
                    help="output_config.effort; omit for the API default")
    ap.add_argument("--batch-id", help="target a specific batch")
    ap.add_argument("--db", default=None)
    ap.add_argument("--yes", action="store_true", help="skip the submit confirmation")
    args = ap.parse_args(argv)

    conn, records = G.load_records(args.db)

    if args.status or args.collect:
        batch_id = args.batch_id
        if not batch_id:
            row = _latest_batch(conn)
            if not row:
                print("no batch on record; run --submit first")
                return 1
            batch_id = row["batch_id"]
        if args.status:
            for key, value in G.status(batch_id).items():
                print("  %-18s %s" % (key, value))
            return 0
        stats = G.collect(conn, batch_id)
        print("collected batch %s" % batch_id)
        print("  written    %d" % stats["written"])
        for label in ("invalid", "errored", "unparsable"):
            items = stats[label]
            print("  %-10s %d" % (label, len(items)))
            for item in items[:10]:
                print("      %s" % item)
            if len(items) > 10:
                print("      ... and %d more" % (len(items) - 10))
        return 0 if stats["written"] and not stats["errored"] else 1

    pending = G.pending_verse_ids(conn, records)
    if args.limit:
        pending = pending[: args.limit]

    if not pending:
        print("every verse already has enrichment; nothing to do")
        return 0

    est = G.estimate_cost(records, pending, args.model)
    print("prompt hash : %s" % P.prompt_hash())
    print("model       : %s" % args.model)
    print("effort      : %s" % (args.effort or "(API default)"))
    print("pending     : %d verses" % len(pending))
    for key in ("est_input_tokens", "est_output_tokens", "est_usd"):
        print("%-12s: %s" % (key, est[key]))
    print("note        : %s" % est["note"])

    if args.dry_run:
        rec = records[pending[0]]
        turn = P.build_user_turn(rec)
        print("\n--- rendered user turn for %s (%d chars) ---" % (rec.verse_id, len(turn)))
        print(turn[:1200] + ("\n  ... [truncated]" if len(turn) > 1200 else ""))
        print("\n--- output schema fields ---")
        for name, spec in P.ENRICHMENT_SCHEMA["properties"].items():
            print("  %-11s %-7s %s" % (name, spec["type"], spec["description"][:70] + "..."))
        return 0

    if not args.yes:
        print("\nThis submits %d requests (~$%s). Re-run with --yes to proceed."
              % (len(pending), est["est_usd"]))
        return 0

    batch_id = G.submit(conn, records, pending, model=args.model, effort=args.effort)
    print("\nsubmitted batch %s (%d requests)" % (batch_id, len(pending)))
    print("poll with:   python -m gita.enrich.run --status")
    print("then:        python -m gita.enrich.run --collect")
    return 0


if __name__ == "__main__":
    sys.exit(main())
