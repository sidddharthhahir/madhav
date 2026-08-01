# Sources and rights

This documents where the text in `data/gita.sqlite3` comes from and why each
part is considered usable. The machine-readable policy lives in
[`src/gita/sources.py`](src/gita/sources.py) and is enforced at ingest; this
file is the human-readable version of the same reasoning.

**Jurisdiction.** These determinations apply India's copyright term of the
author's life plus 60 years, counted from the start of the year following death.
That is the relevant jurisdiction for these authors and publishers. Other
countries differ — the United States in particular turns on publication date and
renewal rather than the author's death. This is a considered reading, not legal
advice. Have counsel confirm it before commercial use.

## Where the data comes from

The corpus is built from the [vedicscriptures Bhagavad Gita
dataset](https://github.com/vedicscriptures/bhagavad-gita) (MIT licensed,
static JSON). The MIT licence covers that repository's code and compilation —
**not** the copyright in each translation it bundles, which is why the
per-field policy below exists.

Recension: **Gita Press, 701 verses**, with chapter 13 having 35 verses.

## Included

| Content | Attribution | Basis |
|---|---|---|
| Sanskrit verse text (Devanagari) | — | Ancient; public domain |
| IAST transliteration | — | Mechanical transliteration of public-domain text |
| English translation | Shri Purohit Swami (d. 1941), published 1935 | Public domain in India since 2002 |
| English translation and commentary | Swami Sivananda (d. 1963) | Public domain in India since 2024 |
| Sanskrit commentaries (13) | Śaṅkarācārya, Rāmānuja, Madhvācārya, Abhinavagupta, Vallabhācārya, Śrīdhara Svāmī, Madhusūdana Sarasvatī, Jayatīrtha, Ānandagiri, Dhanapati, Vedāntadeśika Veṅkaṭanātha, Puruṣottama, Nīlakaṇṭha | All pre-1900; public domain |
| Hindi translation | Claude (`claude-haiku-4-5`), derived | Machine translation from the Sanskrit original plus the Purohit Swami and Sivananda English translations above, both already permitted. Not adapted from any existing Hindi edition — see "Hindi and Gujarati" below for why the obvious sources don't work. |
| Gujarati translation | Claude (`claude-haiku-4-5`), derived | Same basis as the Hindi translation. |

## Excluded

These are present in the upstream dataset and are deliberately filtered out at
ingest. None reaches the database.

| Attribution | Died | In copyright until |
|---|---|---|
| A.C. Bhaktivedanta Swami Prabhupāda (BBT) | 1977 | 2038 |
| Swami Ramsukhdas (Gita Press) | 2005 | 2066 |
| Swami Chinmayananda | 1993 | 2054 |
| Swami Gambirananda | 1988 | 2049 |
| Swami Adidevananda | 1983 | 2044 |
| Swami Tejomayananda | living | — |
| Dr. S. Sankaranarayan | unknown | Excluded as unknown provenance |

Unknown provenance is treated as unusable rather than as permission. A new
commentator appearing upstream is excluded by default until reviewed —
`src/gita/sources.py` fails the ingest loudly rather than sweeping it in.

## The policy is per-field, not per-translator

For Śaṅkarācārya, Rāmānuja and Abhinavagupta the upstream record pairs an
ancient Sanskrit commentary with a modern English or Hindi rendering of it by an
uncredited 20th-century translator. The Sanskrit original is ingested; the
modern rendering beside it is dropped. The commentary is 8th–12th century, the
translation of it is not.

## The raw cache is not distributed

`data/cache/` holds the unfiltered upstream payloads, including every excluded
translation above. It is kept locally so exclusions can be re-audited without
re-crawling, and is excluded from version control by `.gitignore`. Only the
filtered database is committed or redistributed.

Verify at any time:

```bash
python scripts/verify_store.py
```

That re-derives every assertion from the database rather than trusting the
ingester's own reporting, and fails if any excluded source is present.

## Hindi and Gujarati

Every Hindi translation in the upstream vedicscriptures dataset is one of the
excluded entries above, and Hindi Wikisource has no Gita, so neither language
could be *sourced* — both are *derived* instead: translated directly by Claude
from the Sanskrit original plus the already-permitted English translations
(see [`src/gita/translate/`](src/gita/translate/)), not adapted from any
existing Hindi or Gujarati edition. This sidesteps the rights question
entirely rather than resolving it in our favour — there's no third-party
translation being relied on.

The obvious existing sources were considered and don't work mechanically, not
just rights-wise. Gita Press's Jayadayal Goyandka (d. 1965) entered the public
domain in India on 1 January 2026 and would otherwise be the natural choice,
but exists only as archive.org OCR over two-column page scans whose columns
are read out of order — five editions were tested and the best aligned 1 of 18
chapters. See [`src/gita/ingest/gitapress.py`](src/gita/ingest/gitapress.py).
Gandhi's *Anasaktiyoga* for Gujarati is also a page scan and was never tested,
but likely has the same problem.

`src/gita/sources.py`'s `derived` policy entry documents this basis in code,
enforced the same way every other source is: `scripts/verify_store.py` fails
if a `texts` row exists that policy doesn't explicitly permit.

## Code licence

[MIT](LICENSE), covering the application code in `src/`, `scripts/` and
`frontend/web/` only. It does not extend to the corpus text — the rights
position above is independent of whatever licence the code carries, and the
LICENSE file says so explicitly.

## Reader artwork

Background art in the immersive reader (`GET /read/{chapter}` ->
`frontend/web/art/`). Two AI-generated illustrations, supplied directly
by the project owner for use in this app -- not sourced from a museum
or archive, so no public-domain or attribution claim is made for them.
Resized to 1600px on the long edge and re-encoded as WebP.

An earlier pass used five museum-sourced public-domain paintings
instead; retired outright on user feedback rather than kept as
`unused` entries here. Their sourcing and licence verification are in
git history (see the commits touching this file before this one), not
duplicated as permanent documentation for art the app no longer ships.

**Krishna and Arjuna in dialogue on the field**
- Default (every chapter without a more specific plate)
- AI-generated illustration
- File: `frontend/web/art/krishna-arjuna-dialogue.webp`
- Note: Supplied directly by the project owner for use in this reader. Used as the default background for every chapter that has no more specific plate -- i.e. every chapter except 11, which gets the Vishvarupa image below.

**Vishvarupa, the cosmic form**
- Chapter 11
- AI-generated illustration
- File: `frontend/web/art/vishvarupa-cosmic-form.webp`
- Note: Supplied directly by the project owner for use in this reader. Chapter 11 only -- the one chapter this specific image was made for.
