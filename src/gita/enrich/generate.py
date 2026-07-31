"""Generate the enrichment layer via the Message Batches API.

Enrichment is a one-time, 701-request, embarrassingly parallel job with no
latency requirement -- exactly what the Batches API is for. It runs at 50% of
standard token price, and the shared system prompt caches across every request
in the batch, so the per-verse cost is essentially the verse text plus output.

The flow is deliberately three commands rather than one blocking call:

    submit  -> create the batch, record its id in SQLite
    status  -> poll
    collect -> stream results, validate, write rows

Batches can take up to 24 hours. A single blocking script that dies mid-wait
would orphan a paid batch, which is why the id is persisted before anything
else happens.
"""

import datetime as dt
import json

from .. import db
from ..retrieval import corpus
from . import prompt as P

DEFAULT_MODEL = "claude-opus-5"

# Enrichment output is a small JSON object, but thinking is on by default on
# claude-opus-5 and max_tokens caps thinking plus response together -- so this
# needs headroom well beyond the size of the JSON itself.
MAX_TOKENS = 8000

# Batch pricing is 50% of standard. Standard claude-opus-5 is $5/$25 per MTok.
PRICE_PER_MTOK = {"claude-opus-5": (5.00, 25.00),
                  "claude-opus-4-8": (5.00, 25.00),
                  "claude-sonnet-5": (3.00, 15.00),
                  "claude-haiku-4-5": (1.00, 5.00)}
BATCH_DISCOUNT = 0.5


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _client():
    import anthropic
    return anthropic.Anthropic()


def build_params(rec, model: str, effort: str | None):
    """Request params for one verse.

    cache_control sits on the system block so the ~500-token instruction set is
    written once and read by the other 700 requests.
    """
    params = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": [{
            "type": "text",
            "text": P.SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }],
        "messages": [{"role": "user", "content": P.build_user_turn(rec)}],
        "output_config": {
            "format": {"type": "json_schema", "schema": P.ENRICHMENT_SCHEMA},
        },
    }
    if effort:
        params["output_config"]["effort"] = effort
    return params


def pending_verse_ids(conn, records) -> list[str]:
    done = {r[0] for r in conn.execute("SELECT verse_id FROM enrichment")}
    return [vid for vid in records if vid not in done]


# Tokens per character. Latin prose is the familiar ~0.25 (4 chars/token);
# Devanagari is much denser in tokens, so counting it at the Latin rate
# materially under-estimates input. ~10% of these prompts are Devanagari.
LATIN_TPC = 0.25
DEVA_TPC = 0.60

# Output is the JSON record PLUS thinking. Thinking is on by default on
# claude-opus-5 and bills at the OUTPUT rate -- 5x input -- so omitting it was
# the single largest error in the first version of this estimator. Thinking
# volume for a given prompt is not knowable without running it; calibrate with
# a small batch and read usage.output_tokens.
JSON_OUT_TOKENS = 900
ASSUMED_THINKING_TOKENS = 1200


def _token_estimate(text: str) -> float:
    import unicodedata
    deva = sum(1 for ch in text if "DEVANAGARI" in unicodedata.name(ch, ""))
    return deva * DEVA_TPC + (len(text) - deva) * LATIN_TPC


def estimate_cost(records, verse_ids, model: str,
                  thinking_tokens: int = ASSUMED_THINKING_TOKENS) -> dict:
    """Pre-flight estimate with the thinking-token assumption made explicit.

    Still an estimate. Character-class heuristics stand in for the real
    tokenizer, and thinking volume is assumed rather than measured. Run
    scripts/cost_audit.py for low/expected/high bounds, and calibrate against a
    small real batch before trusting any single figure.
    """
    if not verse_ids:
        return {"requests": 0}
    sample = [records[v] for v in verse_ids]
    n = len(sample)

    sys_tok = _token_estimate(P.SYSTEM_PROMPT)
    user_tok = sum(_token_estimate(P.build_user_turn(r)) for r in sample)
    out_tokens = n * (JSON_OUT_TOKENS + thinking_tokens)

    # System block is byte-identical across requests: written once, read n-1 times.
    cached_in = user_tok + sys_tok * 1.25 + sys_tok * max(n - 1, 0) * 0.1

    price_in, price_out = PRICE_PER_MTOK.get(model, (5.00, 25.00))
    cost = (cached_in / 1e6 * price_in
            + out_tokens / 1e6 * price_out) * BATCH_DISCOUNT
    return {
        "requests": n,
        "est_input_tokens": int(cached_in),
        "est_output_tokens": int(out_tokens),
        "est_usd": round(cost, 2),
        "note": ("assumes %d thinking tokens/verse (billed as output, 5x input); "
                 "run scripts/cost_audit.py for bounds" % thinking_tokens),
    }


