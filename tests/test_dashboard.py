"""A page that shows the colony and cannot touch it.

Two properties are worth more than the layout: it can't write, and it
doesn't reach into the running colony. Both are tested against a real
server on a real socket rather than by reading the source, because
"read-only" is a claim about what the process answers, not about intent.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from grokklings import dashboard
from grokklings.config import parse
from grokklings.journal import Journal
from grokklings.model import Task, TaskStatus

PRICES = {"xai/grok-4.6": {"input_cost_per_token": 2e-06, "output_cost_per_token": 6e-06}}


class DashboardCase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="grokklings-dash-"))
        self.raw = {
            "colony": {"name": "watchtower", "db": str(self.dir / "colony.db")},
            "slot": [
                {"name": "triage", "handler": "tests.workers:done", "entry": True,
                 "max_instances": 3, "description": "first pass"},
            ],
            "output": [{"name": "out", "handler": "tests.workers:done"}],
        }

    def config(self, **extra):
        raw = {**self.raw, **extra}
        return parse(raw)

    def journal(self) -> Journal:
        journal = Journal(str(self.dir / "colony.db"))
        self.addCleanup(journal.close)
        return journal

    def pin_prices(self) -> None:
        (self.dir / "prices.json").write_text(json.dumps(PRICES))


class TestSnapshot(DashboardCase):
    def test_an_empty_colony_reports_emptiness_not_an_error(self):
        snap = dashboard.snapshot(self.config(), self.journal())

        self.assertEqual(snap["colony"], "watchtower")
        self.assertEqual(snap["tasks"], {})
        self.assertEqual(snap["proposals"], [])
        self.assertIsNone(snap["cost"])       # no [cost] section at all
        self.assertIsNone(snap["scores"])     # nothing scored yet

    def test_slots_carry_their_ceiling_and_what_is_waiting(self):
        journal = self.journal()
        for i in range(3):
            task = Task(id=f"t{i}", source="manual", payload={"n": i})
            task.slot = "triage"
            journal.enqueue(task)

        snap = dashboard.snapshot(self.config(), journal)
        triage = next(s for s in snap["slots"] if s["name"] == "triage")

        self.assertEqual(triage["ceiling"], 3)
        self.assertEqual(triage["waiting"], 3)
        self.assertTrue(next(s for s in snap["slots"] if s["name"] == "out")["terminal"])

    def test_unrouted_work_is_counted_separately(self):
        journal = self.journal()
        journal.enqueue(Task(id="t1", source="manual", payload={}))   # no slot yet

        self.assertEqual(dashboard.snapshot(self.config(), journal)["unrouted"], 1)

    def test_the_unplaced_pile_shows_up_with_its_reason(self):
        journal = self.journal()
        task = Task(id="t1", source="manual", payload={})
        task.last_error = "not my part of it"
        journal.enqueue(task)
        journal.finish(task, TaskStatus.UNPLACED)

        pile = dashboard.snapshot(self.config(), journal)["unplaced"]
        self.assertEqual(pile[0]["why"], "not my part of it")

    def test_pending_proposals_are_listed_and_answered_ones_are_not(self):
        from grokklings import brain

        journal = self.journal()
        proposal = brain.Proposal(
            kind="new_role", target="images", rationale="lots of unplaced images",
            change='[[slot]]', evidence=["t1"], improves=["capability isolation"],
        )
        seq = journal.record("proposal_made", None, "images", **proposal.as_event())
        self.assertEqual(len(dashboard.snapshot(self.config(), journal)["proposals"]), 1)

        journal.record("proposal_accepted", None, "images", proposal=str(seq), kind="new_role")
        self.assertEqual(dashboard.snapshot(self.config(), journal)["proposals"], [])

    def test_a_tripped_cost_limit_is_surfaced_with_its_reason(self):
        journal = self.journal()
        journal.record("cost_limit_reached", None, None, reason="total spend $6.00 reached $5.00")

        snap = dashboard.snapshot(self.config(), journal)
        self.assertIn("$6.00", snap["limit_reason"])

    def test_spend_is_absent_rather_than_zero_when_no_prices_are_pinned(self):
        """A dashboard reading $0.00 when it simply can't tell would be a lie
        in exactly the direction that matters."""
        snap = dashboard.snapshot(self.config(cost={"limit": 5.0}), self.journal())
        self.assertIn("unavailable", snap["cost"])
        self.assertNotIn("total", snap["cost"])

    def test_spend_is_reported_when_prices_are_there(self):
        self.pin_prices()
        journal = self.journal()
        journal.record("worker_note", "t1", "triage", message="model response received",
                       model="grok-4.6", input_tokens=1_000_000, output_tokens=0)

        money = dashboard.snapshot(self.config(cost={"limit": 5.0}), journal)["cost"]
        self.assertAlmostEqual(money["total"], 2.0, places=4)
        self.assertEqual(money["limit"], 5.0)
        self.assertTrue(money["complete"])

    def test_an_unpriced_model_is_flagged_on_the_page_too(self):
        self.pin_prices()
        journal = self.journal()
        journal.record("worker_note", "t1", "triage", message="model response received",
                       model="mystery", input_tokens=9_000_000, output_tokens=0)

        money = dashboard.snapshot(self.config(cost={"limit": 5.0}), journal)["cost"]
        self.assertFalse(money["complete"])
        self.assertEqual(money["unpriced_models"], ["mystery"])

    def test_quality_averages_only_what_was_scored(self):
        journal = self.journal()
        journal.record("task_scored", "t1", "triage", score=0.8, met=True, violations=[])
        journal.record("task_scored", "t2", "triage", score=0.4, met=False, violations=["made it up"])

        scores = dashboard.snapshot(self.config(), journal)["scores"]
        self.assertEqual(scores["count"], 2)
        self.assertAlmostEqual(scores["average"], 0.6, places=3)
        self.assertEqual(scores["met"], 1)
        self.assertEqual(scores["violations"], 1)


class TestTheServer(DashboardCase):
    """Against a real socket: 'read-only' is what the process answers."""

    def serve(self) -> str:
        server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard._handler(self.config()))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))
        return f"http://127.0.0.1:{server.server_address[1]}"

    def test_the_page_is_served(self):
        with urllib.request.urlopen(self.serve() + "/") as response:
            body = response.read().decode()
        self.assertEqual(response.status, 200)
        self.assertIn("<title>grokklings</title>", body)

    def test_the_api_returns_the_snapshot(self):
        with urllib.request.urlopen(self.serve() + "/api") as response:
            snap = json.loads(response.read())
        self.assertEqual(snap["colony"], "watchtower")
        self.assertIn("slots", snap)

    def test_a_post_is_refused(self):
        """Not by a check inside the handler — the method doesn't exist."""
        request = urllib.request.Request(
            self.serve() + "/api", data=b"{}", method="POST"
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request)
        self.assertIn(caught.exception.code, (405, 501))

    def test_delete_and_put_are_refused_too(self):
        base = self.serve()
        for method in ("DELETE", "PUT", "PATCH"):
            with self.subTest(method=method):
                request = urllib.request.Request(base + "/", method=method)
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(request)
                self.assertIn(caught.exception.code, (405, 501))

    def test_the_handler_implements_no_write_method(self):
        """The guarantee is the absence, so assert the absence."""
        handler = dashboard._handler(self.config())
        implemented = [n for n in vars(handler) if n.startswith("do_")]
        self.assertEqual(implemented, ["do_GET"])

    def test_an_unknown_path_is_a_plain_404(self):
        request = urllib.request.Request(self.serve() + "/secrets")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request)
        self.assertEqual(caught.exception.code, 404)


class TestBindingDefault(unittest.TestCase):
    def test_loopback_names_are_the_ones_that_do_not_warn(self):
        """The journal holds task payloads; publishing them by accident is a
        worse default than making someone type --host."""
        self.assertIn("127.0.0.1", dashboard.LOOPBACK)
        self.assertNotIn("0.0.0.0", dashboard.LOOPBACK)


if __name__ == "__main__":
    unittest.main()
