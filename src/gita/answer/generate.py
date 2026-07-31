"""Query understanding and answer generation against the Anthropic API.

Two model calls per question:

  1. understand -- structured output; detects language and rewrites the
     question into corpus vocabulary for BM25.
  2. answer     -- prose with citations, validated and retried on failure.

The retry loop is the point. A model that invents `[BG 4.19]` produces output
indistinguishable from a correct answer, so validation is not advisory: an
answer that fails it never reaches the caller.
"""

import json
from dataclasses import dataclass, field

from . import context as C
from . import prompts as P
from . import validate as V

DEFAULT_MODEL = "claude-opus-5"

# Query understanding is a short, well-specified transformation on the hot path
# of every question. Low effort is the documented latency lever and does not
# change model tier.
UNDERSTAND_EFFORT = "low"
UNDERSTAND_MAX_TOKENS = 4000

# Answer generation runs at the API default effort. max_tokens leaves room for
# thinking, which is on by default and shares this ceiling with the response.
ANSWER_MAX_TOKENS = 8000

MAX_CITATION_RETRIES = 2


@dataclass
class QueryPlan:
    language: str
    search_query: str
    themes: list[str]
    on_topic: bool
    restated: str

    @property
    def retrieval_query(self) -> str:
        return " ".join([self.search_query, *self.themes])


@dataclass
class GeneratedAnswer:
    text: str
    citations: list[str]
    attempts: int
    validation: V.Report
    usage: dict = field(default_factory=dict)
    rejected: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.validation.ok


class MissingCredentialsError(RuntimeError):
    """No usable Anthropic credential could be resolved."""


class RefusedError(RuntimeError):
    def __init__(self, msg, details=None):
        super().__init__(msg)
        self.details = details


_NO_CREDS_HINT = (
    "no Anthropic credential found -- set ANTHROPIC_API_KEY, or run "
    "`ant auth login`. Retrieval and --preview work without one."
)


def _client(client=None):
    if client is not None:
        return client
    import anthropic
    try:
        return anthropic.Anthropic()
    except TypeError as exc:
        raise MissingCredentialsError(_NO_CREDS_HINT) from exc


def _create(cli, **kwargs):
    """Issue a request, translating the SDK's auth TypeError into our own.

    The credential check happens when headers are built for an outbound
    request, not when the client is constructed -- so a client with no
    resolvable auth constructs happily and raises a bare TypeError here. Both
    sites need translating; only wrapping the constructor leaves a raw
    traceback escaping to the caller.
    """
    try:
        return cli.messages.create(**kwargs)
    except TypeError as exc:
        if "authentication method" in str(exc):
            raise MissingCredentialsError(_NO_CREDS_HINT) from exc
        raise


def _stream(cli, **kwargs):
    """Streaming twin of _create, with the same auth-error translation."""
    try:
        return cli.messages.stream(**kwargs)
    except TypeError as exc:
        if "authentication method" in str(exc):
            raise MissingCredentialsError(_NO_CREDS_HINT) from exc
        raise


def _text_of(message) -> str:
    return "".join(b.text for b in message.content if b.type == "text")


def _usage_of(message) -> dict:
    u = message.usage
    return {
        "input_tokens": u.input_tokens,
        "output_tokens": u.output_tokens,
        "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
        "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
    }


