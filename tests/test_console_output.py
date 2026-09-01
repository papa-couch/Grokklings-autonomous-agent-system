"""The console output — the one `grokklings init` generates by default.

Found untested during an audit, which mattered more than its six lines
suggest: it's what the starter config uses, so it was the first code path a
new user ran and the only one nothing checked.
"""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout

from grokklings.model import Decision, SlotSpec, Task
from grokklings.outputs import console
from grokklings.slots import WorkerContext


def finished_task() -> Task:
    task = Task(id="t1", source="hook", payload={"url": "https://a"}, dedup_key="https://a")
    task.route.append("triage")
    task.add("triage", "triage#0", {"verdict": "worth a look"})
    return task


def context(**options) -> WorkerContext:
    spec = SlotSpec(name="out", terminal=True, options=options)
    return WorkerContext(slot=spec, instance="out#0", journal=None)


class TestConsoleOutput(unittest.TestCase):
    def deliver(self, **options) -> tuple[dict, object]:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            verdict = console.handle(finished_task(), context(**options))
        return json.loads(buffer.getvalue()), verdict

    def test_it_prints_the_task_and_reports_done(self):
        body, verdict = self.deliver()

        self.assertEqual(body["task"], "t1")
        self.assertEqual(body["route"], ["triage"])
        self.assertEqual(verdict.decision, Decision.DONE)
        self.assertEqual(verdict.result, {"delivered_to": "stdout"})

    def test_the_default_shape_merges_the_result(self):
        body, _ = self.deliver()

        self.assertEqual(body["result"], {"triage": [{"verdict": "worth a look"}]})
        self.assertNotIn("payload", body)   # merged is a summary, not everything

    def test_the_full_shape_carries_the_payload_and_every_contribution(self):
        body, _ = self.deliver(shape="full")

        self.assertEqual(body["payload"], {"url": "https://a"})
        self.assertEqual(len(body["result"]), 1)
        self.assertEqual(body["result"][0]["slot"], "triage")

    def test_what_it_prints_is_valid_json_on_one_write(self):
        """It's a debugging output: something else is usually reading it."""
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            console.handle(finished_task(), context())
        text = buffer.getvalue()

        self.assertTrue(text.endswith("\n"))
        json.loads(text)   # raises if it isn't parseable


if __name__ == "__main__":
    unittest.main()
