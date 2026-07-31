"""End-to-end pipeline tests against a stub Anthropic client.

The reject-and-regenerate loop is the most safety-critical logic in the backend
and it is exactly the part that never runs during ordinary happy-path use. A
stub client lets us drive it deterministically -- including the case where the
model hallucinates a citation on the first attempt and corrects on the second --
without a credential and without spending anything.

    python scripts/test_pipeline.py
"""

import json
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gita.answer import generate as G  # noqa: E402
from gita.pipeline import Pipeline  # noqa: E402


# --- stub client ---------------------------------------------------------

@dataclass
class _Block:
    type: str
    text: str


@dataclass
class _Usage:
    input_tokens: int = 1000
    output_tokens: int = 300
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class _Message:
    content: list
    usage: _Usage
    stop_reason: str = "end_turn"
    stop_details: object = None


class StubMessages:
    def __init__(self, plan: dict, answers: list[str]):
        self.plan = plan
        self.answers = list(answers)
        self.calls: list[str] = []

    def create(self, **kwargs):
        # Query understanding is the call carrying a json_schema output_config.
        is_understand = "output_config" in kwargs
        self.calls.append("understand" if is_understand else "answer")
        if is_understand:
            payload = json.dumps(self.plan)
            return _Message([_Block("text", payload)], _Usage())
        text = self.answers.pop(0) if self.answers else "No answer."
        return _Message([_Block("text", text)], _Usage())


class StubClient:
    def __init__(self, plan: dict, answers: list[str]):
        self.messages = StubMessages(plan, answers)


PLAN = {
    "language": "en",
    "search_query": "envy resentment comparison anger desire strangers online",
    "themes": ["envy", "unfulfilled desire", "comparison"],
    "on_topic": True,
    "restated": "Why do I resent strangers online?",
}

QUESTION = "why do I resent people I have never met online"

failures = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global failures
    if not condition:
        failures += 1
    print("  [%s] %s%s" % ("PASS" if condition else "FAIL", label,
                           "" if condition else "  <- " + detail))