def understand(question: str, *, client=None, model: str = DEFAULT_MODEL) -> QueryPlan:
    message = _create(
        _client(client),
        model=model,
        max_tokens=UNDERSTAND_MAX_TOKENS,
        system=[{
            "type": "text",
            "text": P.QUERY_SYSTEM,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": P.build_query_turn(question)}],
        output_config={
            "format": {"type": "json_schema", "schema": P.QUERY_SCHEMA},
            "effort": UNDERSTAND_EFFORT,
        },
    )
    # stop_reason must be checked before reading content: a refusal returns
    # HTTP 200 with empty or partial content.
    if message.stop_reason == "refusal":
        raise RefusedError("query understanding was declined", message.stop_details)

    data = json.loads(_text_of(message))
    return QueryPlan(
        language=data["language"],
        search_query=data["search_query"],
        themes=data["themes"],
        on_topic=data["on_topic"],
        restated=data["restated"],
    )


def answer(
    question: str,
    ctx: C.Context,
    *,
    language: str = "en",
    valid_ids: set[str],
    client=None,
    model: str = DEFAULT_MODEL,
    max_retries: int = MAX_CITATION_RETRIES,
) -> GeneratedAnswer:
    """Generate an answer, validating citations and retrying on rejection."""
    cli = _client(client)
    citable = C.citable_list(ctx)
    context_ids = ctx.verse_ids

    messages = [{
        "role": "user",
        "content": P.build_answer_turn(question, ctx.text, citable, language),
    }]

    totals = {"input_tokens": 0, "output_tokens": 0,
              "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    rejected: list[str] = []
    report = V.Report()
    text = ""

    for attempt in range(1, max_retries + 2):
        message = _create(
            cli,
            model=model,
            max_tokens=ANSWER_MAX_TOKENS,
            system=[{
                "type": "text",
                "text": P.ANSWER_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=messages,
        )
        if message.stop_reason == "refusal":
            raise RefusedError("answer generation was declined", message.stop_details)

        for key, value in _usage_of(message).items():
            totals[key] += value

        text = _text_of(message).strip()
        report = V.validate(text, valid_ids=valid_ids, context_ids=context_ids)
        if report.ok:
            return GeneratedAnswer(text, V.cited_verse_ids(text), attempt,
                                   report, totals, rejected)

        rejected.append("attempt %d: %s" % (attempt, report.summary()))
        if attempt > max_retries:
            break

        problems = (
            "no citations were present"
            if report.uncited
            else "\n".join("  %s -> %s" % (c.raw, c.verdict.value) for c in report.bad)
        )
        messages += [
            {"role": "assistant", "content": text},
            {"role": "user", "content": P.build_retry_turn(problems, citable)},
        ]

    # Exhausted retries. Return the failure rather than serving unverified
    # output; the caller decides what the user sees.
    return GeneratedAnswer(text, V.cited_verse_ids(text), max_retries + 1,
                           report, totals, rejected)


def answer_stream(
    question: str,
    ctx: C.Context,
    *,
    language: str = "en",
    valid_ids: set[str],
    client=None,
    model: str = DEFAULT_MODEL,
    max_retries: int = MAX_CITATION_RETRIES,
):
    """Same contract as answer(), yielding text as it is produced.

    THE CITATION GUARANTEE UNDER STREAMING. Citations can only be checked
    against a finished answer -- a half-written sentence has nothing to
    verify -- so streaming necessarily shows text before it has been
    validated. That is a real change from answer(), which withholds
    everything until the check passes, and it is the reason this is a
    separate function rather than a flag: the non-streaming path keeps its
    original all-or-nothing behaviour untouched.

    What is preserved is the part that matters. Deltas are emitted tagged as
    provisional, the caller is required to present them as unverified, and
    nothing is ever presented as a checked answer until ("done", result)
    arrives with report.ok. If validation fails and a retry begins, a
    ("reset", reason) event is emitted first and the caller must discard
    everything shown so far -- a rejected draft is never allowed to stand.
    If every attempt fails, ("failed", result) is emitted and the text is
    withheld exactly as in answer().

    Events: ("delta", str) | ("reset", str) | ("done", GeneratedAnswer)
            | ("failed", GeneratedAnswer)
    """
    cli = _client(client)
    citable = C.citable_list(ctx)
    context_ids = ctx.verse_ids

    messages = [{
        "role": "user",
        "content": P.build_answer_turn(question, ctx.text, citable, language),
    }]

    totals = {"input_tokens": 0, "output_tokens": 0,
              "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    rejected: list[str] = []
    report = V.Report()
    text = ""

    for attempt in range(1, max_retries + 2):
        text = ""
        with _stream(
            cli,
            model=model,
            max_tokens=ANSWER_MAX_TOKENS,
            system=[{
                "type": "text",
                "text": P.ANSWER_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=messages,
        ) as stream:
            for chunk in stream.text_stream:
                text += chunk
                yield ("delta", chunk)
            message = stream.get_final_message()

        if message.stop_reason == "refusal":
            raise RefusedError("answer generation was declined", message.stop_details)

        for key, value in _usage_of(message).items():
            totals[key] += value

        text = text.strip()
        report = V.validate(text, valid_ids=valid_ids, context_ids=context_ids)
        if report.ok:
            yield ("done", GeneratedAnswer(text, V.cited_verse_ids(text), attempt,
                                           report, totals, rejected))
            return

        rejected.append("attempt %d: %s" % (attempt, report.summary()))
        if attempt > max_retries:
            break

        # Tell the caller to throw away what it has shown before the next
        # attempt starts writing over it.
        yield ("reset", report.summary())

        problems = (
            "no citations were present"
            if report.uncited
            else "\n".join("  %s -> %s" % (c.raw, c.verdict.value) for c in report.bad)
        )
        messages += [
            {"role": "assistant", "content": text},
            {"role": "user", "content": P.build_retry_turn(problems, citable)},
        ]

    yield ("failed", GeneratedAnswer(text, V.cited_verse_ids(text), max_retries + 1,
                                     report, totals, rejected))
