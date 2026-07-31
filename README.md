# Madhav

Cited retrieval over the Bhagavad Gita. You ask a question about your own life;
you get an answer grounded in specific verses, and every citation is verified
against what was actually retrieved before the answer is returned.

Not a fine-tuned model. The value of `[BG 3.37]` is that it points at a real
verse that really makes that point — a fine-tune would produce confident,
unverifiable citations instead.

**Picking this up on a new machine?** Start with [CONTINUE.md](CONTINUE.md) —
setup, current state, the next actions in order with costs, and the traps
already hit.

## Layout

```
src/gita/
  canon.py               recension handling, verse-id scheme
  sources.py             per-field rights policy for upstream translations
  db.py                  SQLite schema
  pipeline.py            understand -> retrieve -> ground -> answer
  ingest/                vedicscriptures fetch + cache + rights filter
  retrieval/             IAST normalisation, BM25, corpus construction, RRF, dense (Ollama)
  enrich/                the bridge layer (Batch API)
  answer/                context assembly, prompts, generation, citation validator
  api/                   FastAPI surface
scripts/                 CLIs and test suites
eval/questions.json      labelled questions for recall measurement
data/gita.sqlite3        the store (~30 MB, populated: corpus + enrichment + embeddings)
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
python scripts/verify_store.py  # 13 integrity checks
```

## Corpus

701 verses (Gita Press recension — chapter 13 has **35** verses, not 34).
Sanskrit and IAST transliteration, two complete English translations
(Purohit Swami, Sivananda), Sivananda's English commentary on all 701, 14
ancient Sanskrit commentaries, and Hindi + Gujarati translations for all 701
(machine-derived — see "Hindi and Gujarati" below). 12,618 texts.

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
advice. Have counsel confirm before shipping commercially. Full attribution
and the per-source basis: [NOTICE.md](NOTICE.md).

## Retrieval

```bash
python scripts/search.py "why am I always angry"
python scripts/search.py --health
python scripts/search.py --eval                      # recall@k, retrieval only, free
python scripts/search.py --eval --hybrid --real       # recall@k as the product actually behaves, costs ~$0.55
python scripts/search.py --explain BG.3.37 "desire becomes anger"
```

`--eval` alone measures retrieval on the raw question text — useful for fast,
free iteration, but not what `/ask` actually does. `--real` routes each
question through the same query-understanding call the real pipeline uses
before retrieving. The difference is large, and on the identical index: 17/106
full recall on raw question text against **55/106** through real query
understanding at the shipping defaults (k=20, fusion pool 30). Don't quote the
free number as the product's recall. `understand()` is a real,
non-deterministic call, so treat any single `--real` run as a measurement with
variance, not a constant.

Transliteration normalisation is load-bearing. The corpus writes "Kṛṣṇa"; users
type "Krishna". Plain Unicode folding gives `krsna` and matches nothing, so
`normalize.py` maps IAST to the common English romanisation first
(`kṛṣṇa`→`krishna`, `dhṛtarāṣṭra`→`dhritarashtra`).

## The enrichment layer

**This is the component that decides whether the product works.** Retrieval
does not search verse text — it searches an English description of the human
situations each verse speaks to. A question about resenting a stranger online
shares no vocabulary with a verse about *dvandva-moha*.

Measured baseline without it, over 106 questions: recall@8 of 5 full, 25
partial, 76 complete misses.

**Status: generated.** All 692 non-demo verses were enriched with Haiku 4.5 via
the Batch API (9 verses keep hand-written demo notes). Actual measured cost —
not the estimate — was **$1.10** for the full batch. With enrichment alone,
recall@8 moves to **7 full, 41 partial, 58 miss**; with enrichment plus dense
retrieval, **17/106 full** on raw question text — and **55/106 full, 38
partial, 13 miss** at the shipping defaults through the same query-
understanding step the real product actually uses. See "Dense retrieval" and "Retrieval" above for the
full before/after and why the raw-text number understates what ships.

