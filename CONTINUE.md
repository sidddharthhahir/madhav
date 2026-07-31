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
python scripts/verify_store.py     # expect 13 PASS, exit 0
```

Run everything:

```bash
for s in verify_store test_validator test_pipeline test_api test_api_ui \
         test_prefixes validate_eval; do python scripts/$s.py; done
```

127 assertions total, all passing as of commit `9cb10f8`. As of this update:
129 (verify_store.py gained 2 real checks — Hindi and Gujarati coverage went
from informational "expected 0" lines to actual pass/fail assertions once
those languages were populated) across the same seven suites, all still
passing.

---

## 2. Where the project actually stands

**Built, tested, and now actually exercised against the real API:** corpus with
a per-field rights policy, BM25 + dense hybrid retrieval (local Ollama,
`nomic-embed-text`, fused via `reciprocal_rank_fusion`) with IAST folding,
enrichment pipeline (Batch API, **run — 692 verses enriched on Haiku 4.5**),
two-stage answer generation, citation validation with reject-and-regenerate,
FastAPI (dense-retrieval-backed by default), desktop UI on live data,
106-question eval set, seven test suites.

**Retrieval, measured:**

| Configuration | full | partial | miss |
|---|---|---|---|
| BM25 only, no enrichment (original baseline) | 5/106 | 25 | 76 |
| BM25 + Haiku enrichment | 7/106 | 41 | 58 |
| BM25 + enrichment + dense, raw question text, k=8 | 17/106 | 44 | 45 |
| Same, through real query understanding, k=8 | 34-40/106 | 44 | 22-28 |
| Same, through real query understanding, k=12 | 45/106 (42%) | 41 | 20 |
| **Same, k=20 + fusion pool of 30 (current default)** | **54/106 (51%)** | **41** | **11** |

The `k=8 -> k=12` row is the real fix for most of what was previously read as
"retrieval architecture problem": a lot of misses were verses ranked 8-12,
just past the old cutoff, displaced by a thematically adjacent but
differently-specific verse -- e.g. "attached to outcomes I cannot control"
pulls the famous *nishkama karma* / fruits-of-action cluster (BG.5.12,
BG.3.19, BG.2.51...) ahead of the more specific dwelling -> attachment ->
craving chain in BG.2.62, which the eval expects and which sits at dense
rank 42 but BM25 rank 144 -- a real ranking call, not a broken one. Widening
`Pipeline`'s `max_verses` default from 8 to 12 (`src/gita/pipeline.py`)
recovered most of that at the cost of ~50% more context tokens per answer
(a real but small cost increase; the citation validator still only allows
citing what the model was actually shown).

Two different k=8 numbers are listed above because `answer.generate.understand()`
is a real, non-deterministic LLM call -- re-running the identical eval set
against the identical index produced 34/106 one time and 40/106 another.
Don't treat any single `--real` run as exact; it's a real measurement with
real variance, not a fixed constant. The k=12 number was only measured once,
so treat it the same way pending another run.

`scripts/search.py --eval` measured retrieval on raw text for this project's
entire life, which is a different and meaningfully easier-to-undercount task
than what actually ships -- `Pipeline.ask()` never retrieves on the raw
question. Use plain `--eval --hybrid` for fast, free retrieval-only iteration
(e.g. after touching the enrichment prompt or trying an embedding model); use
`--eval --hybrid --real` (costs ~$0.55, calls the real understanding LLM per
question) for the number that actually describes the product.

### On "get it to 106/106"

Asked for directly, and worth writing down because the honest answer is not
"yes". recall@k reaches 100% at k=701 by handing back the entire corpus, so
the number means nothing without its k. `scripts/eval_sweep.py --ranks` shows
where the ceiling actually sits: the worst-placed expected verse has median
rank 23, p90 114, and max 258. So 106/106 needs k around 258 -- roughly a
third of the Gita in every prompt. That is not retrieval, it is giving up on
retrieval, and it would cost about 20x the current per-answer tokens while
making the answer stage worse, not better.

The other route to 106/106 is tuning until this particular 106-question
answer key is satisfied, which is overfitting to the ruler. The eval exists
to estimate generalisation; optimising against it directly destroys the only
thing it is for. And some misses are known to be label artifacts rather than
retrieval failures (BG.2.62 vs the nishkama-karma cluster, documented above),
so a perfect score would partly mean having taught retrieval to prefer
narrower answers than the text supports.

What was done instead: measure honestly, then take the gains that are real.
k=12 -> k=20 and decoupling the fusion pool moved 43 -> 54 full and 23 -> 11
miss. Multi-query fusion was tried and is WORSE (34 full at k=12 against 43)
-- individual themes are short generic phrases that match many verses weakly
and flood the fusion with noise, so the concatenated query wins. Ranker
weighting is within noise. Recorded so none of it is retried blind.

### Valence / stance: tried, measured, did NOT work

The audit's one actionable finding was that enrichment says what a verse is
about but not which side of a feeling it addresses, so "I feel worthless next
to everyone" and "I think I am better than everyone" pull the same
comparison-and-status verses. A `stance` field was added to the enrichment
schema and the whole corpus re-enriched to fill it ($1.30).

The generated text is exactly right. BG.16.15 came back as "a warning to the
arrogant and self-satisfied, NOT comfort for the humble... inflation of
self-regard, not deflation". That is precisely the discrimination that was
missing.

Indexing it changed nothing:

| index | full | partial | miss |
|---|---|---|---|
| with stance | 54 | 41 | 11 |
| without stance | 55 | 39 | 12 |

One question moving, inside run-to-run noise. Neither audited failure was
fixed.

**Why, and this is the part worth keeping:** neither ranker can represent
negation. BM25 is bag-of-words, so "not comfort for the humble" contributes
the tokens `comfort` and `humble` and ATTRACTS the query it was written to
repel. A sentence embedding averages its tokens, so a negated clause lands
near what it negates rather than away from it. Stance is a claim about which
queries should NOT match, and neither method can express "not".

The field is kept, generated and stored, but excluded from the index
(`corpus.INDEXED_FIELDS`). It is the right input for a reranker -- a model
reading the text can act on a negation that no bag-of-words or embedding
can -- and that is the next thing to try, not another indexing variant.

Do not re-add stance to the index expecting a different result.

**Since resolved, two ways.** The stance field is no longer idle:

1. *"Show me the opposite"* (`retrieval/counterpoint.py`) uses it for free.
   Rather than asking an index to understand "not X", it splits the stance
   line ON the "not" and searches for the right-hand clause as a positive
   query. 61.4% of stance lines carry that pivot, covering 86.4% of stanced
   verses. This ships, works, costs nothing, and is exposed in the UI.
2. The reranker (`retrieval/rerank.py`) is the thing this section predicted --
   built, off by default (`MADHAV_RERANK=1`), and **not yet measured**,
   because it needs credit to run at all. Free headroom measurement: pool 30
   -> k=20 could win at most 10 of 106; pool 60 -> k=20 at most 29. Settle it
   with `eval_sweep.py --rerank` before believing anything about it.

### Free query expansion from the corpus's own vocabulary: DON'T

Raw user text retrieves much worse than a model-expanded query (39/106 full vs
53/106, hybrid at k=20). That gap is what `understand()` buys, and it is the
only reason /ask costs money before it even writes anything. Since the free
surfaces -- /preview, /dilemma -- cannot pay for that call, the obvious idea is
to expand queries locally instead, using the 11,266 distinct enrichment phrases
already in the store. Two forms were built and measured. Both were embedded
locally with Ollama (65s, free).

**Form A -- add the nearest phrases to the query as extra text.**

| configuration | full | partial | miss |
|---|---|---|---|
| raw question text | 39 | 37 | 30 |
| raw + 5 phrases | 45 | 33 | 28 |
| raw + 10 phrases | 41 | 40 | 25 |
| raw + 20 phrases | 47 | 31 | 28 |
| model expansion (ceiling) | 53 | 34 | 19 |

Looks like it recovers half the gap. Treat with suspicion: the run is fully
deterministic (cached plans, fixed embeddings, no model calls), yet the result
is NOT monotonic in the one parameter -- 45, then 41, then 47. A 6-point swing
on a monotonic knob means the effect is unstable and the true size is smaller
than the best row suggests. Nobody has swept it properly. Do that before
believing it.

**Form B -- route to the verses that OWN the nearest phrases (a third ranker
fused by RRF alongside BM25 and dense).**

| configuration | full | partial | miss |
|---|---|---|---|
| raw text, bm25+dense | 39 | 37 | 30 |
| + phrase-routing (10) | 40 | 36 | 30 |
| + phrase-routing (25) | 36 | 44 | 26 |
| phrase-routing alone | 19 | 48 | 39 |

**Worth nothing.** Not shipped.

The part worth remembering is *why it looked good*. The motivating failure was
"stay near my parents as they age", where both rankers latch onto `age` and
return metaphysics about the self not being born or dying. Form B fixed it
beautifully -- top hit became BG.18.7, "you cannot rightfully abandon the
duties you actually have". One perfect anecdote, and the aggregate says the
technique does nothing. (Form A, meanwhile, moved the aggregate and did NOT fix
that query at all: the matched phrases contain "aging parents", so they
amplified the very vocabulary that was already dominating.)

This is the third time in this project that a plausible retrieval idea has
survived eyeballing and died on the eval, after multi-query fusion and stance
indexing. Do not ship a retrieval change on the strength of a query you liked.

### The contrast checker earned its keep immediately

`scripts/check_contrast.py` was written to support four prahar palettes instead
of two. On its first run it failed the *existing, already-shipped* light theme:
`--gw-on-accent` (#FFFDF7) on `--gw-accent-fill` (#B98A22) was **3.07:1**
against a required 4.5. That is the "New question" button, and at 13px the
large-text allowance does not apply. Fixed by darkening the fill to #946D19.

This is the same trap CONTINUE.md already warned about, caught a second time,
which is the argument for the check being a script rather than a habit. It
tests 51 text/surface pairs per prahar, parsed out of the real stylesheet. Run
it after touching any colour.

### Audit of the 11 misses (done, question by question)

Each miss was read against what retrieval actually returned. Verdicts are
judgement calls about which verses answer a question, not measurements --
disagree with any of them and the tally moves. Evidence for each is the
enrichment summaries of expected vs. retrieved verses.

**Label artifact -- retrieval as good or better (7 of 11):**

- *attached to outcomes I cannot control* -> returned the nishkama-karma
  cluster (5.12, 3.19, 2.38, 9.28, 2.49). These answer the question more
  directly than the labelled 2.62/2.64.
- *cannot be happy for my friend's success* -> returned 16.18, whose
  enrichment literally says "becomes envious of others", and 9.1 which names
  jealousy. Labels were 16.4/7.27.
- *stop measuring my life against other people's* -> returned 12.19 (not
  shaken by others' opinions) and 5.24/3.17 (satisfaction from within).
- *any way to be genuinely peaceful* -> returned 2.65 and 2.66, the verses
  immediately adjacent to the labelled 2.70/2.71 and making the same point,
  plus 6.15 ("unshakeable peace").
- *is ambition a bad thing* -> returned 7.11, which is arguably THE verse for
  this (desire not opposed to dharma), and 18.34 on determination driven by
  attachment to reward.
- *everyone gives me different advice and I am lost* -> returned 3.2, which
  is Arjuna literally saying "your words conflict, tell me one thing", and
  2.53 on a mind pulled by conflicting teachings. Better than both labels.
- *does it matter how I behave when nobody is watching* -> returned 17.18
  (discipline performed to be seen) and 3.6 (self-deception). Here the LABEL
  looks wrong: 3.21 is about setting an example for others, i.e. behaviour
  when people ARE watching, close to the opposite of the question.

**Mixed -- one label matched by an equivalent verse (2 of 11):**

- *only enjoy my job when praised* -> 12.19 and 14.24 were labelled; returned
  5.24 and 5.21 on not chasing approval. Both defensible.
- *find purpose when I have no idea what I am doing* -> returned 2.7, Arjuna
  asking to be taught, which is the same move as the labelled 4.34. The
  labelled 3.35 (svadharma) was genuinely not found.

**Real retrieval failures (2 of 11):**

- *I feel worthless compared to everyone around me* -> labelled 6.5/6.6
  ("lift yourself by yourself; the mind is your friend or your enemy"),
  which is exactly right. Retrieval returned 16.15 and 16.13, about pride
  and acquisitiveness -- the OPPOSITE emotional pole. The query shares
  vocabulary ("compared", "status") with verses about arrogance, and nothing
  distinguishes the valence. This is the clearest real defect found.
- *scroll for hours and feel worse afterwards* -> caught the compulsion
  (6.35, 14.12, 2.44) but missed the envy/comparison half (16.18), which the
  index clearly holds since it surfaces for the envy question above.

**What this means for the headline number.** The true retrieval failure rate
is about 2-4 of 106, not 11. 54/106 "full" understates the system, because
"full" demands both hand-picked verses and the Gita usually offers several
equally good ones. Chasing that number upward would mostly mean teaching
retrieval to prefer narrower answers than the text supports -- the concrete
form of the overfitting argument above.

**The one actionable pattern: valence.** Both real failures are the same
defect. Enrichment describes what a verse is ABOUT but not which side of a
feeling it speaks to, so "I feel worthless" and "I am arrogant" retrieve the
same status-and-comparison verses. A valence or stance field in the
enrichment prompt is the targeted fix, and it is cheap to test against the
cached plans before spending on a re-enrichment run.

11 nominal misses remain at k=20, audited above. A quick read of them: mostly abstract/
existential questions ("am I my thoughts or something underneath them",
"does anything actually care whether I exist") where the phrase itself
carries little concrete vocabulary for either BM25 or embeddings to grab
onto, unlike the concrete-situation questions ("I only enjoy my job when I
get praised for it") that dense retrieval handles well. Some of these may
also be an artifact of the eval set only labelling 2 expected verses per
question when the Gita legitimately supports more (BG.2.62 vs. the
nishkama-karma cluster above is a clean example of this) -- that still
hasn't been audited question by question.

**Nothing left from the original scope.** Hindi and Gujarati are now generated
too (see below) and the code licence is resolved (§5).

### The API has now actually been called — costs so far

- Sanity check (`ask.py`, real generation): **$0.026** — `attempts: 1`,
  `validation: OK`. The citation validator's regex handles real model output
  fine, no widening needed.
- 20-verse Haiku enrichment calibration batch: **$0.026** (measured from
  `usage.input_tokens`/`usage.output_tokens`, not the estimator).
- Full 672-verse Haiku enrichment batch: **$1.10** (measured, same way).
- 20-verse Hindi/Gujarati calibration batch: **$0.017**.
- Full 681-verse Hindi/Gujarati batch: **$0.63**.
- Three `--eval --hybrid --real` diagnostic runs (106 real `understand()` calls
  each, ~$0.55/run) while chasing the recall numbers above: **~$1.65**. One of
  these overlapped with a duplicate process that wasn't fully killed and ran
  for roughly a minute before being caught (a background-job management
  mistake, not a code bug) — a small amount of that is wasted double-spend,
  not reflected as a separate line since the exact overlap wasn't measured.
- **Total: ~$3.45.** Every cost table in this repo is an estimate for models
  that were never run, or a pre-flight number superseded once the real batch
  ran — Haiku enrichment's estimate was off by ~3.4x, translation's by ~1.9x,
  always in the same direction (over, not under). Don't trust an unmeasured
  estimate here for anything but rough ordering between models; always
  calibrate on a small batch first.

---

## 3. What's actually left

### a. Hindi and Gujarati — resolved

Generated: all 701 verses, both languages, from `src/gita/translate`. Real
cost $0.65 against a $1.24 estimate. Read a few before trusting them blindly:

```bash
sqlite3 data/gita.sqlite3 \
  "SELECT verse_id, body FROM texts WHERE lang='hi' AND source_key='derived' LIMIT 5;"
