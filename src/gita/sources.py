"""Rights policy for the upstream vedicscriptures dataset.

The upstream repo is MIT licensed, but that licence covers the repo's *code and
compilation*, not the copyright in each translation it bundles. The payload
mixes three very different categories:

  1. Ancient/medieval Sanskrit commentaries (Shankara, Ramanuja, Madhva, ...).
     The Sanskrit originals are long out of copyright.
  2. Early-20th-century English translations whose authors died long enough ago
     that the work has fallen into the public domain in India (life + 60 years).
  3. Modern, actively-published translations that are firmly in copyright --
     including A.C. Bhaktivedanta Swami Prabhupada's, i.e. exactly the BBT
     material we set out to avoid.

Crucially the policy has to be decided per *field*, not per source. Several
entries pair a public-domain Sanskrit commentary (`sc`) with a modern
English or Hindi rendering of that commentary (`et` / `ht`) by an uncredited
20th-century translator. The Sanskrit is usable; the rendering is not.

Field codes used upstream:
    et = English translation      ht = Hindi translation
    ec = English commentary       hc = Hindi commentary
    sc = Sanskrit commentary

NOTE: the determinations below reflect India's life + 60 term, which is the
relevant jurisdiction for these authors and publishers. Terms differ elsewhere
(the US in particular turns on publication date and renewal, not death date).
This encodes a defensible reading, not legal advice -- have counsel confirm
before shipping commercially.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SourcePolicy:
    key: str                      # upstream JSON key
    translator: str
    died: int | None              # year of death; None if living/unknown
    allow: frozenset[str]         # fields we ingest
    deny: frozenset[str] = field(default_factory=frozenset)
    note: str = ""

    def permits(self, fld: str) -> bool:
        return fld in self.allow


def _p(key, translator, died, allow, deny=(), note=""):
    return SourcePolicy(key, translator, died, frozenset(allow), frozenset(deny), note)


# --- Public domain: early-20th-century English translations ------------------

_PD_ENGLISH = [
    _p("purohit", "Shri Purohit Swami", 1941, allow=["et"],
       note="Translation published 1935; PD in India since 2002. Complete, "
            "readable verse translation -- our primary English text."),
    _p("siva", "Swami Sivananda", 1963, allow=["et", "ec"],
       note="PD in India since 2024. Ships a per-verse commentary as well as a "
            "translation, which is our main substitute for Prabhupada's purports."),
]

# --- Public domain: ancient & medieval Sanskrit commentaries -----------------
# For these, only `sc` (the Sanskrit original) is public domain. Any `et`/`ht`
# alongside it is an uncredited modern rendering and is denied.

_PD_SANSKRIT = [
    _p("sankar", "Sri Shankaracharya", 820, allow=["sc"], deny=["et", "ht"],
       note="Bhasya itself is PD; the bundled English/Hindi renderings are modern."),
    _p("raman", "Sri Ramanuja", 1137, allow=["sc"], deny=["et"]),
    _p("abhinav", "Sri Abhinav Gupta", 1020, allow=["sc"], deny=["et"]),
    _p("madhav", "Sri Madhavacharya", 1317, allow=["sc"]),
    _p("anand", "Sri Anandgiri", 1300, allow=["sc"]),
    _p("jaya", "Sri Jayatritha", 1388, allow=["sc"]),
    _p("vallabh", "Sri Vallabhacharya", 1531, allow=["sc"]),
    _p("ms", "Sri Madhusudan Saraswati", 1650, allow=["sc"]),
    _p("srid", "Sri Sridhara Swami", 1450, allow=["sc"]),
    _p("dhan", "Sri Dhanpati", 1800, allow=["sc"]),
    _p("venkat", "Vedantadeshikacharya Venkatanatha", 1369, allow=["sc"]),
    _p("puru", "Sri Purushottamji", 1725, allow=["sc"]),
    _p("neel", "Sri Neelkanth", 1650, allow=["sc"]),
]

# --- Derived: generated here, not sourced from upstream at all ---------------
# Hindi and Gujarati translations produced by src/gita/translate -- an LLM
# translation from the Sanskrit original plus the two already-cleared English
# translations (purohit, siva), not adapted from any third-party Hindi/Gujarati
# edition. There is no upstream rights question to evaluate: nothing here
# passed through vedicscriptures, and the source material it was built from is
# already permitted by the entries above. Gujarati never had a field code at
# all until this, since the upstream dataset carries no Gujarati.

_DERIVED = [
    _p("derived", "Claude (derived translation)", None, allow=["ht", "gt"],
       note="Not from vedicscriptures. Machine translation from Sanskrit + "
            "the purohit/siva English translations, both already permitted "
            "above. See src/gita/translate/prompt.py for the exact prompt."),
]

# --- In copyright: excluded entirely ----------------------------------------

_EXCLUDED = [
    _p("prabhu", "A.C. Bhaktivedanta Swami Prabhupada", 1977, allow=[],
       deny=["et", "ec"],
       note="THE BBT MATERIAL. In copyright in India until 2038 and actively "
            "enforced. This is the content we left vedabase.io to avoid; it is "
            "bundled here regardless of the repo's MIT badge. Never ingest."),
    _p("tej", "Swami Tejomayananda", None, allow=[], deny=["ht"],
       note="Living author, Chinmaya Mission. In copyright."),
    _p("chinmay", "Swami Chinmayananda", 1993, allow=[], deny=["hc"],
       note="In copyright in India until 2054."),
    _p("rams", "Swami Ramsukhdas", 2005, allow=[], deny=["ht", "hc"],
       note="Gita Press. In copyright until 2066. This was the best Hindi "
            "translation in the payload, and it is unusable."),
    _p("gambir", "Swami Gambirananda", 1988, allow=[], deny=["et"],
       note="Advaita Ashrama. In copyright until 2049."),
    _p("adi", "Swami Adidevananda", 1983, allow=[], deny=["et"],
       note="Ramakrishna Math. In copyright until 2044."),
    _p("san", "Dr.S.Sankaranarayan", None, allow=[], deny=["et"],
       note="Modern academic translation, rights unclear. Excluded by default: "
            "unknown provenance is treated as unusable, not as permission."),
]

POLICIES: dict[str, SourcePolicy] = {
    p.key: p for p in (_PD_ENGLISH + _PD_SANSKRIT + _DERIVED + _EXCLUDED)
}

# Fields that carry the verse text itself rather than a commentary.
# "gt" (Gujarati translation) exists only for the derived source above --
# nothing upstream has ever had a Gujarati field.
TRANSLATION_FIELDS = {"et", "ht", "gt"}
COMMENTARY_FIELDS = {"ec", "hc", "sc"}

FIELD_LANG = {"et": "en", "ec": "en", "ht": "hi", "hc": "hi", "sc": "sa", "gt": "gu"}


def kind_of(fld: str) -> str:
    return "translation" if fld in TRANSLATION_FIELDS else "commentary"


def permitted(source_key: str, fld: str) -> bool:
    """True only for fields we have an affirmative reason to ingest.

    Unknown source keys return False. New commentators appearing upstream must
    be reviewed and added explicitly rather than silently swept in.
    """
    policy = POLICIES.get(source_key)
    return bool(policy and policy.permits(fld))


def unknown_keys(seen: set[str]) -> set[str]:
    """Upstream keys we have no policy for -- ingest must surface these."""
    return {k for k in seen if k not in POLICIES}
