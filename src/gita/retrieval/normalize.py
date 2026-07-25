"""Text normalisation for retrieval.

The hard part here is not stemming, it is transliteration. The corpus writes
Sanskrit names in IAST -- "Kṛṣṇa", "Dhṛtarāṣṭra", "Kurukṣetra" -- while users
type "Krishna", "Dhritarashtra", "Kurukshetra". Naive Unicode folding (NFKD,
strip combining marks) turns "Kṛṣṇa" into "krsna", which still fails to match
"krishna". So we map IAST characters to the *common English romanisation*
first, then fold whatever is left:

    kṛṣṇa       -> krishna
    dhṛtarāṣṭra -> dhritarashtra
    kurukṣetra  -> kurukshetra
    sañjaya     -> sanjaya

Without this step every proper-noun query silently misses.
"""

import re
import unicodedata

# IAST -> common romanisation. Applied before Unicode folding so we control
# the result; ṛ becomes "ri" and ś/ṣ become "sh" rather than bare r/s.
_IAST = {
    "ā": "a", "ī": "i", "ū": "u", "ē": "e", "ō": "o",
    "ṛ": "ri", "ṝ": "ri", "ḷ": "li", "ḹ": "li",
    "ṅ": "n", "ñ": "n", "ṇ": "n", "ṃ": "m", "ṁ": "m",
    "ṭ": "t", "ḍ": "d", "ḥ": "h", "ś": "sh", "ṣ": "sh",
}

# Ordinary English stopwords only. Corpus-ubiquitous terms such as "krishna"
# or "lord" are deliberately NOT listed -- BM25's IDF already discounts terms
# that appear in most documents, and hard-removing them would break queries
# where the name is the point.
STOPWORDS = frozenset("""
a an the and or but if then than that this these those of in on at to for from
by with without about into over under again further is am are was were be been
being have has had having do does did doing i me my we our you your he him his
she her it its they them their what which who whom when where why how all any
both each few more most other some such no nor not only own same so too very
can will just should now as also there here
""".split())

_WORD = re.compile(r"[a-z0-9]+")


def fold(text: str) -> str:
    """Lowercase, map IAST to common romanisation, strip remaining accents."""
    text = text.lower()
    text = "".join(_IAST.get(ch, ch) for ch in text)
    # Anything still carrying a combining mark gets flattened.
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def stem(word: str) -> str:
    """Deliberately light suffix stripping.

    Aggressive stemming hurts here: it collapses distinct Sanskrit terms that
    happen to share a tail. We only strip common English inflections, and only
    when enough stem remains to stay meaningful.
    """
    for suffix, min_len in (
        ("ness", 6), ("ment", 6), ("tion", 6), ("ing", 5),
        ("edly", 6), ("ely", 5), ("ly", 4), ("ed", 4), ("es", 4), ("s", 3),
    ):
        if len(word) > min_len and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def tokenize(text: str, *, drop_stopwords: bool = True) -> list[str]:
    tokens = _WORD.findall(fold(text))
    if drop_stopwords:
        tokens = [t for t in tokens if t not in STOPWORDS]
    return [stem(t) for t in tokens if len(t) > 1]