```

One thing this surfaced that wasn't anticipated: `src/gita/sources.py`'s
rights-policy schema was built entirely around the upstream vedicscriptures
dataset's field codes (`et`/`ht`/`sc`/...) and had no field code for Gujarati
at all, and no entry for content generated here rather than sourced upstream.
`scripts/verify_store.py`'s policy check correctly flagged the new rows as
unreviewed rather than silently permitting them -- that's the check working as
designed, not a bug to route around. Fixed by adding an explicit `derived`
policy entry and a `gt` field code, not by weakening the check. See the trap
entry below if this comes up again for a third derived-content type.

### b. Push recall further, or accept the current ceiling

`max_verses` went from 8 to 12 (see §2) for a real, measured recall jump. What's
still open:

- `nomic-embed-text` was tried against `mxbai-embed-large` (larger, 1024-dim) —
  mxbai did *worse* untuned (4/106 vs 17/106 at k=8), because it needs an
  instruction-prefix convention (`"Represent this sentence for searching
  relevant passages: "` on queries) this codebase doesn't apply. Before trying
  another model, apply that convention properly rather than assuming bigger is
  better.
- The enrichment `situations` prompt in `src/gita/enrich/prompt.py` was
  validated qualitatively (the notes read as concrete and modern) but never
  audited against *why* specific expected verses are missed.
