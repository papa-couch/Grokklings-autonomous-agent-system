"""The Grok worker's request shape and response handling.

The real call needs a key and the network; neither is available here (open
question 4). What *is* checkable without them is everything around the
call: that the request carries the schema the verdict is parsed against,
that the slot's config reaches the request, and that the response is turned
into a verdict — or into an honest error. A wrong request shape in the
colony's primary backend would otherwise surface as every user's first
crash.
"""

from __future__ import annotations

import unittest
import unittest.mock
from types import SimpleNamespace
from typing import Any

from grokklings.handlers import grok
from grokklings.model import Decision, SlotSpec, Task
from grokklings.slots import WorkerContext, WorkerCrashed


class FakeJournal:
    def __init__(self) -> None:
        self.records: list[tuple[str, dict[str, Any]]] = []

    def record(self, type: str, task_id: str | None = None, slot: str | None = None, **data: Any) -> int:
        self.records.append((type, data))
        return len(self.records)


class FakeClient:
    """Stands in for openai.AsyncOpenAI, recording what it was asked for."""

    def __init__(self, content: str, model: str = "grok-4.6") -> None:
        self.content = content
        self.model = model
        self.request: dict[str, Any] = {}
        self.timeout: float | None = None
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def with_options(self, timeout: float) -> "FakeClient":
        self.timeout = timeout
        return self

    async def _create(self, **kwargs: Any) -> Any:
        self.request = kwargs
        return SimpleNamespace(
            model=self.model,
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))],
            usage=SimpleNamespace(prompt_tokens=11, completion_tokens=22),
        )


class GrokWorkerCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.journal = FakeJournal()
        self.addCleanup(setattr, grok, "_client", None)

    def context(self, **extra: Any) -> WorkerContext:
        spec = SlotSpec(
            name="screen",
            description="first-pass screening",
            context="Drop the junk.",
            timeout=120,
            **extra,
        )
        return WorkerContext(slot=spec, instance="screen#0", journal=self.journal)

    def install(self, content: str) -> FakeClient:
        client = FakeClient(content)
        grok._client = client
        return client

    async def test_request_carries_the_schema_the_verdict_is_parsed_against(self):
        client = self.install('{"decision": "done", "result": {"verdict": "junk"}, '
                              '"next_slot": null, "reason": null}')
        await grok.handle(Task(id="1", source="manual", payload={"url": "u"}), self.context())

        fmt = client.request["response_format"]
        self.assertEqual(fmt["type"], "json_schema")
        self.assertTrue(fmt["json_schema"]["strict"])
        self.assertEqual(fmt["json_schema"]["schema"], grok.VERDICT_SCHEMA)

    async def test_slot_config_reaches_the_request(self):
        client = self.install('{"decision": "done", "result": {}, "next_slot": null, "reason": null}')
        ctx = self.context(options={"model": "grok-4.6-fast", "max_tokens": 512})
        await grok.handle(Task(id="1", source="manual", payload={}), ctx)

        self.assertEqual(client.request["model"], "grok-4.6-fast")
        self.assertEqual(client.request["max_tokens"], 512)
        # The client is given less time than the slot, so the SDK raises its
        # own error instead of the slot's timeout cutting the call off.
        self.assertEqual(client.timeout, 115.0)

    async def test_the_prompt_is_split_into_system_and_user(self):
        client = self.install('{"decision": "done", "result": {}, "next_slot": null, "reason": null}')
        ctx = self.context(can_route_to=["deep_dive"])
        await grok.handle(Task(id="7", source="hook", payload={"url": "u"}), ctx)

        system, user = client.request["messages"]
        self.assertEqual(system["role"], "system")
        self.assertIn("Drop the junk.", system["content"])
        self.assertIn("deep_dive", system["content"])
        self.assertEqual(user["role"], "user")
        self.assertIn("Task 7", user["content"])

    async def test_a_routing_verdict_comes_back_parsed(self):
        self.install('{"decision": "next", "result": {"flag": "worth a look"}, '
                     '"next_slot": "deep_dive", "reason": null}')
        verdict = await grok.handle(
            Task(id="1", source="manual", payload={}), self.context(can_route_to=["deep_dive"])
        )

        self.assertEqual(verdict.decision, Decision.NEXT)
        self.assertEqual(verdict.next_slot, "deep_dive")
        self.assertEqual(verdict.result, {"flag": "worth a look"})

    async def test_usage_is_journalled_for_cost_tracking(self):
        self.install('{"decision": "done", "result": {}, "next_slot": null, "reason": null}')
        await grok.handle(Task(id="1", source="manual", payload={}), self.context())

        types = [t for t, _ in self.journal.records]
        self.assertIn("worker_note", types)
        note = next(data for t, data in self.journal.records if t == "worker_note")
        self.assertEqual((note["input_tokens"], note["output_tokens"]), (11, 22))

    async def test_an_empty_response_is_an_error_not_a_silent_verdict(self):
        self.install("   ")
        with self.assertRaisesRegex(ValueError, "empty response"):
            await grok.handle(Task(id="1", source="manual", payload={}), self.context())

    async def test_a_missing_key_names_what_is_missing(self):
        grok._client = None
        with unittest.mock.patch.dict("os.environ", {}, clear=True):
            with unittest.mock.patch.dict("sys.modules", {"openai": SimpleNamespace()}):
                with self.assertRaisesRegex(WorkerCrashed, "XAI_API_KEY"):
                    grok._get_client()


if __name__ == "__main__":
    unittest.main()
