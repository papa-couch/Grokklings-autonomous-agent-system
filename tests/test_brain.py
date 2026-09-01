"""The brain proposes; a person decides.

The cases worth guarding are the ones where a self-improving system quietly
becomes a self-modifying one: a proposal that applies itself, a proposal
resting on nothing, a payoff verdict from two data points. Each of those
looks like progress and costs the owner their say.
"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from typing import Any

from grokklings import brain
from grokklings.model import SlotSpec
from tests.helpers import ColonyFixture, slot

ROSTER = [
    SlotSpec(name="triage", description="first pass", context="Drop the junk."),
    SlotSpec(name="deep_dive", description="full review"),
]

GOOD = {
    "kind": "new_role",
    "target": "images",
    "rationale": "eleven tasks with image links went unplaced",
    "evidence": ["t1", "t2"],
    "improves": ["capability isolation"],
    "change": '[[slot]]\nname = "images"\n',
}


def brain_returning(payload: dict[str, Any], captured: dict[str, Any] | None = None) -> brain.GrokBrain:
    async def create(**kwargs: Any) -> Any:
        if captured is not None:
            captured.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
        )

    it = brain.GrokBrain()
    it._client = SimpleNamespace(
        with_options=lambda timeout: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
    )
    return it


class TestProposing(unittest.IsolatedAsyncioTestCase):
    async def test_a_well_formed_proposal_comes_back(self):
        it = brain_returning({"proposals": [GOOD]})
        got = await it.propose("evidence", ROSTER)

        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].kind, "new_role")
        self.assertTrue(got[0].grounded)

    async def test_a_proposal_citing_nothing_is_dropped(self):
        """Evidence is what separates a proposal from a guess."""
        it = brain_returning({"proposals": [{**GOOD, "evidence": []}]})
        with self.assertLogs("grokklings.brain", level="WARNING"):
            self.assertEqual(await it.propose("evidence", ROSTER), [])

    async def test_a_proposal_clearing_no_bar_is_dropped(self):
        it = brain_returning({"proposals": [{**GOOD, "improves": []}]})
        with self.assertLogs("grokklings.brain", level="WARNING"):
            self.assertEqual(await it.propose("evidence", ROSTER), [])

    async def test_a_proposal_about_a_slot_that_does_not_exist_is_dropped(self):
        it = brain_returning(
            {"proposals": [{**GOOD, "kind": "edit_context", "target": "nowhere"}]}
        )
        with self.assertLogs("grokklings.brain", level="WARNING"):
            self.assertEqual(await it.propose("evidence", ROSTER), [])

    async def test_a_new_role_may_name_a_slot_that_does_not_exist_yet(self):
        """That's the whole point of proposing one."""
        it = brain_returning({"proposals": [GOOD]})
        self.assertEqual((await it.propose("e", ROSTER))[0].target, "images")

    async def test_an_empty_list_is_a_valid_answer(self):
        it = brain_returning({"proposals": []})
        self.assertEqual(await it.propose("evidence", ROSTER), [])

    async def test_a_failed_call_proposes_nothing_rather_than_raising(self):
        async def boom(**kwargs: Any) -> Any:
            raise RuntimeError("the API is down")

        it = brain.GrokBrain()
        it._client = SimpleNamespace(
            with_options=lambda timeout: SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(create=boom))
            )
        )
        with self.assertLogs("grokklings.brain", level="WARNING"):
            self.assertEqual(await it.propose("evidence", ROSTER), [])

    async def test_the_request_carries_the_roster_and_the_schema(self):
        captured: dict[str, Any] = {}
        it = brain_returning({"proposals": []}, captured)
        await it.propose(brain.build_prompt({"unplaced": []}, ROSTER), ROSTER)

        fmt = captured["response_format"]
        self.assertEqual(fmt["json_schema"]["schema"], brain.PROPOSALS_SCHEMA)
        self.assertTrue(fmt["json_schema"]["strict"])
        self.assertIn("Drop the junk.", captured["messages"][1]["content"])


class StubBrain:
    def __init__(self, proposals: list[brain.Proposal]) -> None:
        self.proposals = proposals
        self.asked = 0

    async def propose(self, evidence: str, slots: list[SlotSpec]) -> list[brain.Proposal]:
        self.asked += 1
        return [
            brain.Proposal(**{k: list(v) if isinstance(v, list) else v
                              for k, v in vars(p).items() if k != "id"})
            for p in self.proposals
        ]


class BrainCase(unittest.IsolatedAsyncioTestCase):
    fixture: ColonyFixture

    def tearDown(self):
        if getattr(self, "fixture", None):
            self.fixture.close()

    async def colony_with_a_refusal(self) -> ColonyFixture:
        self.fixture = ColonyFixture({"slot": [slot("triage", "refuse")]})
        self.fixture.colony.submit({"url": "https://a"})
        await self.fixture.settle()
        return self.fixture

    def proposal(self, **extra: Any) -> brain.Proposal:
        fields = {**GOOD, **extra}
        fields.pop("id", None)
        return brain.Proposal(**fields)


