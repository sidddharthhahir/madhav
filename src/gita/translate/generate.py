"""Generate Hindi and Gujarati translations via the Message Batches API.

Mirrors src/gita/enrich/generate.py's shape deliberately: same three-command
flow (submit / status / collect), same reasons (batches run up to 24h, the
batch id is persisted before anything else so a dead shell doesn't orphan a
paid job). The output target differs -- this writes into the `texts` table
(lang='hi'/'gu', source_key='derived') rather than the `enrichment` table.
"""

import datetime as dt
import json

from .. import db
from ..retrieval import corpus
from . import prompt as P

DEFAULT_MODEL = "claude-haiku-4-5"

# Two short translations, not a five-field JSON record -- much less output
# than enrichment needs. Still budget real headroom for thinking tokens.
MAX_TOKENS = 4000

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
            "format": {"type": "json_schema", "schema": P.TRANSLATION_SCHEMA},
        },
    }
    if effort:
        params["output_config"]["effort"] = effort
    return params


def pending_verse_ids(conn, records) -> list[str]:
    done_hi = {r[0] for r in conn.execute(
        "SELECT verse_id FROM texts WHERE lang='hi' AND source_key='derived'")}
    done_gu = {r[0] for r in conn.execute(
        "SELECT verse_id FROM texts WHERE lang='gu' AND source_key='derived'")}
    done = done_hi & done_gu  # only fully-done verses (both languages) count
    return [vid for vid in records if vid not in done]


LATIN_TPC = 0.25
DEVA_TPC = 0.60   # also used as the Gujarati-script estimate: no public
                  # per-script token-density figures for Gujarati exist, and
                  # it is a similar-complexity abugida, so this is the same
                  # kind of estimate as the Devanagari one, not a measurement.

# Two short verse translations plus thinking. Output here is much smaller than
# enrichment's five-field JSON record -- a translated verse is typically
# 100-250 characters per language, not a paragraph.
OUTPUT_CHARS_PER_LANG = 220
ASSUMED_THINKING_TOKENS = 400


def _token_estimate(text: str) -> float:
    import unicodedata
    deva = sum(1 for ch in text if "DEVANAGARI" in unicodedata.name(ch, ""))
    return deva * DEVA_TPC + (len(text) - deva) * LATIN_TPC


def estimate_cost(records, verse_ids, model: str,
                   thinking_tokens: int = ASSUMED_THINKING_TOKENS) -> dict:
    """Pre-flight estimate. Unmeasured -- run a small calibration batch before
    trusting this, exactly as CONTINUE.md documents for enrichment: the
    enrichment estimate was off by ~3.4x on Haiku specifically because assumed
    thinking-token volume didn't match what Haiku actually produced.
    """
    if not verse_ids:
        return {"requests": 0}
    sample = [records[v] for v in verse_ids]
    n = len(sample)

    sys_tok = _token_estimate(P.SYSTEM_PROMPT)
    user_tok = sum(_token_estimate(P.build_user_turn(r)) for r in sample)
    out_tokens = n * (OUTPUT_CHARS_PER_LANG * 2 * DEVA_TPC + thinking_tokens)

    cached_in = user_tok + sys_tok * 1.25 + sys_tok * max(n - 1, 0) * 0.1

    price_in, price_out = PRICE_PER_MTOK.get(model, (5.00, 25.00))
    cost = (cached_in / 1e6 * price_in
            + out_tokens / 1e6 * price_out) * BATCH_DISCOUNT
    return {
        "requests": n,
        "est_input_tokens": int(cached_in),
        "est_output_tokens": int(out_tokens),
        "est_usd": round(cost, 2),
        "note": ("assumes %d thinking tokens/verse (billed as output, 5x "
                 "input) and ~%d chars/language output; UNMEASURED -- run a "
                 "small calibration batch and read usage.output_tokens before "
                 "trusting this, same lesson as the enrichment estimate"
                 % (thinking_tokens, OUTPUT_CHARS_PER_LANG)),
    }


def _to_custom_id(verse_id: str) -> str:
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
        """INSERT INTO translate_batches
             (batch_id, model, prompt_hash, verse_ids, submitted_at, status)
           VALUES (?, ?, ?, ?, ?, 'submitted')""",
        (batch.id, model, P.prompt_hash(), json.dumps(verse_ids), _now()),
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
    client = _client()
    batch = client.messages.batches.retrieve(batch_id)
    if batch.processing_status != "ended":
        raise RuntimeError(
            "batch %s is %s, not ended" % (batch_id, batch.processing_status))

    written = 0
    invalid: list[str] = []
    unparsable: list[str] = []
    errored: list[str] = []
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

        problems = P.validate_translation(data)
        if problems:
            invalid.append("%s:%s" % (verse_id, "; ".join(problems)))
            continue

        db.upsert_text(conn, verse_id, "hi", "derived",
                        "Claude (derived translation)", "translation",
                        data["hindi"].strip(), "derived")
        db.upsert_text(conn, verse_id, "gu", "derived",
                        "Claude (derived translation)", "translation",
                        data["gujarati"].strip(), "derived")
        written += 1

    stats = {"written": written, "invalid": invalid, "errored": errored,
              "unparsable": unparsable}
    conn.execute(
        """UPDATE translate_batches
              SET collected_at=?, status=?, stats=?
            WHERE batch_id=?""",
        (_now(), "collected", json.dumps(stats, ensure_ascii=False), batch_id),
    )
    conn.commit()
    return stats


def load_records(db_path=None):
    conn = db.connect(db_path or db.DEFAULT_DB)
    return conn, corpus.load_verses(conn)
