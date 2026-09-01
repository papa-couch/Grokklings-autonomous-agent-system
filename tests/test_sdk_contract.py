"""Our call shapes, checked against the SDKs we actually ship against.

Every other test of a model backend hands it a stub that accepts anything.
That is the right shape for testing our own logic, and it is also exactly
how a real integration breaks with the whole suite green: GEPA's adapter
passed fifteen tests written against its documented interface and optimized
nothing, because 0.1.4 reads an attribute the documentation calls optional.
A stub cannot notice that. Only the real thing can.

These tests close as much of that gap as is reachable without a key, from
both directions:

**The request.** The keyword arguments our production code actually sends
are bound against the installed SDK's real `create` signature. Neither
`chat.completions.create` nor `beta.messages.create` takes `**kwargs`, so a
renamed or dropped parameter is a `TypeError` at call time — and three of
our five call sites catch `Exception` broadly and carry on without the
model, which means that `TypeError` would not crash anything. The router
would simply stop routing, the judge would stop scoring, and the colony
would look like it was running fine.

Measured, not assumed: adding one bogus keyword to the Grok worker's call
leaves all seven of `test_grok.py`'s tests green, and turns exactly one
test in the whole 248-test suite red — the one below.

**The response.** Our code is handed a response built from the SDK's own
response models rather than a hand-built namespace. The stubs elsewhere do
catch a field we read that we never put on them, so this is the smaller of
the two gaps — but it is a real one, and it runs the other way: a namespace
is written from our reading of the SDK and frozen there, so it goes on
agreeing with *us* after the SDK moves. Were `usage.prompt_tokens` renamed
upstream, every stub would still carry the old name and stay green.

Nothing here reaches the network: the transport is replaced, the signature
and the models are the real ones. Both SDKs are optional extras, so the
whole module skips when they aren't installed and the stdlib-only core
([0001](../docs/decisions/0001-python-stdlib.md)) still tests clean.

What this still does not prove is that xAI's server accepts what the SDK
accepts, or that a real Grok returns JSON matching our schema. That needs a
key — open question 4.
"""

from __future__ import annotations

import inspect
import unittest
from types import SimpleNamespace
from typing import Any

from grokklings.model import SlotSpec, Task
from grokklings.slots import WorkerContext

try:  # both backends are optional extras
    import openai
except ModuleNotFoundError:  # pragma: no cover - depends on the environment
    openai = None

try:
    import anthropic
except ModuleNotFoundError:  # pragma: no cover - depends on the environment
    anthropic = None

needs_openai = unittest.skipIf(openai is None, "the openai SDK isn't installed")
needs_anthropic = unittest.skipIf(anthropic is None, "the anthropic SDK isn't installed")


class FakeJournal:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def record(self, type: str, task_id: str | None = None, slot: str | None = None, **data: Any) -> int:
        self.records.append(data)
        return len(self.records)


def chat_completion(content: str, model: str = "grok-4.6") -> Any:
    """A response built from the SDK's own model, not a stand-in."""
    from openai.types.chat import ChatCompletion

    return ChatCompletion.model_validate(
        {
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "created": 0,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": content},
                }
            ],
            "usage": {"prompt_tokens": 11, "completion_tokens": 22, "total_tokens": 33},
        }
    )


def beta_message(text: str, model: str = "claude-opus-4-5") -> Any:
    from anthropic.types.beta import BetaMessage

    return BetaMessage.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 11, "output_tokens": 22},
        }
    )


class Recorder:
    """Records the request, answers with a real response model."""

    def __init__(self, response: Any) -> None:
        self.response = response
        self.request: dict[str, Any] = {}
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def with_options(self, timeout: float) -> "Recorder":
        return self

    async def _create(self, **kwargs: Any) -> Any:
        self.request = kwargs
        return self.response


def openai_create() -> Any:
    return openai.AsyncOpenAI(base_url="https://api.x.ai/v1", api_key="unused").chat.completions.create


def anthropic_create() -> Any:
    return anthropic.AsyncAnthropic(api_key="unused").beta.messages.create


class ContractCase(unittest.IsolatedAsyncioTestCase):
    def assertBinds(self, fn: Any, request: dict[str, Any]) -> None:
        """The real SDK would accept this call.

        `bind` raises exactly where the SDK would: an unexpected keyword, or
        a required one missing.
        """
        self.assertTrue(request, "nothing was recorded — the call never happened")
        try:
            inspect.signature(fn).bind(**request)
        except TypeError as exc:
            self.fail(f"the installed SDK would reject this call: {exc}")

    def context(self, **extra: Any) -> WorkerContext:
        spec = SlotSpec(name="screen", description="first pass", timeout=120, **extra)
        return WorkerContext(slot=spec, instance="screen#0", journal=FakeJournal())

    def task(self) -> Task:
        return Task(id="t1", source="manual", payload={"url": "https://a"})


DONE = '{"decision": "done", "result": {"verdict": "junk"}, "next_slot": null, "reason": null}'


