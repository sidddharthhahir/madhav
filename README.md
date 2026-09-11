# Madhav

[![tests](https://github.com/sidddharthhahir/madhav/actions/workflows/tests.yml/badge.svg)](https://github.com/sidddharthhahir/madhav/actions/workflows/tests.yml)

Cited Bhagavad Gita retrieval and answering with strict citation validation.

## Overview

Madhav helps users ask life questions and receive answers grounded in specific Gita verses. It emphasizes verifiable retrieval and citation integrity over unverifiable generated references.

## Key Features

- Grounded Q&A with verse-level citations
- Citation validation before returning final answers
- Retrieval pipeline with BM25 and optional dense hybrid mode
- Corpus-backed API with health, search, preview, and ask endpoints
- Rights-aware corpus handling and attribution notes

## Tech Stack

- Python 3
- FastAPI + Uvicorn
- SQLite
- Optional Ollama for dense query embeddings

## Setup and Run

### macOS / Linux

```bash
git clone https://github.com/sidddharthhahir/madhav.git
cd madhav
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn --app-dir src gita.api.app:app --reload
```

### Windows

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\uvicorn --app-dir src gita.api.app:app --reload
```

Open <http://127.0.0.1:8000> after startup.

## Deploying a public demo

[`render.yaml`](render.yaml) is a ready-to-go Blueprint — Render reads it
and provisions the service, build command, and public-demo env vars
automatically. Free tier, no credit card:

1. [render.com](https://render.com) → sign up (GitHub login is fastest)
2. **New** → **Blueprint** → select this repo → Render finds `render.yaml`
3. It'll prompt for two secrets it deliberately doesn't default:
   - `ANTHROPIC_API_KEY` — **leave this blank.** With no server key, `/ask`
     runs entirely on bring-your-own-key (see below) — visitors pay for
     their own answers, you pay nothing, ever. Only set this if you
     specifically want to fund answers yourself.
   - `MADHAV_TOKEN` — make one up anyway (e.g. a long random string). It's
     what would keep `/ask` private to you *if* you ever did add a server
     key later — required to start regardless, so it's there before you
     need it.
4. **Apply** — first deploy takes a few minutes (installs deps, no GPU/build
   step needed)

That's it — `MADHAV_PUBLIC_DEMO=1` is already set in the blueprint, so
`/search`, `/preview`, `/counterpoint`, `/dilemma`, `/read`, and `/chapters`
are live and usable by anyone immediately; `/ask` prompts each visitor for
their own key (see [Bring your own key](#public-demo-mode)) and never
touches yours. See [Public demo mode](#public-demo-mode) below for exactly
what `MADHAV_PUBLIC_DEMO` changes.

## Usage

```bash
python scripts/search.py "why am I always angry"
python scripts/ask.py --preview "why do I resent people I've never met"
python scripts/ask.py "why do I resent people I've never met"
python scripts/verify_store.py
```

API routes:

- `GET /health`
- `GET /search?q=`
- `POST /preview`
- `GET /verse/{id}`
- `POST /ask`
- `POST /ask/stream`

## Configuration

Optional environment variables:

- `ANTHROPIC_API_KEY` for `/ask` and `/ask/stream`
- `MADHAV_TOKEN` to require `X-Madhav-Token` on paid answer endpoints
- `MADHAV_ASK_PER_HOUR` to cap paid answers per client (default: `60`)
- `MADHAV_RERANK=1` to enable model-based reranking

Core retrieval routes (`/search`, `/preview`, `/counterpoint`, `/dilemma`, `/read`) work without API credentials.

### Public demo mode

Set `MADHAV_PUBLIC_DEMO=1` when deploying this somewhere reachable by anyone,
not just running it locally. It closes two gaps that don't matter for a
single-user desktop app but do matter on the public internet:

- **History and saved verses are shared, unisolated state** (one SQLite
  table, no per-visitor separation) — `/history` and `/saved` (all methods)
  respond `404` instead of exposing or letting strangers edit that state.
- **The free retrieval endpoints had no rate limit at all** — `/search`,
  `/preview`, `/counterpoint`, `/dilemma`, `/verse`, `/chapters`, `/read` are
  capped per-IP at `MADHAV_FREE_PER_MIN` (default `30`) once this is on.

`MADHAV_TOKEN` becomes **mandatory** in this mode — the app refuses to start
without it, since an unguarded `/ask` on a public URL means anyone who finds
it can spend your Anthropic API key. Local/self-hosted use is unaffected;
none of this activates unless `MADHAV_PUBLIC_DEMO` is set.

**Bring your own key.** A visitor can send their own Anthropic key as the
`X-Anthropic-Key` header — it funds their own question, is never logged or
persisted server-side, and bypasses the `X-Madhav-Token` requirement
entirely (there's nothing of yours left to protect once they're paying).
The web UI does this automatically: when `/ask` comes back
`no_credentials` — which it always will on a deploy with no server-side
`ANTHROPIC_API_KEY` — it shows an inline field for a visitor's own key,
saved to their browser's `localStorage` and reused from then on. This is
the intended way to run a fully public demo that costs you nothing: leave
`ANTHROPIC_API_KEY` unset entirely, set only `MADHAV_TOKEN` (kept private,
for your own use) and `MADHAV_PUBLIC_DEMO=1`, and every visitor who wants
answers brings their own key.

## Evaluation

Numbers below are reproduced by running the scripts in [Verification](#verification) against the committed corpus and eval set — nothing here is asserted without a script that checks it.

| | |
|---|---|
| Corpus | 701 verses, full text in English, Sanskrit, Hindi, and Gujarati, all embedded |
| Eval set | 106 questions across 18/18 chapters and 30 themes, validated against the corpus (`validate_eval.py`) |
| Citation validator | 9/9 adversarial cases correctly accepted or rejected — hallucinated verse numbers, hallucinated chapters, and valid-but-out-of-context citations are all caught (`test_validator.py`) |
| Reject-and-regenerate pipeline | 12/12 scenarios pass end-to-end against a stubbed model, including a first-attempt hallucination that gets corrected on retry, and the case where no draft ever validates and the answer is withheld rather than shipped (`test_pipeline.py`) |

The `/ask` and `/ask/stream` endpoints only return an answer once its citations have been checked against the corpus; if none validate, the API withholds the answer rather than returning an unverified one.

## Verification

Run the repository checks (all offline, no API credential required):

```bash
for s in verify_store test_validator test_pipeline test_api test_api_ui test_prefixes test_speakers test_rerank validate_eval; do python scripts/$s.py; done
```

CI runs the same checks on every push and pull request — see [`.github/workflows/tests.yml`](.github/workflows/tests.yml).

## Project Structure

```text
src/gita/
  api/            FastAPI app and routes
  answer/         answer generation and citation validation
  enrich/         enrichment pipeline
  ingest/         corpus ingestion
  retrieval/      search and ranking components
scripts/          utility scripts and test suites
data/             SQLite data store
eval/             evaluation dataset
frontend/         web UI
```

## Contributing

Contributions are welcome. Please open an issue for substantial changes and submit focused pull requests with clear descriptions.

## License / Contact

- Code license: [MIT](LICENSE)
- Corpus rights and attribution: [NOTICE.md](NOTICE.md)
- Contact: open an issue in this repository
