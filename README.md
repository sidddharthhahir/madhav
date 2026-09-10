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