def main() -> int:
    base = Pipeline()
    # Retrieval runs on the EXPANDED query from stage 1, not the raw question.
    # Deriving the expected citable set from the raw question would make the
    # validator reject correct answers as out-of-context -- which is what it is
    # supposed to do, and is a test bug rather than a code bug.
    expanded = " ".join([PLAN["search_query"], *PLAN["themes"]])
    grounded = base.preview(expanded, k=5)
    citable = [h["verse_id"] for h in grounded["retrieved"]]
    good = citable[0].replace("BG.", "BG ")
    second = citable[1].replace("BG.", "BG ")
    print("retrieved for the test question: %s\n" % ", ".join(citable))

    # 1. Happy path -------------------------------------------------------
    print("1. clean answer accepted on first attempt")
    client = StubClient(PLAN, ["Resentment starts as wanting [%s]. It hardens "
                               "into dislike [%s]." % (good, second)])
    result = Pipeline(client=client).ask(QUESTION, k=5)
    check("ok", result.ok, result.status)
    check("attempts == 1", result.attempts == 1, str(result.attempts))
    check("citations captured", len(result.citations) == 2, str(result.citations))
    check("two model calls", client.messages.calls == ["understand", "answer"],
          str(client.messages.calls))

    # 2. Hallucinated citation, corrected on retry ------------------------
    print("\n2. hallucinated verse rejected, then corrected on retry")
    client = StubClient(PLAN, [
        "This is explained in [BG 2.99] clearly.",     # does not exist
        "Resentment is unfulfilled wanting [%s]." % good,
    ])
    result = Pipeline(client=client).ask(QUESTION, k=5)
    check("ok after retry", result.ok, result.status)
    check("attempts == 2", result.attempts == 2, str(result.attempts))
    check("bad citation absent from answer", "2.99" not in result.answer,
          result.answer)
    check("three model calls", client.messages.calls.count("answer") == 2,
          str(client.messages.calls))

    # 3. Out-of-context citation ------------------------------------------
    print("\n3. real verse that was not retrieved is rejected")
    outside = next(v for v in ("BG 18.66", "BG 9.22", "BG 4.7")
                   if v.replace("BG ", "BG.") not in citable)
    client = StubClient(PLAN, [
        "The answer is surrender [%s]." % outside,
        "Resentment is unfulfilled wanting [%s]." % good,
    ])
    result = Pipeline(client=client).ask(QUESTION, k=5)
    check("ok after retry", result.ok, result.status)
    check("out-of-context citation removed",
          outside.split()[1] not in result.answer, result.answer)

    # 4. Never recovers -> hard failure, no answer served -----------------
    print("\n4. unfixable citations fail closed rather than serving output")
    client = StubClient(PLAN, ["[BG 2.99]"] * 4)
    result = Pipeline(client=client).ask(QUESTION, k=5)
    check("not ok", not result.ok, result.status)
    check("status is citation_validation_failed",
          result.status == "citation_validation_failed", result.status)
    check("answer text withheld", result.answer == "", repr(result.answer))
    check("retries bounded at 3 attempts",
          client.messages.calls.count("answer") == 3,
          str(client.messages.calls.count("answer")))

    # 5. Uncited answer rejected ------------------------------------------
    print("\n5. answer with no citations at all is rejected")
    client = StubClient(PLAN, ["Hate comes from inside you, not from them."] * 4)
    result = Pipeline(client=client).ask(QUESTION, k=5)
    check("not ok", not result.ok, result.status)
    check("answer withheld", result.answer == "", repr(result.answer))

    # 6. Off-topic short circuit ------------------------------------------
    print("\n6. off-topic question short-circuits before retrieval")
    off = dict(PLAN, on_topic=False)
    client = StubClient(off, ["should never be reached"])
    result = Pipeline(client=client).ask("what is the weather in Ahmedabad")
    check("status is off_topic", result.status == "off_topic", result.status)
    check("no answer call made", "answer" not in client.messages.calls,
          str(client.messages.calls))

    # 7. Language routing --------------------------------------------------
    print("\n7. non-English question routes language through to the result")
    hindi = dict(PLAN, language="hi")
    client = StubClient(hindi, ["क्रोध इच्छा से आता है [%s]।" % good])
    result = Pipeline(client=client).ask("mujhe gussa kyon aata hai")
    check("language is hi", result.language == "hi", result.language)
    check("ok", result.ok, result.status)

    # 8. Missing credentials -> typed failure, not a traceback ------------
    print("\n8. absent credential reports cleanly")
    import os
    had = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        result = Pipeline().ask(QUESTION)
        check("status is no_credentials", result.status == "no_credentials",
              result.status)
        check("detail names the fix", "ANTHROPIC_API_KEY" in result.detail,
              result.detail)
    except Exception as exc:  # noqa: BLE001
        check("no raw exception escapes", False, "%s: %s" % (type(exc).__name__, exc))
    finally:
        if had:
            os.environ["ANTHROPIC_API_KEY"] = had

    # 9. Empty question ----------------------------------------------------
    print("\n9. empty question rejected without any model call")
    client = StubClient(PLAN, ["unreachable"])
    result = Pipeline(client=client).ask("   ")
    check("status is empty_question", result.status == "empty_question",
          result.status)
    check("no model calls", client.messages.calls == [],
          str(client.messages.calls))

    # 10. History persists the answer text, not just the question ---------
    # record_history() existed with nothing ever calling it -- the history
    # table, GET /history, and the sidebar UI were all live and all silently
    # empty. This is the regression test for that: a reload has to be able to
    # restore the last answer, which means the answer text itself has to be
    # in the row, not just the question and citation ids.
    print("\n10. record_history persists question, answer, and citations")
    client = StubClient(PLAN, ["Desire denied becomes anger [%s]." % good])
    pipeline = Pipeline(client=client)
    result = pipeline.ask(QUESTION, k=5)
    pipeline.record_history(result)
    logged = pipeline.history(limit=1)[0]
    check("question logged", logged["question"] == QUESTION, logged["question"])
    check("answer text logged", logged["answer"] == result.answer,
          repr(logged["answer"]))
    check("citations logged", logged["citations"] == result.citations,
          str(logged["citations"]))
    # This suite runs against the real store, not a fixture, so it has to
    # leave no trace. Targets `local` (where history now lives) and matches
    # on the question rather than MAX(id) -- deleting the highest id would
    # remove whatever the person using the app asked most recently if this
    # ever ran while the server was up.
    pipeline.local.execute("DELETE FROM history WHERE question = ?", (QUESTION,))
    pipeline.local.commit()
    pipeline.close()

    print()
    if failures:
        print("%d FAILURE(S)" % failures)
        return 1
    print("All pipeline tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
