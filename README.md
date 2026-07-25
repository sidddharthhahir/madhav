# Gita Wisdom — Backend

Cited retrieval over the Bhagavad Gita. You ask a question about your own life;
you get an answer grounded in specific verses, and every citation is verified
against what was actually retrieved before the answer is returned.

Not a fine-tuned model. The value of `[BG 3.37]` is that it points at a real
verse that really makes that point — a fine-tune would produce confident,
unverifiable citations instead.

## Layout

```
src/gita/
  canon.py               recension handling, verse-id scheme
  sources.py             per-field rights policy for upstream translations
  db.py                  SQLite schema
  pipeline.py            understand -> retrieve -> ground -> answer
  ingest/                vedicscriptures fetch + cache + rights filter
  retrieval/             IAST normalisation, BM25, corpus construction, RRF
  enrich/                the bridge layer (Batch API)
  answer/                context assembly, prompts, generation, citation validator
  api/                   FastAPI surface
scripts/                 CLIs and test suites
eval/questions.json      labelled questions for recall measurement
data/gita.sqlite3        the store (22 MB, populated)
```

## Setup

### macOS / Linux

```bash
git clone https://github.com/sidddharthhahir/madhav.git
cd madhav
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn --app-dir src gita.api.app:app --reload
```

Open <http://127.0.0.1:8000>. **No ingest step needed** — the corpus
(`data/gita.sqlite3`, 701 verses) is committed, so a fresh clone has the full
text immediately with no network access required.

The virtualenv is deliberately *not* committed: it holds platform-specific
binaries and Windows-layout paths (`.venv/Scripts/` rather than `.venv/bin/`),
so a committed one would be broken on macOS rather than helpful.

