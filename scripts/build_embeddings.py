"""Compute and cache dense embeddings for the corpus via a local Ollama server.

    python scripts/build_embeddings.py             # embed whatever is missing
    python scripts/build_embeddings.py --force      # re-embed every verse

Requires the Ollama app running locally with `nomic-embed-text` pulled. No API
key, no per-query cost -- see src/gita/retrieval/dense.py.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gita.retrieval import corpus, dense  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true",
                     help="re-embed every verse, not just missing ones")
    ap.add_argument("--model", default=dense.MODEL)
    ap.add_argument("--db", default=None)
    args = ap.parse_args(argv)

    conn, _, records = corpus.open_index(args.db)
    existing = {} if args.force else dense.load_embeddings(conn)
    pending = {vid: corpus.dense_text(rec) for vid, rec in records.items()
               if vid not in existing}

    if not pending:
        print("all %d verses already embedded with %s" % (len(records), args.model))
        return 0

    print("embedding %d verses with %s (skipping %d already cached)..."
          % (len(pending), args.model, len(existing)))
    ids = list(pending)
    texts = [pending[vid] for vid in ids]
    vectors = dense.embed(texts, model=args.model)
    dense.store_embeddings(conn, dict(zip(ids, vectors)), model=args.model)
    print("done -- %d embedded" % len(pending))
    return 0


if __name__ == "__main__":
    sys.exit(main())