- The 20 remaining k=12 misses skew abstract/existential rather than
  situational — worth checking whether the enrichment prompt should explicitly
  ask for an abstract/philosophical framing alongside the concrete-situation
  one it currently asks for exclusively.
- Some fraction of "misses" are the eval set's own artifact (a question with
  more than 2 legitimate Gita answers, only 2 of which are labelled) rather
  than a real retrieval failure — BG.2.62 vs. the nishkama-karma cluster (§2)
  is a clean, confirmed example. This hasn't been audited question by
  question to find out how large that fraction actually is, which means the
  true ceiling on this number is currently unknown in either direction.

### c. Code licence — resolved

MIT. See §5.

---

## 4. Traps already hit

Each of these cost real debugging time.

| Trap | Detail |
|---|---|
| **XML comments cannot contain `--`** | SVG is XML. A double hyphen inside a comment is a hard parse error and the file renders *nothing*, silently, while still serving HTTP 200. Broke `logo.svg` once. |
| **SQLite cannot cross threads** | FastAPI dispatches sync endpoints to a worker threadpool. A connection opened at startup fails on first request. Fixed by loading everything into memory at construction; `check_same_thread=False` is only a backstop. This backstop still let a real segfault through later — see the fuller entry below. |
| **SDK auth error fires at request time** | The Anthropic SDK raises a bare `TypeError` when building request headers, *not* when constructing the client. Wrapping only the constructor leaves a raw traceback escaping. Both sites are wrapped in `answer/generate.py`. |
| **Checkpoint WAL before committing the database** | Otherwise the commit captures a half-written file. `PRAGMA wal_checkpoint(TRUNCATE)` first. |
| **Never commit `data/cache/`** | 34 MB of unfiltered upstream payloads including Prabhupāda's translation. Public repo. `.gitignore` explains why; do not "helpfully" re-add it. |
| **Corporate TLS proxies break Python but not PowerShell** | On the Windows machine, `urllib` could not reach archive.org (self-signed cert in chain) while `Invoke-WebRequest` could, because they use different trust stores. If a download fails on Mac with `CERTIFICATE_VERIFY_FAILED`, this is why. |
| **`.dc.html` is not standalone** | `frontend/Madhav.dc.html` needs `support.js` beside it to render `{{ }}` bindings. It is a design reference, not the app. The real UI is `frontend/web/`. |
| **Windows 260-char path limit** | Irrelevant on Mac, but it is why a project-local venv exists rather than a global install: the Anthropic SDK ships filenames long enough to break the Store Python's `site-packages`. |
| **Batch API `custom_id` rejects dots** | Verse ids are `BG.1.1`-style; the Batch API requires `^[a-zA-Z0-9_-]{1,64}$`. Submitting the enrichment batch with the raw verse id as `custom_id` fails outright with a 400. Fixed by encoding `.`→`_` on submit and decoding on collect (`_to_custom_id`/`_from_custom_id` in `enrich/generate.py`) — verse ids never contain underscores, so this round-trips losslessly. Never hit this until the first real batch submission, because nothing had called the Batch API before. |
| **A single pooled embedding dilutes with long, heterogeneous input** | Feeding BM25's full `searchable_text` (enrichment + translations + word-by-word Sanskrit glosses) into the embedding model produced a much weaker semantic signal than feeding it just the enrichment fields. BM25 doesn't have this problem — each term scores independently — but a dense vector is one pooled representation of the whole input, and the Sanskrit glosses (`अद्वेष्टा nonhater? सर्वभूतानाम्...`) are noise for that purpose. Fixed with a separate `corpus.dense_text()` that embeds enrichment only. |
| **SQLite reads need the same lock as writes, not just writes** | `Pipeline` already knew inserts had to be serialised under a threaded FastAPI server (`check_same_thread=False` is a backstop, not a real guarantee) — but `chapters()`, `history()`, and `saved()` were plain reads on the same shared connection, left unlocked on the assumption that "writes are the only runtime SQLite access." They aren't: those three reads fire concurrently on every page load. Two threads calling `execute()` on the same connection at once doesn't raise a Python exception — it segfaults the whole process (confirmed via macOS crash report: `SIGSEGV` inside `sqlite3VdbeExec`, called from a FastAPI threadpool worker). Fixed by putting every `self.conn` access, reads included, through the same lock. If you add a new method that touches `self.conn`, it needs the lock too — there is no read/write distinction that makes a bare read safe here. |
| **Background shell jobs (`&`/`nohup`/`disown`) don't reliably survive their tool call** | Starting `uvicorn ... &` then `disown`ing it inside a single shell invocation looked like it worked (server answered requests) but the process vanished minutes later with no shutdown log line — the wrapping tool call's process group appears to get torn down regardless of `disown`. Long-running dev servers need to be started via whatever the harness's actual "run in background" primitive is, not shell-level backgrounding tricks. |
| **The rights-policy schema has no concept of "generated here," only "sourced from upstream"** | `sources.py`'s field codes (`et`/`ht`/`sc`/...) and `FIELD_LANG` map were built entirely around the upstream vedicscriptures dataset. Writing Hindi/Gujarati translations into `texts` with `source_key='derived'` made `verify_store.py`'s policy check fail: no field code existed for Gujarati at all, and no policy entry existed for `derived`. This is the check working correctly, not a bug -- `sources.py` says explicitly "unknown source keys return False... must be reviewed and added explicitly rather than silently swept in." Fixed by adding a real `_DERIVED` policy entry and a `gt` field code, with a note explaining why it's permitted (translated from Sanskrit + already-cleared English, not adapted from any third-party Hindi/Gujarati edition). Don't be tempted to special-case `derived` past the check instead of teaching the check about it -- the whole point of the check is that new sources get reviewed, not exempted. |
| **`scripts/search.py --eval` measured a different, harder task than the product actually does** | The eval harness ran retrieval on the raw question text. `Pipeline.ask()` never does that -- it always runs the question through `answer.generate.understand()` first, which rewrites it toward corpus vocabulary before retrieval. This made recall look far worse than it is: 17/106 full on raw text vs. 40/106 through the same understanding step the real pipeline always uses, on the identical index. Nothing about retrieval changed between those two numbers -- only what was being measured. `test_pipeline.py` had already flagged this exact distinction in a comment ("retrieval runs on the EXPANDED query from stage 1, not the raw question") but the eval script was never updated to match. Fixed by adding `--real` to `search.py --eval`, which costs real money (~$0.55 for the full set) since it calls the understanding LLM per question -- so the free, raw-text number stays the default and is still useful for isolating retrieval quality alone, but report the `--real` number, not the free one, when the question is "what does the product actually do." |