class TestThinking(BrainCase):
    async def test_proposals_are_recorded_and_nothing_else_happens(self):
        fixture = await self.colony_with_a_refusal()
        before = fixture.colony.journal.status_counts()

        got = await brain.think(fixture.colony.journal, StubBrain([self.proposal()]), ROSTER)

        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].id)  # it got the journal's row number
        events = fixture.colony.journal.events(limit=50, type="proposal_made")
        self.assertEqual(len(events), 1)
        # The colony is untouched: proposing is not doing.
        self.assertEqual(fixture.colony.journal.status_counts(), before)

    async def test_a_quiet_colony_is_not_worth_asking_about(self):
        """No unplaced work, no failures, no bad scores — don't spend a call."""
        self.fixture = ColonyFixture(
            {
                "slot": [slot("triage", "done")],
                "output": [{"name": "output", "handler": "tests.workers:done"}],
            }
        )
        self.fixture.colony.submit({"url": "https://a"})
        await self.fixture.settle()

        stub = StubBrain([self.proposal()])
        self.assertEqual(await brain.think(self.fixture.colony.journal, stub, ROSTER), [])
        self.assertEqual(stub.asked, 0)

    async def test_the_unplaced_pile_reaches_the_evidence(self):
        fixture = await self.colony_with_a_refusal()
        evidence = brain.gather(fixture.colony.journal)

        self.assertEqual(len(evidence["unplaced"]), 1)
        self.assertIn("not my part of it", evidence["unplaced"][0]["why"])


class TestConsent(BrainCase):
    async def test_applying_without_confirmation_raises(self):
        """The guarantee is the argument, not a note in the docs."""
        fixture = await self.colony_with_a_refusal()
        got = await brain.think(fixture.colony.journal, StubBrain([self.proposal()]), ROSTER)

        with self.assertRaises(brain.NotConfirmed):
            brain.apply(fixture.colony.journal, got[0], confirmed=False)
        # And nothing was recorded as accepted.
        self.assertEqual(fixture.colony.journal.events(limit=50, type="proposal_accepted"), [])

    async def test_accepting_records_it_and_hands_back_the_change(self):
        fixture = await self.colony_with_a_refusal()
        got = await brain.think(fixture.colony.journal, StubBrain([self.proposal()]), ROSTER)

        change = brain.apply(fixture.colony.journal, got[0], confirmed=True, note="looks right")
        self.assertIn('name = "images"', change)
        events = fixture.colony.journal.events(limit=50, type="proposal_accepted")
        self.assertEqual(events[0]["data"]["note"], "looks right")

    async def test_an_answered_proposal_stops_waiting(self):
        fixture = await self.colony_with_a_refusal()
        got = await brain.think(fixture.colony.journal, StubBrain([self.proposal()]), ROSTER)
        self.assertEqual(len(brain.pending(fixture.colony.journal)), 1)

        brain.apply(fixture.colony.journal, got[0], confirmed=True)
        self.assertEqual(brain.pending(fixture.colony.journal), [])

    async def test_a_rejection_is_recorded_too(self):
        fixture = await self.colony_with_a_refusal()
        got = await brain.think(fixture.colony.journal, StubBrain([self.proposal()]), ROSTER)

        brain.reject(fixture.colony.journal, got[0], note="not now")
        self.assertEqual(brain.pending(fixture.colony.journal), [])
        events = fixture.colony.journal.events(limit=50, type="proposal_rejected")
        self.assertEqual(events[0]["data"]["note"], "not now")


class TestPayoff(BrainCase):
    def record_scores(self, journal: Any, slot_name: str, scores: list[float]) -> None:
        for value in scores:
            journal.record("task_scored", f"t{value}", slot_name, score=value, met=True)

    async def test_a_verdict_needs_enough_on_both_sides(self):
        """Two data points is not a verdict — and a verdict gets acted on."""
        fixture = await self.colony_with_a_refusal()
        journal = fixture.colony.journal
        self.record_scores(journal, "triage", [0.4, 0.5])
        got = await brain.think(journal, StubBrain([self.proposal(target="triage")]), ROSTER)
        brain.apply(journal, got[0], confirmed=True)
        self.record_scores(journal, "triage", [0.9, 0.9])

        report = brain.payoff(journal, "triage")
        self.assertEqual(report["verdict"], "not enough yet")

    async def test_it_compares_before_and_after_the_acceptance(self):
        fixture = await self.colony_with_a_refusal()
        journal = fixture.colony.journal
        self.record_scores(journal, "triage", [0.1, 0.2, 0.3])
        got = await brain.think(journal, StubBrain([self.proposal(target="triage")]), ROSTER)
        brain.apply(journal, got[0], confirmed=True)
        self.record_scores(journal, "triage", [0.8, 0.9, 0.95])

        report = brain.payoff(journal, "triage")
        self.assertEqual(report["verdict"], "better")
        self.assertLess(report["before"], report["after"])

    async def test_a_change_that_made_things_worse_says_so(self):
        fixture = await self.colony_with_a_refusal()
        journal = fixture.colony.journal
        self.record_scores(journal, "triage", [0.9, 0.9, 0.9])
        got = await brain.think(journal, StubBrain([self.proposal(target="triage")]), ROSTER)
        brain.apply(journal, got[0], confirmed=True)
        self.record_scores(journal, "triage", [0.2, 0.3, 0.1])

        self.assertEqual(brain.payoff(journal, "triage")["verdict"], "worse or unchanged")

    async def test_nothing_accepted_means_nothing_to_report(self):
        fixture = await self.colony_with_a_refusal()
        self.assertIsNone(brain.payoff(fixture.colony.journal, "triage"))


if __name__ == "__main__":
    unittest.main()
