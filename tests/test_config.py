from __future__ import annotations

import unittest

from grokklings.config import ConfigError, parse
from tests.helpers import slot


class TestConfigValidation(unittest.TestCase):
    def test_colony_without_slots_is_rejected(self):
        with self.assertRaisesRegex(ConfigError, "no worker slots"):
            parse({})

    def test_ambiguous_entry_is_rejected(self):
        with self.assertRaisesRegex(ConfigError, "where a new task goes"):
            parse({"slot": [slot("a", "done"), slot("b", "done")]})

    def test_single_slot_becomes_the_entry(self):
        self.assertEqual(parse({"slot": [slot("a", "done")]}).entry, "a")

    def test_entry_flag_wins_over_order(self):
        config = parse({"slot": [slot("a", "done"), slot("b", "done", entry=True)]})
        self.assertEqual(config.entry, "b")

    def test_two_entry_flags_are_rejected(self):
        with self.assertRaisesRegex(ConfigError, "several slots marked"):
            parse({"slot": [slot("a", "done", entry=True), slot("b", "done", entry=True)]})

    def test_route_to_unknown_slot_is_rejected(self):
        with self.assertRaisesRegex(ConfigError, "non-existent slots"):
            parse({"slot": [slot("a", "done", can_route_to=["no-such-slot"])]})

    def test_duplicate_names_are_rejected(self):
        with self.assertRaisesRegex(ConfigError, "repeat"):
            parse({"slot": [slot("a", "done"), slot("a", "done")]})

    def test_unknown_output_type_is_rejected(self):
        with self.assertRaisesRegex(ConfigError, "unknown type"):
            parse({"slot": [slot("a", "done")], "output": [{"name": "o", "type": "carrier-pigeon"}]})

    def test_unknown_source_type_is_rejected(self):
        with self.assertRaisesRegex(ConfigError, "unknown type"):
            parse({"slot": [slot("a", "done")], "source": [{"name": "s", "type": "telepathy"}]})

    def test_output_keys_land_in_options(self):
        config = parse({
            "slot": [slot("a", "done")],
            "output": [{"name": "o", "type": "file", "path": "out.jsonl", "when": {"route_contains": ["a"]}}],
        })
        output = config.outputs[0]
        self.assertEqual(output.handler, "grokklings.outputs.file:handle")
        self.assertEqual(output.options["path"], "out.jsonl")
        self.assertEqual(output.options["when"], {"route_contains": ["a"]})
        self.assertTrue(output.terminal)


if __name__ == "__main__":
    unittest.main()