---

## 5. Decisions still open

**Code licence — resolved.** MIT, in [LICENSE](LICENSE). Covers the code only
— `NOTICE.md` still governs the corpus text under its own per-source basis;
the LICENSE file says so explicitly to avoid the MIT grant being misread as
covering third-party translations nobody here holds copyright over.

**Enrichment model — resolved.** Haiku 4.5 was used for the full run. The
20-verse calibration read as concrete and modern in plain language, and actual
cost ($1.10 for 672 verses) came in well under even the optimistic estimate,
so Opus was never tried. If recall needs to go materially higher later,
re-enriching a sample with Opus and comparing `--eval --hybrid` before/after is
still the fallback option — nothing about the Haiku run forecloses it.

**Whether to commit the updated `data/gita.sqlite3`.** It grew from 22MB to
~30MB (enrichment text + 701 embedding vectors). Not yet committed as of this
writing — a deliberate choice to leave to whoever's driving, since it's a
meaningful binary diff and worth a conscious decision rather than a reflexive
`git add -A`.

**Whether dense retrieval should be a hard dependency or stay optional.**
Currently it's opt-in-by-default in the API server (`use_dense=True`) but
degrades silently to BM25 if Ollama isn't running. That's the right call for a
single-user desktop app; it may not be if this is ever deployed somewhere
Ollama isn't guaranteed to be present.

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

