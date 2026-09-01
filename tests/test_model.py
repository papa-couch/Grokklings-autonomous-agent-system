from __future__ import annotations

import unittest

from grokklings.handlers._contract import VERDICT_SCHEMA, build_message, build_system
from grokklings.model import Decision, SlotSpec, Task, Verdict
from grokklings.slots import WorkerContext


class TestVerdict(unittest.TestCase):
    def test_next_requires_a_slot(self):
        with self.assertRaisesRegex(ValueError, "must name a slot"):
            Verdict(Decision.NEXT)

    def test_refusal_requires_a_reason(self):
        with self.assertRaisesRegex(ValueError, "must give a reason"):
            Verdict(Decision.NOT_MINE)

    def test_parse_accepts_both_next_spellings(self):
        self.assertEqual(Verdict.parse({"decision": "next", "next": "b"}).next_slot, "b")
        self.assertEqual(Verdict.parse({"decision": "next", "next_slot": "b"}).next_slot, "b")

    def test_parse_rejects_non_objects(self):
        with self.assertRaisesRegex(ValueError, "must be an object"):
            Verdict.parse("done")

    def test_parse_matches_the_schema_the_model_is_given(self):
        shaped = {"decision": "done", "result": {"output": 1}, "next_slot": None, "reason": None}
        self.assertEqual(Verdict.parse(shaped).decision, Decision.DONE)
        self.assertEqual(set(VERDICT_SCHEMA["required"]), set(shaped))


class TestTask(unittest.TestCase):
    def test_result_accumulates_and_is_never_overwritten(self):
        task = Task(id="1", source="manual", payload={})
        task.add("a", "a#0", {"count": 1})
        task.add("b", "b#0", {"count": 2})
        task.add("a", "a#1", {"count": 3})

        self.assertEqual(len(task.result), 3)
        self.assertEqual(task.merged_result(), {"a": [{"count": 1}, {"count": 3}], "b": [{"count": 2}]})

    def test_empty_contribution_is_skipped(self):
        task = Task(id="1", source="manual", payload={})
        task.add("a", "a#0", None)
        self.assertEqual(task.result, [])

    def test_round_trip_through_storage_keeps_everything(self):
        task = Task(id="1", source="manual", payload={"url": "u"}, dedup_key="u", hops=2)
        task.route.append("a")
        task.add("a", "a#0", {"x": 1})
        restored = Task.from_row(task.to_row())
        self.assertEqual(restored.route, ["a"])
        self.assertEqual(restored.hops, 2)
        self.assertEqual(restored.merged_result(), {"a": [{"x": 1}]})


class TestWorkerPrompt(unittest.TestCase):
    def context(self, **extra):
        spec = SlotSpec(name="triage", description="first-pass screening", context="Screen out scams.", **extra)
        return WorkerContext(slot=spec, instance="triage#0", journal=None)

    def test_system_carries_contract_and_user_context(self):
        text = build_system(self.context(can_route_to=["deep_dive"]))
        self.assertIn("Screen out scams.", text)
        self.assertIn("deep_dive", text)
        self.assertIn('decision="not_mine"', text)

    def test_without_routes_the_worker_is_told_so(self):
        self.assertIn("nowhere to hand off to", build_system(self.context()))

    def test_message_shows_accumulated_result_only_when_there_is_one(self):
        task = Task(id="1", source="manual", payload={"url": "u"})
        self.assertNotIn("Already accumulated", build_message(task))
        task.route.append("triage")
        task.add("triage", "triage#0", {"verdict": "looks good"})
        self.assertIn("Already accumulated", build_message(task))


if __name__ == "__main__":
    unittest.main()