```bash
python -m gita.enrich.run --dry-run     # renders prompts, estimates cost, free
python scripts/cost_audit.py            # low/expected/high bounds
python scripts/demo_enrichment.py       # hand-written notes for 9 verses, $0
python -m gita.enrich.run --submit --limit 20 --model claude-haiku-4-5 --yes
python -m gita.enrich.run --status
python -m gita.enrich.run --collect
sqlite3 data/gita.sqlite3 "SELECT model, COUNT(*) FROM enrichment GROUP BY model;"
```

`demo_enrichment.py` writes notes by hand for nine verses and re-measures: on
those questions retrieval goes from 1 of 11 expected verses to 7 of 11, with no
API calls. It demonstrates the mechanism; it is not a measurement, since the
verses were chosen because the eval expects them.

Runs on the Message Batches API: 701 requests, no latency requirement, 50% off,
and the system prompt caches across every request. Cost depends entirely on
model tier, because output — mostly thinking tokens — is the whole bill. The
table below is the pre-flight *estimate*, which assumes 1200 thinking
tokens/verse; the real Haiku run used almost none (~255 output tokens/verse
measured), so the estimate overstates Haiku's actual cost by roughly 3-4x —
run the 20-verse calibration and read `usage.output_tokens` rather than
trusting the table for anything but ballpark ordering:

| Model | Config | Estimated | Actually measured |
|---|---|---|---|
| Haiku 4.5 | no thinking (its default) | ~$3.70 | **$1.10** |
| Sonnet 5 | effort=low | ~$7.35 | not run |
| Opus 5 | default | ~$19.26 | not run |
| Opus 5 | effort=high | ~$35.04 | not run |

Three commands, not one, deliberately: a batch can take up to 24 hours, and a
blocking script that died mid-wait would orphan a paid job. The batch id is
written to SQLite before anything else happens.

## Dense retrieval (hybrid)

**Optional. Measured cost of skipping it: about one question in a hundred.**

| retrieval | full | partial | miss |
|---|---|---|---|
| BM25 + dense (Ollama running) | 55/106 | 38 | 13 |
| BM25 alone (no Ollama) | 54/106 | 37 | 15 |

Measured through `Pipeline.retrieve` on the 106-question eval. The app is
fully usable without Ollama: retrieval falls back to BM25 automatically, in
about 5ms, with no error and no hang — verified by pointing the client at a
dead port. Nothing else in the product depends on it. Install it if you want
the extra question or two; skip it and nothing breaks.

(The embeddings for the 701 verses ship inside the committed database. Ollama
is only needed to embed *your question* at query time, which is why the app
still works without it.)

BM25 is lexical: it needs shared vocabulary between the question and the
enrichment text. `reciprocal_rank_fusion` in `retrieval/bm25.py` fuses it with
a dense (embedding-similarity) ranking. Anthropic has no embeddings endpoint,
and paying a hosted embeddings API per query is hard to justify for 701 short
documents, so this runs entirely locally against [Ollama](https://ollama.com):

```bash
brew install --cask ollama            # or download from ollama.com
open -a Ollama
ollama pull nomic-embed-text
python scripts/build_embeddings.py    # embeds all 701 verses, free, ~1 minute
```

Then opt in with `--hybrid`:

```bash
python scripts/search.py --eval --hybrid
python scripts/ask.py --hybrid "why do I resent people I've never met"
```

Or leave it on by default for the API server — `src/gita/api/app.py` already
constructs the pipeline with `use_dense=True`. If Ollama isn't running or no
embeddings are cached, retrieval degrades silently to BM25 alone rather than
failing the request; `GET /health` reports `dense_index.active` so you can
tell which mode is actually in effect.

The text embedded for dense retrieval is deliberately narrower than what BM25
indexes (`corpus.dense_text` vs. `corpus.searchable_text`): a single pooled
embedding vector over a long, heterogeneous document — enrichment prose plus
literal translations plus word-by-word Sanskrit glosses — dilutes the
semantic signal. BM25 doesn't have this problem, since each term scores
independently.

`nomic-embed-text` (768-dim) outperformed the larger `mxbai-embed-large`
(1024-dim) in testing here — the latter needs an instruction-prefix convention
this codebase doesn't apply, and untuned it scored worse (4/106 full vs.
17/106). Don't switch models without re-running `--eval --hybrid` to confirm
the swap actually helps.