### Windows

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\uvicorn --app-dir src gita.api.app:app --reload
```

On Windows the Anthropic SDK ships filenames long enough to hit the 260-char
path limit under the Microsoft Store Python's `site-packages`. A project-local
venv keeps paths short; installing globally there fails mid-write and leaves a
broken package.

### Rebuilding the corpus (optional)

Ingestion, retrieval and the citation validator are **stdlib-only** and need
nothing installed. To rebuild the database from the upstream API instead of
using the committed copy:

```bash
python -m gita.ingest.run       # re-fetches all 701 verses, a few minutes
python scripts/verify_store.py  # 12 integrity checks
```

## Corpus

701 verses (Gita Press recension — chapter 13 has **35** verses, not 34).
Sanskrit and IAST transliteration, two complete English translations
(Purohit Swami, Sivananda), Sivananda's English commentary on all 701, and 14
ancient Sanskrit commentaries. 11,216 texts.

```bash
python -m gita.ingest.run          # full corpus; cached, so re-runs are free
python scripts/verify_store.py     # independent integrity check
```

`verify_store.py` re-derives every assertion from the database rather than
importing the ingester's own reporting, so a bug in the ingester cannot vouch
for itself.

### Rights

The upstream repo is MIT licensed, but that covers its *code*, not the
copyright in each translation it bundles. `sources.py` enforces a **per-field**
policy: for Śaṅkara and Rāmānuja the ancient Sanskrit commentary is ingested
while the uncredited modern English rendering beside it is dropped.

Excluded entirely: Prabhupāda (BBT, in copyright until 2038), Tejomayananda,
Chinmayananda, Ramsukhdas, Gambirananda, Adidevananda, Sankaranarayan. The raw
HTTP cache keeps the *unfiltered* payloads so exclusions can be re-audited
without re-crawling; only the SQLite store is filtered, which is what makes it
redistributable.

These are India life+60 determinations and a defensible reading, not legal
advice. Have counsel confirm before shipping commercially.

## Retrieval

```bash
python scripts/search.py "why am I always angry"
python scripts/search.py --health
python scripts/search.py --eval                      # recall@k against eval set
python scripts/search.py --explain BG.3.37 "desire becomes anger"
```

Transliteration normalisation is load-bearing. The corpus writes "Kṛṣṇa"; users
type "Krishna". Plain Unicode folding gives `krsna` and matches nothing, so
`normalize.py` maps IAST to the common English romanisation first
(`kṛṣṇa`→`krishna`, `dhṛtarāṣṭra`→`dhritarashtra`).

## The enrichment layer

**This is the component that decides whether the product works.** Retrieval
does not search verse text — it searches an English description of the human
situations each verse speaks to. A question about resenting a stranger online
shares no vocabulary with a verse about *dvandva-moha*.

Measured baseline without it: **recall@8 of 1/20 full, 16/20 complete misses.**

```bash
python -m gita.enrich.run --dry-run     # renders prompts, estimates cost, free
python scripts/cost_audit.py            # low/expected/high bounds
python -m gita.enrich.run --submit --limit 20 --yes   # calibration, ~$0.54
python -m gita.enrich.run --status
python -m gita.enrich.run --collect
```

Runs on the Message Batches API: 701 requests, no latency requirement, 50% off,
and the system prompt caches across every request. Expect **~$19** on Opus 5
(range $11–$35 — the spread is thinking tokens, which bill at the output rate).
Calibrate with 20 verses before committing.

Three commands, not one, deliberately: a batch can take up to 24 hours, and a
blocking script that died mid-wait would orphan a paid job. The batch id is
written to SQLite before anything else happens.

## Asking

```bash
python scripts/ask.py --preview "why do I resent people I've never met"  # free
python scripts/ask.py "why do I resent people I've never met"
python scripts/ask.py --health
```

`--preview` runs retrieval and context assembly with **no model calls**. It is
the fastest way to tell a retrieval problem from a generation problem.

## The citation guarantee

Every answer is validated before return. Two distinct failures:

- `nonexistent` — the verse isn't in the corpus (`[BG 2.99]`). Always a bug.
- `out_of_context` — the verse exists but was never retrieved for this
  question, so the model is citing from memory rather than from evidence.

On rejection the model is re-prompted with the offending citations named
explicitly (a generic "follow the rules" retry reproduces the same error), up to
two retries. If it still fails, the pipeline **returns a failure and withholds
the text** rather than serving unverified output.

## HTTP API

```bash
.venv/Scripts/uvicorn --app-dir src gita.api.app:app --reload
```

| Route | Needs a key | Purpose |
|---|---|---|
| `GET /health` | no | corpus and index coverage |
| `GET /search?q=` | no | raw lexical search with matched terms |
| `POST /preview` | no | retrieval + grounding context, free |
| `GET /verse/{id}` | no | one verse with translations and enrichment |
| `POST /ask` | yes | the full pipeline |

`/ask` returns HTTP 200 with `ok: false` and a machine-readable `status`
(`no_credentials`, `off_topic`, `no_verses`, `citation_validation_failed`,
`refused`) rather than a 500.

## Tests

```bash
python scripts/verify_store.py    # 12 corpus integrity checks
python scripts/test_validator.py  # 15 citation-validator cases
python scripts/test_pipeline.py   # 22 end-to-end checks against a stub client
python scripts/test_api.py        # 21 HTTP contract checks
```

`test_pipeline.py` stubs the Anthropic client, so the reject-and-regenerate
loop — the most safety-critical path, and the one that never runs during happy-
path use — is driven deterministically with no credential and no spend.

## Credentials

Set `ANTHROPIC_API_KEY`, or run `ant auth login`. Only enrichment and `/ask`
need one; ingestion, retrieval, `--preview`, and all four test suites do not.

## State

Done: corpus, rights policy, retrieval, enrichment pipeline, answer generation,
citation validation, HTTP API, four test suites, eval harness.

Not done: enrichment not yet generated (needs a key — this is the next step and
the one that moves the 5% baseline). Hindi and Gujarati not yet ingested —
Gandhi's *Anasaktiyoga* for Gujarati, derived Hindi. Dense retrieval is designed
for (`reciprocal_rank_fusion` is written and unused) but not wired.

## Known issues

- The Sivananda commentary carries OCR damage from the source scans: commas
  rendered as question marks, occasional broken words. Translations are clean;
  this affects only the commentary field.
- Cost estimates use character-class heuristics for token counts, and assume a
  thinking-token volume. Calibrate against a real batch before trusting them.
- The eval set is 20 questions. It should be ~100 before recall numbers carry
  real weight.