@needs_openai
class TestGrokWorker(ContractCase):
    """The primary backend. A wrong shape here is every user's first crash."""

    async def test_the_request_binds_against_the_real_signature(self):
        from grokklings.handlers import grok

        self.addCleanup(setattr, grok, "_client", None)
        grok._client = recorder = Recorder(chat_completion(DONE))
        await grok.handle(self.task(), self.context())

        self.assertBinds(openai_create(), recorder.request)

    async def test_the_response_fields_we_read_exist_on_the_real_model(self):
        """The token counts the cost guard bills against are read straight
        off the response. Here they come off the SDK's own model."""
        from grokklings.handlers import grok

        self.addCleanup(setattr, grok, "_client", None)
        grok._client = Recorder(chat_completion(DONE))
        ctx = self.context()
        verdict = await grok.handle(self.task(), ctx)

        self.assertEqual(verdict.result["verdict"], "junk")
        noted = ctx.journal.records[-1]
        self.assertEqual(noted["model"], "grok-4.6")
        self.assertEqual(noted["input_tokens"], 11)
        self.assertEqual(noted["output_tokens"], 22)


@needs_openai
class TestRouter(ContractCase):
    """The router swallows every exception so a colony without a key keeps
    running. That also swallows a wrong request shape — forever, and
    silently. This is the test that would notice."""

    async def test_the_request_binds_against_the_real_signature(self):
        from grokklings import routing

        router = routing.GrokRouter()
        router._client = recorder = Recorder(chat_completion('{"slot": "deep_dive"}'))
        picked = await router.pick(
            self.task(),
            [SlotSpec(name="triage"), SlotSpec(name="deep_dive")],
        )

        self.assertEqual(picked, "deep_dive")  # not None: the call went through
        self.assertBinds(openai_create(), recorder.request)


@needs_openai
class TestEvaluator(ContractCase):
    """Same swallow, same risk: a miscalled judge reports "couldn't tell"
    on every task, which is indistinguishable from an honest failure."""

    async def test_the_request_binds_against_the_real_signature(self):
        from grokklings import evaluate

        ev = evaluate.GrokEvaluator()
        ev._client = recorder = Recorder(
            chat_completion('{"score": 0.9, "met": true, "feedback": "ok", "violations": []}')
        )
        task = self.task()
        task.route.append("triage")
        task.add("triage", "triage#0", {"verdict": "worth a look"})
        got = await ev.score(task, evaluate.Criteria(looking_for="signal"))

        self.assertIsNotNone(got)  # not None: the call went through
        self.assertBinds(openai_create(), recorder.request)


@needs_openai
class TestBrain(ContractCase):
    async def test_the_request_binds_against_the_real_signature(self):
        from grokklings import brain

        it = brain.GrokBrain()
        it._client = recorder = Recorder(chat_completion('{"proposals": []}'))
        await it.propose("evidence", [SlotSpec(name="triage")])

        self.assertBinds(openai_create(), recorder.request)


@needs_openai
class TestReflectionModel(unittest.TestCase):
    """GEPA's teacher. Synchronous by GEPA's design, so it gets its own case."""

    def test_the_request_binds_against_the_real_signature(self):
        from grokklings import optimize
        from grokklings.handlers import grok

        self.addCleanup(setattr, grok, "_client", None)
        grok._client = recorder = Recorder(chat_completion("a better instruction"))

        got = optimize.reflection_lm()("rewrite this")

        self.assertEqual(got, "a better instruction")
        try:
            inspect.signature(openai_create()).bind(**recorder.request)
        except TypeError as exc:  # pragma: no cover - the failure message is the point
            self.fail(f"the installed SDK would reject this call: {exc}")


@needs_anthropic
class TestAnthropicWorker(ContractCase):
    """The secondary backend sends the newer parameters — `thinking`,
    `output_config`, `betas`, `fallbacks` — which is where a shape drifts
    first."""

    async def test_the_request_binds_against_the_real_signature(self):
        from grokklings.handlers import anthropic as backend

        self.addCleanup(setattr, backend, "_client", None)
        backend._client = recorder = Recorder(beta_message(DONE))
        await backend.handle(self.task(), self.context())

        self.assertBinds(anthropic_create(), recorder.request)

    async def test_a_refusal_is_read_off_the_real_response_model(self):
        """`stop_reason == "refusal"` becomes a failed verdict, not a crash."""
        from anthropic.types.beta import BetaMessage

        from grokklings.handlers import anthropic as backend

        refusal = BetaMessage.model_validate(
            {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-4-5",
                "content": [],
                "stop_reason": "refusal",
                "stop_sequence": None,
                "usage": {"input_tokens": 5, "output_tokens": 0},
            }
        )
        self.addCleanup(setattr, backend, "_client", None)
        backend._client = Recorder(refusal)
        verdict = await backend.handle(self.task(), self.context())

        self.assertEqual(verdict.decision.value, "fail")
        self.assertIn("declined", verdict.reason)


if __name__ == "__main__":
    unittest.main()