## Hindi and Gujarati

**Status: generated.** All 701 verses have Hindi and Gujarati translations
under `src/gita/sources.py`'s `derived` policy — machine-translated from the
Sanskrit original plus the two already-permitted English translations
(Purohit Swami, Sivananda), not adapted from any existing Hindi or Gujarati
edition. Neither Gita Press (Goyandka) nor Gandhi's *Anasaktiyoga* were usable
sources — both are two-column/scanned-page OCR with verse markers that don't
align to actual verse order; see CONTINUE.md §6 for what was tried.

```bash
python -m gita.translate.run --dry-run                                  # free
python -m gita.translate.run --submit --limit 20 --model claude-haiku-4-5 --yes
python -m gita.translate.run --status
python -m gita.translate.run --collect
python -m gita.translate.run --submit --model claude-haiku-4-5 --yes    # full run
```

Same shape as enrichment deliberately: both languages come out of a single
request per verse (not two), and the cost estimator carries the same warning
enrichment's did after being measured wrong — the pre-flight estimate for the
full 701-verse batch was $1.24; actual measured cost (from real
`usage.output_tokens`, not the estimator) was **$0.65**.

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

### What streaming changes, and what it doesn't

Citations can only be checked against a *finished* answer — a half-written
sentence has nothing to verify. So `/ask/stream` (what the UI uses) necessarily
shows text before it has been validated, which `/ask` never does. That is a
real difference, and it is why they are separate endpoints rather than one
endpoint with a flag: `/ask` keeps the strict all-or-nothing contract for any
caller that wants it.

What the streaming path preserves:

- Streamed text is rendered as visibly **unverified** — dimmed, behind a dashed
  rule, under a live "checking citations…" note — and citation markers stay
  plain text instead of becoming clickable pills.
- If a draft fails validation, a `reset` event fires and the client **discards
  everything shown so far** before the retry begins. A rejected draft is never
  left standing.
- Nothing is presented as a checked answer until `done` arrives. Only then is
  the text re-rendered as a finished article with citation pills.
- If every attempt fails, `failed` arrives and the text is withheld, exactly as
  in `/ask`.

Both properties — retracting a rejected draft, and withholding text that never
validates — are asserted in `scripts/test_pipeline.py` (cases 11 and 12) against
a stub client, since neither runs on the happy path.

## HTTP API

```bash
uvicorn --app-dir src gita.api.app:app --reload      # .venv\Scripts\uvicorn on Windows
```

| Route | Needs a key | Purpose |
|---|---|---|
| `GET /health` | no | corpus and index coverage |
| `GET /search?q=` | no | raw lexical search with matched terms |
| `POST /preview` | no | retrieval + grounding context, free |
| `GET /verse/{id}` | no | one verse with translations and enrichment |
| `POST /ask` | yes | the full pipeline, all-or-nothing |
| `POST /ask/stream` | yes | same pipeline as server-sent events (see above) |

`/ask` returns HTTP 200 with `ok: false` and a machine-readable `status`
(`no_credentials`, `off_topic`, `no_verses`, `citation_validation_failed`,
`refused`) rather than a 500.

## Tests

```bash
python scripts/verify_store.py    # 13 corpus integrity checks
python scripts/validate_eval.py   # eval-set sanity (verses exist, no over-used verse)
python scripts/test_prefixes.py   # OCR repair + clamp-not-discard
python scripts/test_validator.py  # 15 citation-validator cases
python scripts/test_pipeline.py   # 22 end-to-end checks against a stub client
python scripts/test_api.py        # 21 HTTP contract checks
python scripts/test_api_ui.py     # confirms the desktop UI's app.js calls the routes it needs
python scripts/eval_answers.py     # answer-shape regressions (costs ~25c, real API calls)
```

Run all seven in sequence:

```bash
for s in verify_store test_validator test_pipeline test_api test_api_ui \
         test_prefixes validate_eval; do python scripts/$s.py; done
```

`test_pipeline.py` stubs the Anthropic client, so the reject-and-regenerate
loop — the most safety-critical path, and the one that never runs during happy-
path use — is driven deterministically with no credential and no spend.

## Credentials