def _to_custom_id(verse_id: str) -> str:
    # Batch API custom_id must match ^[a-zA-Z0-9_-]{1,64}$ -- verse ids like
    # "BG.1.1" contain dots, which that pattern rejects. Encode losslessly;
    # verse ids never contain underscores, so this round-trips exactly.
    return verse_id.replace(".", "_")


def _from_custom_id(custom_id: str) -> str:
    return custom_id.replace("_", ".")


def submit(conn, records, verse_ids, *, model=DEFAULT_MODEL, effort=None) -> str:
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    requests = [
        Request(
            custom_id=_to_custom_id(vid),
            params=MessageCreateParamsNonStreaming(
                **build_params(records[vid], model, effort)
            ),
        )
        for vid in verse_ids
    ]
    batch = _client().messages.batches.create(requests=requests)

    conn.execute(
        """INSERT INTO enrich_batches
             (batch_id, model, prompt_hash, verse_ids, submitted_at, status)
           VALUES (?, ?, ?, ?, ?, 'submitted')""",
        (batch.id, model, P.prompt_hash(),
         json.dumps(verse_ids), _now()),
    )
    conn.commit()
    return batch.id


def status(batch_id: str) -> dict:
    batch = _client().messages.batches.retrieve(batch_id)
    counts = batch.request_counts
    return {
        "batch_id": batch.id,
        "processing_status": batch.processing_status,
        "succeeded": counts.succeeded,
        "errored": counts.errored,
        "processing": counts.processing,
        "canceled": counts.canceled,
        "expired": counts.expired,
    }


def collect(conn, batch_id: str) -> dict:
    """Stream a finished batch into the enrichment table.

    Results arrive in arbitrary order, so every row is keyed by custom_id --
    never by position. Getting that wrong would silently attach each verse's
    enrichment to a different verse, and the corpus would still look complete.
    """
    client = _client()
    batch = client.messages.batches.retrieve(batch_id)
    if batch.processing_status != "ended":
        raise RuntimeError(
            "batch %s is %s, not ended" % (batch_id, batch.processing_status))

    row = conn.execute(
        "SELECT model, prompt_hash FROM enrich_batches WHERE batch_id=?", (batch_id,)
    ).fetchone()
    model = row["model"] if row else "unknown"
    phash = row["prompt_hash"] if row else P.prompt_hash()

    written = 0
    invalid: list[str] = []
    trimmed: list[str] = []
    errored: list[str] = []
    unparsable: list[str] = []
    generated_at = _now()

    for result in client.messages.batches.results(batch_id):
        verse_id = _from_custom_id(result.custom_id)
        if result.result.type != "succeeded":
            errored.append("%s:%s" % (verse_id, result.result.type))
            continue

        text = next((b.text for b in result.result.message.content
                     if b.type == "text"), None)
        if not text:
            unparsable.append("%s:no-text-block" % verse_id)
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            unparsable.append("%s:%s" % (verse_id, exc.msg))
            continue

        # Trim overflow rather than discard the record; only genuine breakage
        # blocks a write.
        record, notes = P.normalise_enrichment(data)
        if notes:
            trimmed.append("%s:%s" % (verse_id, "; ".join(notes)))

        problems = P.validate_enrichment(record)
        if problems:
            invalid.append("%s:%s" % (verse_id, "; ".join(problems)))
            continue

        conn.execute(
            """INSERT INTO enrichment (verse_id, summary, themes, situations,
                                       emotions, stance, keywords, model,
                                       prompt_hash, generated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(verse_id) DO UPDATE SET
                 summary=excluded.summary, themes=excluded.themes,
                 situations=excluded.situations, emotions=excluded.emotions,
                 stance=excluded.stance,
                 keywords=excluded.keywords, model=excluded.model,
                 prompt_hash=excluded.prompt_hash,
                 generated_at=excluded.generated_at""",
            (verse_id, record["summary"],
             json.dumps(record["themes"], ensure_ascii=False),
             json.dumps(record["situations"], ensure_ascii=False),
             json.dumps(record["emotions"], ensure_ascii=False),
             json.dumps(record.get("stance", []), ensure_ascii=False),
             json.dumps(record["keywords"], ensure_ascii=False),
             model, phash, generated_at),
        )
        written += 1

    stats = {"written": written, "invalid": invalid, "trimmed": trimmed,
             "errored": errored, "unparsable": unparsable}
    conn.execute(
        """UPDATE enrich_batches
              SET collected_at=?, status=?, stats=?
            WHERE batch_id=?""",
        (_now(), "collected", json.dumps(stats, ensure_ascii=False), batch_id),
    )
    conn.commit()
    return stats


def load_records(db_path=None):
    conn = db.connect(db_path or db.DEFAULT_DB)
    return conn, corpus.load_verses(conn)
