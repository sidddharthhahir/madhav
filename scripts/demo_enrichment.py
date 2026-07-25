"""Hand-written enrichment for a handful of verses, to demonstrate the mechanism.

This costs nothing and calls no API. It writes enrichment records by hand for a
few verses so the effect on retrieval is visible immediately.

IMPORTANT -- this is a demonstration, NOT a measurement. The verses were chosen
because the eval set expects them, so measuring recall against that same eval
set afterwards is circular by construction. It shows that the mechanism works;
it does not show how well real generated enrichment will perform. Only a real
batch over all 701 verses gives an honest number.

    python scripts/demo_enrichment.py            # insert and show the effect
    python scripts/demo_enrichment.py --revert    # remove again
"""

import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gita import db  # noqa: E402
from gita.retrieval import corpus  # noqa: E402

# Written by hand from the public-domain translations already in the store.
DEMO = {
    "BG.3.37": {
        "summary": "Desire and anger are described as a single force showing up at "
                   "two different moments. Wanting something you cannot have does "
                   "not stay as wanting; it turns into hostility, and the hostility "
                   "then feels justified even though it began as appetite.",
        "themes": ["unfulfilled desire", "anger", "envy", "comparison to others",
                   "resentment", "appetite that turns hostile"],
        "situations": [
            "resenting a colleague who got the promotion you wanted",
            "disliking a stranger online who seems to have the life you want",
            "feeling irritated by someone successful you have never met",
            "scrolling social media and feeling worse about yourself afterwards",
            "snapping at people close to you when something else is frustrating you",
            "being unable to stop thinking about someone who wronged you",
            "wanting something badly and turning bitter when it does not happen",
        ],
        "emotions": ["resentment", "jealousy", "irritation", "bitterness",
                     "frustration", "envy"],
        "keywords": ["resent", "hate", "jealous", "envy", "angry", "bitter",
                     "comparison", "wanting", "craving", "spite", "hostile",
                     "why am I angry", "online hate"],
    },
    "BG.7.27": {
        "summary": "People are described as confused from the start by pairs of "
                   "opposites -- liking and disliking, wanting and rejecting. The "
                   "mind sorts everything into these two bins automatically, and "
                   "that reflex is what produces a lot of ordinary confusion.",
        "themes": ["attraction and aversion", "instant judgement",
                   "pairs of opposites", "reflexive liking and disliking",
                   "confusion"],
        "situations": [
            "forming a strong opinion about someone you have just met",
            "disliking a public figure you have never actually interacted with",
            "having instant reactions you cannot really justify",
            "flipping between loving and hating the same thing",
            "taking sides strongly on something that does not affect you",
        ],
        "emotions": ["confusion", "instant dislike", "attraction", "aversion"],
        "keywords": ["like", "dislike", "opposites", "judgement", "reaction",
                     "why do I hate", "strangers", "snap judgement", "bias"],
    },
    "BG.16.18": {
        "summary": "Describes a state built out of ego, self-importance, "
                   "conceit and appetite, and how someone in it becomes envious "
                   "of others rather than settled in themselves.",
        "themes": ["ego", "pride", "conceit", "envy", "self-importance",
                   "arrogance"],
        "situations": [
            "feeling threatened by someone else doing well",
            "needing to be seen as better than the people around you",
            "resenting recognition that went to someone else",
            "putting others down to feel steady in yourself",
            "being unable to be happy for a friend's success",
        ],
        "emotions": ["envy", "pride", "insecurity", "contempt", "superiority"],
        "keywords": ["ego", "pride", "envy", "arrogant", "jealous", "insecure",
                     "self-important", "conceit", "resentment"],
    },
    "BG.2.62": {
        "summary": "Traces a chain: dwelling on something produces attachment to "
                   "it, attachment produces craving, and craving is where the "
                   "trouble starts. The point is that the sequence begins with "
                   "simply letting the mind circle something.",
        "themes": ["dwelling on something", "attachment", "craving",
                   "how desire forms", "rumination"],
        "situations": [
            "doomscrolling late at night and feeling worse",
            "not being able to stop checking someone's profile",
            "replaying a conversation over and over",
            "getting attached to an outcome you cannot control",
            "letting a small want grow into an obsession",
        ],
        "emotions": ["craving", "obsession", "restlessness", "attachment"],
        "keywords": ["obsess", "attached", "craving", "rumination", "can't stop",
                     "scrolling", "fixated", "overthinking"],
    },
    "BG.2.63": {
        "summary": "Continues the chain: craving produces anger, anger produces "
                   "confusion, confusion clouds memory, and once judgement goes "
                   "the person comes apart. It describes a slide rather than a "
                   "single failure.",
        "themes": ["anger", "confusion", "loss of judgement", "spiralling",
                   "losing control"],
        "situations": [
            "losing your temper over something small",
            "saying something in anger you cannot take back",
            "making a bad decision while upset",
            "spiralling from irritation into something much worse",
        ],
        "emotions": ["anger", "rage", "confusion", "regret"],
        "keywords": ["temper", "angry", "lost it", "rage", "spiral",
                     "bad decision", "why do I lose my temper"],
    },
    "BG.6.16": {
        "summary": "Says the practice does not work for someone who eats too much "
                   "or too little, nor for someone who sleeps too much or too "
                   "little. Balance in ordinary bodily habits is treated as a "
                   "precondition, not an afterthought.",
        "themes": ["moderation", "balance", "routine", "eating", "sleeping",
                   "extremes"],
        "situations": [
            "overeating and then feeling sluggish and low",
            "sleeping too much or too little and feeling unable to function",
            "swinging between overworking and collapsing",
            "having no routine and feeling scattered because of it",
            "burning out from pushing too hard for too long",
        ],
        "emotions": ["sluggishness", "restlessness", "exhaustion", "guilt"],
        "keywords": ["overeat", "oversleep", "routine", "balance", "moderation",
                     "burnout", "no discipline", "habits", "too much", "too little"],
    },
    "BG.6.17": {
        "summary": "Describes regulated habits -- eating, resting, activity and "
                   "sleep kept in proportion -- as what makes the practice "
                   "workable and brings an end to a particular kind of misery.",
        "themes": ["regulated habits", "proportion", "discipline", "rest",
                   "sustainable effort"],
        "situations": [
            "trying to build a routine that actually holds",
            "finding a sustainable pace instead of extremes",
            "fixing a broken sleep schedule",
            "recovering from burning yourself out",
        ],
        "emotions": ["exhaustion", "hope", "steadiness"],
        "keywords": ["routine", "discipline", "balance", "sleep schedule",
                     "sustainable", "pace", "moderation", "habits"],
    },
    "BG.2.20": {
        "summary": "States that what a person essentially is was never born and "
                   "does not die -- it is not something that comes into being and "
                   "then stops. Offered as a reason the fact of death is not the "
                   "catastrophe it appears to be.",
        "themes": ["death", "what does not die", "fear of ending",
                   "continuity", "mortality"],
        "situations": [
            "lying awake afraid of dying",
            "panicking about your own mortality",
            "being frightened after a health scare",
            "struggling with the idea that everything ends",
        ],
        "emotions": ["fear", "dread", "panic", "anxiety"],
        "keywords": ["death", "dying", "afraid", "mortality", "fear of death",
                     "terrified", "end", "scared"],
    },
    "BG.2.27": {
        "summary": "Points out plainly that death is certain for anyone who was "
                   "born, and birth certain for whatever dies, and concludes there "
                   "is no cause for grief in something so inevitable.",
        "themes": ["death is certain", "grief", "inevitability", "mourning",
                   "acceptance"],
        "situations": [
            "grieving a parent who has died",
            "unable to stop crying after losing someone",
            "afraid of losing someone close to you",
            "struggling to accept a death you saw coming",
        ],
        "emotions": ["grief", "sorrow", "fear", "acceptance"],
        "keywords": ["grief", "died", "death", "mourning", "loss", "lost someone",
                     "cannot stop grieving", "bereaved"],
    },
}