Set `ANTHROPIC_API_KEY`, or run `ant auth login`. Only enrichment and `/ask`
need one; ingestion, retrieval, `--preview`, and all four test suites do not.

## State

Done: corpus, rights policy, retrieval (BM25 + dense hybrid via local Ollama),
enrichment pipeline (generated, all 692 non-demo verses), Hindi and Gujarati
translations (all 701 verses, machine-translated from the Sanskrit + English
already in the corpus — not from Gita Press or Gandhi's *Anasaktiyoga*, see
CONTINUE.md §6 for why those don't work mechanically), answer generation,
citation validation, HTTP API, seven test suites, eval harness, code licence
([MIT](LICENSE) — covers the code only; the corpus text has its own per-source
basis in [NOTICE.md](NOTICE.md)).

Not done: nothing left from the original scope. Recall is now measured
correctly (see Known issues) at 55/106 full (k=20) — the remaining 20 misses
are the main open lever, mostly abstract/existential questions with little
concrete vocabulary for retrieval to grab onto.

## Known issues

- The Sivananda commentary carries OCR damage from the source scans: commas
  rendered as question marks, occasional broken words. Translations are clean;
  this affects only the commentary field.
- Cost estimates use character-class heuristics for token counts, and assume a
  thinking-token volume high enough to be misleading for Haiku specifically —
  see the enrichment section above. Calibrate against a real batch before
  trusting them for any given model.
- **`scripts/search.py --eval` without `--real` measures a different, easier
  question than "what does the product actually retrieve."** It runs
  retrieval on the raw question text; `Pipeline.ask()` never does that — it
  always rewrites the question toward corpus vocabulary via
  `answer.generate.understand()` first. On the identical index, raw-text
  recall is 17/106 full; through real query understanding it's **55/106
  (52%)** at the shipping defaults (k=20, fusion pool 30). Use
  plain `--eval --hybrid` for free, fast iteration on retrieval itself; use
  `--eval --hybrid --real` (costs ~$0.55, calls the understanding LLM per
  question) for the number that actually describes the product.
- `Pipeline`'s `max_verses` default is 20, not 8 — widening it recovered a
  meaningful chunk of recall (many misses were verses ranked 8-12, displaced
  by a thematically adjacent but differently-specific verse) for about 50%
  more context tokens per answer. A real but small cost increase; the
  citation validator still only allows citing what the model was shown.
- **`k` is now 20 and the browser no longer overrides it.** The frontend sent
  `k: 8` on every request, which silently overrode the server default -- so
  the earlier 8 -> 12 tuning never reached the UI at all. Removing it, and
  raising the default to 20, takes the shipped app from ~35 to **55/106 full**
  (11 misses). Measured on `Pipeline.retrieve` itself, not a reimplementation.
- Fusion now draws from a pool of 30 candidates per ranker rather than exactly
  `k`. At `pool == k`, RRF could only reorder verses both rankers already
  agreed on; a verse ranked 15th by BM25 and 3rd by dense was invisible.
  Depth here costs local sorting and no tokens, since only the top `k` are
  sent to the model.
- 13 nominal misses remain at k=20. Mostly abstract/existential questions
  ("am I my thoughts or something underneath them") where the phrasing itself
  carries little concrete vocabulary, unlike the concrete-situation questions
  dense retrieval handles well. Some are also a confirmed artifact of the
  eval set labelling only 2 expected verses per question when the Gita
  legitimately supports more — e.g. "I keep getting attached to outcomes I
  cannot control" retrieves the famous *nishkama karma* cluster (BG.5.12,
  BG.3.19, BG.2.51...) ahead of the more specific verse the eval expects
  (BG.2.62), which is a defensible ranking choice, not a failure. Not yet
  audited question-by-question to find out how much of the 20 this explains.
- Dense retrieval adds an **optional** runtime dependency on a local Ollama
  server. When it's absent the app falls back to BM25 in ~5ms with no error,
  and the measured difference is one question in 106 (see "Dense retrieval").
  `GET /health` reports `dense_index.ollama_reachable` if you want to know
  which mode is in effect; nothing is surfaced at query time because at that
  magnitude it would be noise.
