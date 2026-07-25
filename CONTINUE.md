# Continuing on another machine

Written as a handoff. Everything below is the current state, the next actions in
order, and the traps already hit so they are not hit again.

---

## 1. Get it running (macOS / Linux)

```bash
git clone https://github.com/sidddharthhahir/madhav.git
cd madhav
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn --app-dir src gita.api.app:app --reload
```

Open <http://127.0.0.1:8000>.

**No ingest step.** `data/gita.sqlite3` is committed, so the clone arrives with
all 701 verses. You need no network and no API key to browse chapters, read
verses, or run retrieval.

Confirm the corpus survived the trip:

```bash
python scripts/verify_store.py     # expect 11 PASS, exit 0
```

Run everything:

```bash
for s in verify_store test_validator test_pipeline test_api test_api_ui \
         test_prefixes validate_eval; do python scripts/$s.py; done
```

127 assertions total, all passing as of commit `9cb10f8`.

---

## 2. Where the project actually stands

**Built and tested:** corpus with a per-field rights policy, BM25 retrieval with
IAST folding, enrichment pipeline (Batch API), two-stage answer generation,
citation validation with reject-and-regenerate, FastAPI, desktop UI on live
data, 106-question eval set, seven test suites.

**The one thing blocking a working product:** enrichment is not generated.
Retrieval sits at **recall@8 of 5/106 full, 25 partial, 76 miss**.

**Also missing:** Hindi and Gujarati (0/701 each), PNG/PDF export, code licence.

### Nothing here has ever called the Anthropic API

Worth stating plainly, because every figure in this repo is a local computation
or hand-written test data:

- the 5/106 baseline — real, measured by BM25 locally
- the cost table ($1.75 to $35) — character heuristics with an *assumed*
  thinking-token count, not measurements
- the 1-of-11 to 7-of-11 enrichment demo — hand-written notes, and circular by
  construction since the verses were chosen because the eval expects them
- the citation validator — only ever exercised against a stub client

That last point is why step 3 is a 3-cent test, not the enrichment batch.

---

## 3. Next actions, in this order

```bash
export ANTHROPIC_API_KEY="sk-ant-..."      # add to ~/.zshrc to persist
```

### a. Prove the answer path works — about 3 cents

```bash
python scripts/ask.py "why do I resent people I have never met online"
```

Do this **first**. It is the only thing that has never touched the real API, and
it exercises structured-output conformance, context assembly, generation, and
the citation validator in one shot.

The risk it is checking for: if real model output phrases citations in a way the
validator's regex misses, every answer fails validation and burns three attempts
retrying. Cheaper to learn that on one question than after a batch.

Expected: an answer with `[BG x.y]` citations, `attempts: 1`, and
`validation: OK`. If you see `citation_validation_failed`, the regex in
`src/gita/answer/validate.py` needs widening — the answer text is in the failure
detail.

### b. Calibrate enrichment cost — about 5 cents

```bash
python -m gita.enrich.run --dry-run                                    # free
python -m gita.enrich.run --submit --limit 20 --model claude-haiku-4-5 --yes
python -m gita.enrich.run --status                                     # poll
python -m gita.enrich.run --collect
```

Batches can take up to 24 hours, though 20 requests usually land in minutes. The
batch id is written to SQLite before anything else, so nothing is orphaned if
the shell dies.

`--collect` reports `written`, `trimmed`, `invalid`, `errored`, `unparsable`.
Then read the notes it produced:

```bash
sqlite3 data/gita.sqlite3 \
  "SELECT verse_id, summary FROM enrichment WHERE prompt_hash <> 'demo' LIMIT 3;"
```

**This is the decision point.** If Haiku's notes name concrete modern
situations in plain language, run the full batch on Haiku for ~$1.75. If they
are vague or generic, spend 54 cents on the same 20 verses with
`--model claude-opus-5` and compare before committing to ~$19.

### c. Full enrichment run

```bash
python -m gita.enrich.run --submit --model <whichever won> --yes
python -m gita.enrich.run --status
python -m gita.enrich.run --collect
```

### d. Measure — this is the number that matters

```bash
python scripts/search.py --eval
```

Baseline to beat: **5/106 full, 25 partial**. If this does not move
substantially, enrichment quality is the problem, not the architecture — reread
the prompt in `src/gita/enrich/prompt.py`, particularly the `situations` field,
which is what carries retrieval.

If it improves but not enough, the next lever is dense retrieval.
`reciprocal_rank_fusion` in `src/gita/retrieval/bm25.py` is written and unused,
waiting for a second ranker. Note Anthropic has no embeddings endpoint, so that
means Voyage, OpenAI, or a local model.