def insert(conn) -> int:
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    n = 0
    for verse_id, e in DEMO.items():
        conn.execute(
            """INSERT INTO enrichment (verse_id, summary, themes, situations,
                                       emotions, keywords, model, prompt_hash,
                                       generated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'hand-written-demo', 'demo', ?)
               ON CONFLICT(verse_id) DO UPDATE SET
                 summary=excluded.summary, themes=excluded.themes,
                 situations=excluded.situations, emotions=excluded.emotions,
                 keywords=excluded.keywords, model=excluded.model,
                 prompt_hash=excluded.prompt_hash""",
            (verse_id, e["summary"],
             json.dumps(e["themes"]), json.dumps(e["situations"]),
             json.dumps(e["emotions"]), json.dumps(e["keywords"]), now),
        )
        n += 1
    conn.commit()
    return n


def revert(conn) -> int:
    cur = conn.execute("DELETE FROM enrichment WHERE prompt_hash='demo'")
    conn.commit()
    return cur.rowcount


QUESTIONS = [
    ("why do people hate content creators they have never met in real life",
     ["BG.3.37", "BG.7.27", "BG.16.18"]),
    ("why do I lose my temper over small things",
     ["BG.2.62", "BG.2.63", "BG.3.37"]),
    ("I overeat and oversleep and cannot find any balance in my routine",
     ["BG.6.16", "BG.6.17"]),
    ("I am terrified of dying", ["BG.2.20", "BG.2.27"]),
    ("my father died and I cannot stop grieving", ["BG.2.27"]),
]


def probe(conn, label: str) -> None:
    index, _ = corpus.build_index(conn)
    print("\n--- %s ---" % label)
    for question, expected in QUESTIONS:
        hits = [h.doc_id for h in index.search(question, k=8)]
        found = [v for v in expected if v in hits]
        print("  %-2d/%-2d  %s" % (len(found), len(expected), question[:58]))
        if len(found) != len(expected):
            print("         missing %s" % [v for v in expected if v not in found])


def main() -> int:
    conn = db.connect()
    if "--revert" in sys.argv:
        print("removed %d demo enrichment rows" % revert(conn))
        probe(conn, "after revert")
        return 0

    probe(conn, "BEFORE: no enrichment, searching verse text only")
    n = insert(conn)
    print("\ninserted hand-written enrichment for %d verses (no API calls, $0)" % n)
    probe(conn, "AFTER: those verses have a plain-language note")

    print("\nNOTE: these verses were picked because the questions above expect")
    print("them, so this is a demonstration of the mechanism, not a measurement.")
    print("Run with --revert to undo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
