"""Translating journal events into OpenTelemetry's vocabulary.

The design being guarded here is a refusal: most of our events are *not*
translated, and that has to stay deliberate rather than drifting into
forced mappings that claim a meaning the convention doesn't carry. So the
tests care as much about what doesn't come out as what does.
"""

from __future__ import annotations

import unittest

from grokklings import otel


def event(type: str, seq: int = 1, task_id: str | None = "t1",
          slot: str | None = "triage", **data) -> dict:
    return {"seq": seq, "ts": 1000.0, "type": type, "task_id": task_id, "slot": slot, "data": data}


class TestProvider(unittest.TestCase):
    def test_the_registry_spells_xai_with_an_underscore(self):
        """`x_ai`, not `xai` — read off the attribute registry, not guessed."""
        self.assertEqual(otel.provider_for("grok-4.6"), "x_ai")

    def test_the_other_backends_map_too(self):
        self.assertEqual(otel.provider_for("claude-opus-5"), "anthropic")
        self.assertEqual(otel.provider_for("gpt-5-mini"), "openai")

    def test_an_unknown_model_gets_no_provider_rather_than_a_guess(self):
        """provider.name is required and has a registry of allowed values.
        Inventing one produces a span that looks conformant and isn't."""
        self.assertIsNone(otel.provider_for("llama-3"))
        self.assertIsNone(otel.provider_for(None))


class TestTranslation(unittest.TestCase):
    def test_an_assignment_becomes_invoke_agent(self):
        span = otel.to_span(event("task_assigned", instance="triage#0"))

        self.assertEqual(span["attributes"][otel.Attr.OPERATION], otel.Op.INVOKE_AGENT)
        self.assertEqual(span["attributes"][otel.Attr.AGENT_NAME], "triage")
        self.assertEqual(span["attributes"][otel.Attr.AGENT_ID], "triage#0")
        self.assertEqual(span["attributes"][otel.Attr.CONVERSATION], "t1")

    def test_the_span_is_named_the_way_the_convention_says(self):
        """"{operation} {agent name}"."""
        span = otel.to_span(event("task_assigned", instance="triage#0"))
        self.assertEqual(span["name"], "invoke_agent triage")

    def test_a_model_call_carries_usage_which_is_where_cost_comes_from(self):
        span = otel.to_span(
            event("worker_note", slot="screen", instance="screen#0",
                  message="model response received", model="grok-4.6",
                  input_tokens=1200, output_tokens=340)
        )
        attrs = span["attributes"]

        self.assertEqual(attrs[otel.Attr.OPERATION], otel.Op.CHAT)
        self.assertEqual(attrs[otel.Attr.PROVIDER], "x_ai")
        self.assertEqual(attrs[otel.Attr.MODEL], "grok-4.6")
        self.assertEqual(attrs[otel.Attr.INPUT_TOKENS], 1200)
        self.assertEqual(attrs[otel.Attr.OUTPUT_TOKENS], 340)

    def test_an_ordinary_worker_note_is_not_a_chat_span(self):
        """worker_note is free-form. Only the ones carrying a model and
        usage are a model call; the rest would be spans about nothing."""
        self.assertIsNone(otel.to_span(event("worker_note", message="just a note")))

    def test_a_spawned_instance_becomes_create_agent(self):
        span = otel.to_span(event("instance_spawned", task_id=None, instance="triage#ab12"))
        self.assertEqual(span["attributes"][otel.Attr.OPERATION], otel.Op.CREATE_AGENT)
        self.assertEqual(span["attributes"][otel.Attr.AGENT_ID], "triage#ab12")

    def test_the_router_choosing_is_a_planning_step(self):
        span = otel.to_span(event("slot_picked", slot="deep_dive", purpose="not_mine"))
        self.assertEqual(span["attributes"][otel.Attr.OPERATION], otel.Op.PLAN)

    def test_a_deliberate_fail_verdict_sets_the_stable_error_attribute(self):
        span = otel.to_span(
            event("worker_result", instance="triage#0", decision="fail", reason="bad data")
        )
        self.assertEqual(span["attributes"][otel.Attr.ERROR_TYPE], "bad data")

    def test_the_other_verdicts_are_outcomes_not_errors(self):
        for decision in ("done", "next", "not_mine"):
            with self.subTest(decision=decision):
                span = otel.to_span(event("worker_result", instance="triage#0", decision=decision))
                self.assertNotIn(otel.Attr.ERROR_TYPE, span["attributes"])

    def test_absent_fields_are_left_out_rather_than_sent_as_null(self):
        span = otel.to_span(event("task_assigned", task_id=None, slot=None))
        self.assertNotIn(otel.Attr.AGENT_NAME, span["attributes"])
        self.assertNotIn(otel.Attr.CONVERSATION, span["attributes"])
        self.assertEqual(span["name"], "invoke_agent")


class TestWhatStaysOurs(unittest.TestCase):
    def test_our_own_vocabulary_is_not_translated(self):
        """These carry meanings the convention has no home for. A forced
        mapping would claim something it doesn't say."""
        for name in ("task_unplaced", "task_looped", "step_replayed", "proposal_made",
                     "scaling_decision", "task_scored", "judge_calibrated", "task_duplicate"):
            with self.subTest(event=name):
                self.assertIsNone(otel.to_span(event(name)))

    def test_every_untranslated_name_is_listed_on_purpose(self):
        """The split should be a decision someone wrote down, not whatever
        happened to be left over."""
        overlap = set(otel.MAPPING) & set(otel.OURS_ALONE)
        self.assertEqual(overlap, set(), "an event can't be both translated and ours alone")

    def test_the_listed_names_are_the_ones_the_code_actually_records(self):
        """Guards against the list going stale as events are added."""
        import ast
        import pathlib

        recorded = set()
        for path in pathlib.Path("grokklings").rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "record" and node.args
                        and isinstance(node.args[0], ast.Constant)):
                    recorded.add(node.args[0].value)

        accounted = set(otel.MAPPING) | set(otel.OURS_ALONE)
        self.assertEqual(
            recorded - accounted, set(),
            "these events are recorded but neither translated nor listed as ours",
        )


class TestCoverage(unittest.TestCase):
    def test_it_reports_how_much_was_left_untranslated(self):
        events = [
            event("task_assigned", instance="triage#0"),
            event("task_unplaced"),
            event("task_looped"),
        ]
        self.assertEqual(
            otel.coverage(events), {"exported": 1, "ours_only": 2, "total": 3}
        )

    def test_spans_skips_what_it_cannot_translate(self):
        events = [event("task_unplaced"), event("task_assigned", instance="a"), event("alert")]
        self.assertEqual(len(list(otel.spans(events))), 1)


if __name__ == "__main__":
    unittest.main()