### e. Then Hindi and Gujarati

Both are now **derived** from the public-domain Sanskrit and English already in
the corpus — see §6 for why Gita Press and Gandhi are not options. Fold into the
same batch pattern; no extra source ingestion needed.

---

## 4. Traps already hit

Each of these cost real debugging time.

| Trap | Detail |
|---|---|
| **XML comments cannot contain `--`** | SVG is XML. A double hyphen inside a comment is a hard parse error and the file renders *nothing*, silently, while still serving HTTP 200. Broke `logo.svg` once. |
| **SQLite cannot cross threads** | FastAPI dispatches sync endpoints to a worker threadpool. A connection opened at startup fails on first request. Fixed by loading everything into memory at construction; `check_same_thread=False` is only a backstop. |
| **SDK auth error fires at request time** | The Anthropic SDK raises a bare `TypeError` when building request headers, *not* when constructing the client. Wrapping only the constructor leaves a raw traceback escaping. Both sites are wrapped in `answer/generate.py`. |
| **Checkpoint WAL before committing the database** | Otherwise the commit captures a half-written file. `PRAGMA wal_checkpoint(TRUNCATE)` first. |
| **Never commit `data/cache/`** | 34 MB of unfiltered upstream payloads including Prabhupāda's translation. Public repo. `.gitignore` explains why; do not "helpfully" re-add it. |
| **Corporate TLS proxies break Python but not PowerShell** | On the Windows machine, `urllib` could not reach archive.org (self-signed cert in chain) while `Invoke-WebRequest` could, because they use different trust stores. If a download fails on Mac with `CERTIFICATE_VERIFY_FAILED`, this is why. |
| **`.dc.html` is not standalone** | `frontend/Madhav.dc.html` needs `support.js` beside it to render `{{ }}` bindings. It is a design reference, not the app. The real UI is `frontend/web/`. |
| **Windows 260-char path limit** | Irrelevant on Mac, but it is why a project-local venv exists rather than a global install: the Anthropic SDK ships filenames long enough to break the Store Python's `site-packages`. |

---

## 5. Decisions still open

**Code licence.** None chosen. `NOTICE.md` covers the corpus only and grants
nothing over the code. MIT if others should build on it; omit entirely to keep
it closed. Worth settling before the repo gets attention.

**Enrichment model.** Haiku 4.5 (~$1.75) or Opus 5 (~$19). Step 3b answers this
with output rather than opinion. The task is paraphrase and brainstorm, not
reasoning, which is the argument for Haiku — but enrichment quality permanently
caps retrieval quality, which is the argument for Opus.

---

## 6. Things previously recommended that turned out wrong

Recorded so they are not retried.

**Gita Press (Goyandka) for Hindi.** The rights position is genuinely fine —
Goyandka died 1965, so public domain in India since 1 January 2026, and this is
distinct from Ramsukhdas (d. 2005), who stays excluded. But the only available
form is archive.org OCR over two-column page scans, and the columns are read out
of order, so verse numbers interleave. **Five editions tested; the best aligned
1 of 18 chapters, the rest zero.** `src/gita/ingest/gitapress.py` is kept
because the segmenter and its validation are correct and would work on a
single-column scan — the source fails, not the code. Recommending this before
checking marker *order* (rather than just marker *count*) was the mistake.

**Gandhi's Anasaktiyoga for Gujarati.** Never tested. It is also a page scan
(ProofreadPage over `Anashakti Yog.pdf`), so it likely has the same problem.
Test before planning around it.

**`#8A857D` for dark-theme muted text.** Validated against the canvas only,
where it passes at 4.96:1 — but it is also used on the lighter button surface,
where it is 4.10:1 and fails WCAG AA. Corrected to `#948F86`. Check every
background a colour actually appears on.

**Cost estimates.** First figure was $9.34, which omitted thinking tokens
entirely — they bill at the output rate and are most of the cost on Opus. Real
expectation is ~$19. Corrected in `src/gita/enrich/generate.py`, but it is still
an estimate.

---

## 7. Useful commands

```bash
# retrieval only, no API calls, free
python scripts/ask.py --preview "why am I always angry"
python scripts/search.py "fear of dying" -k 8
python scripts/search.py --explain BG.3.37 "desire becomes anger"
python scripts/search.py --health

# demonstrate what enrichment does, no API calls
python scripts/demo_enrichment.py
python scripts/demo_enrichment.py --revert

# cost bounds
python scripts/cost_audit.py

# rebuild the corpus from upstream if ever needed
python -m gita.ingest.run
python scripts/verify_store.py
```