# hybrid (BM25 + dense) -- needs Ollama running with nomic-embed-text pulled
python scripts/build_embeddings.py             # one-time, embeds all 701 verses, free
python scripts/search.py --eval --hybrid       # the number that matters
python scripts/ask.py --hybrid "..."           # real generation, costs a few cents

# demonstrate what enrichment does, no API calls
python scripts/demo_enrichment.py
python scripts/demo_enrichment.py --revert

# "show me the opposite" -- free, no model call
curl -s -X POST localhost:8000/counterpoint -H 'content-type: application/json' \
  -d '{"verse_ids":["BG.16.18","BG.16.13"],"k":4}'

# dharma-sankata: both sides of a choice -- free, no model call
curl -s -X POST localhost:8000/dilemma -H 'content-type: application/json' \
  -d '{"option_a":"leave the company i built","option_b":"stay and fight for it","k":4}'

# the reranker: off by default, and unmeasured until this is run
MADHAV_RERANK=1 uvicorn --app-dir src gita.api.app:app
python scripts/eval_sweep.py --rerank --limit 25   # ~15c, does it actually help?

# cost bounds
python scripts/cost_audit.py

# rebuild the corpus from upstream if ever needed
python -m gita.ingest.run
python scripts/verify_store.py

# check what's actually enriched/embedded, and by which model
sqlite3 data/gita.sqlite3 "SELECT model, COUNT(*) FROM enrichment GROUP BY model;"
sqlite3 data/gita.sqlite3 "SELECT model, dim, COUNT(*) FROM embeddings GROUP BY model, dim;"
```
