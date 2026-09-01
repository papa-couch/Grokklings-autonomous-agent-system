"""The brain: proposes changes to the roster, and never makes them.

The colony's workers process tasks. The brain does something different: it
reads what happened — the unplaced pile, the scores, the failures — and
proposes that the roster itself should change. A slot that doesn't exist
should; one slot is doing two jobs; a context is producing bad results; a
slot isn't earning its place.

Three rules hold this apart from a system that rewrites itself:

1. **A proposal is inert.** It is a record with a rationale and evidence,
   not an action with a delay on it. Nothing in the colony reads proposals.
2. **Consent is a required argument, not a convention.** `apply` takes
   `confirmed: bool` and raises when it's false — borrowed from
   `BerriAI/self-improving-agent`, where the same guarantee is enforced in
   the tool schema rather than in a docstring nobody reads.
3. **The brain never touches the config file.** Accepting a proposal prints
   the TOML to put in it. What the colony runs stays something the user
   typed.

It also runs outside the pipeline, on its own cadence, like scoring — a
task's fate never depends on what the brain is doing.

The bar a proposal has to clear is borrowed too, from the OpenAI Agents
SDK's orchestration guidance: a new specialist is worth it only when it
"materially improves capability isolation, policy isolation, prompt
clarity, or trace legibility," and splitting early costs more than it buys.
That's the right bias for a component whose whole job is proposing growth.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .model import SlotSpec, TaskStatus

log = logging.getLogger("grokklings.brain")

DEFAULT_MODEL = "grok-4.6"

# The four things a change has to improve, from the orchestration guidance.
BARS = ["capability isolation", "policy isolation", "prompt clarity", "trace legibility"]

KINDS = ["new_role", "split_slot", "edit_context", "retire_slot"]

PROPOSALS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "proposals": {
            "type": "array",
            "description": "Changes worth making. Empty when the roster is fine as it is.",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": KINDS},
                    "target": {
                        "type": "string",
                        "description": (
                            "The slot this concerns. For new_role, the name you propose for it."
                        ),
                    },
                    "rationale": {
                        "type": "string",
                        "description": "Why, in terms of what actually happened. Cite the evidence.",
                    },
                    "evidence": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Task ids this rests on. Never empty.",
                    },
                    "improves": {
                        "type": "array",
                        "items": {"type": "string", "enum": BARS},
                        "description": "Which bars this clears. A change that clears none isn't worth proposing.",
                    },
                    "change": {
                        "type": "string",
                        "description": (
                            "The concrete change, as the TOML the user would put in their config, "
                            "or the replacement context text for edit_context."
                        ),
                    },
                },
                "required": ["kind", "target", "rationale", "evidence", "improves", "change"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["proposals"],
    "additionalProperties": False,
}

CONTRACT = """You are the brain of a colony of workers. You do not process
tasks and you do not change anything. You read what happened and propose
changes to the roster, for a person to accept or reject.

Propose a change only when it materially improves one of: capability
isolation, policy isolation, prompt clarity, trace legibility. Splitting
early costs more than it buys — more slots, more traces, more to approve —
so the honest answer is often that nothing should change. An empty list is
a good answer when the evidence doesn't support a change.

Every proposal cites the task ids it rests on. A proposal without evidence
is a guess, and a guess wastes the owner's attention.

The four kinds:
- new_role — work keeps arriving that no existing slot will take;
- split_slot — one slot is doing two different jobs, and doing at least one badly;
- edit_context — the slot is right but its instructions are producing bad results;
- retire_slot — a slot isn't earning its place.

You are proposing to someone who knows their own work better than you do.
Say what you saw and what you'd change; don't argue for it twice."""


@dataclass
class Proposal:
    """A change the brain suggests. Inert until a person accepts it."""

    kind: str
    target: str
    rationale: str
    change: str
    evidence: list[str] = field(default_factory=list)
    improves: list[str] = field(default_factory=list)
    id: str = ""

    @property
    def grounded(self) -> bool:
        """A proposal that cites nothing, or clears no bar, isn't one."""
        return bool(self.evidence) and bool(self.improves) and self.kind in KINDS

    def as_event(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "target": self.target,
            "rationale": self.rationale,
            "change": self.change,
            "evidence": self.evidence,
            "improves": self.improves,
        }

    @classmethod
    def from_event(cls, event: dict[str, Any]) -> "Proposal":
        data = event.get("data") or {}
        return cls(
            # The journal's own row number is the proposal's id — it's what
            # an acceptance points back at.
            id=str(event.get("seq", "")),
            kind=str(data.get("kind", "")),
            target=str(data.get("target", "")),
            rationale=str(data.get("rationale", "")),
            change=str(data.get("change", "")),
            evidence=list(data.get("evidence") or []),
            improves=list(data.get("improves") or []),
        )


class NotConfirmed(Exception):
    """Someone tried to apply a proposal without a person agreeing to it."""


@runtime_checkable
class Proposer(Protocol):
    async def propose(self, evidence: str, slots: list[SlotSpec]) -> list[Proposal]: ...


# ------------------------------------------------------------------ evidence


def gather(journal: Any, limit: int = 40) -> dict[str, Any]:
    """What the brain gets to look at. All of it is already being written."""
    unplaced = journal.by_status(TaskStatus.UNPLACED, limit=limit)
    failed = journal.by_status(TaskStatus.FAILED, limit=limit)
    scored = journal.events(limit=1000, type="task_scored")

    low: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []
    for event in scored:
        data = event.get("data") or {}
        row = {
            "task": event.get("task_id"),
            "slot": event.get("slot"),
            "score": data.get("score"),
            "feedback": data.get("feedback"),
        }
        if data.get("violations"):
            violations.append({**row, "violations": data["violations"]})
        elif not data.get("met"):
            low.append(row)

    return {
        "unplaced": [
            {"task": t.id, "why": t.last_error, "payload": t.payload} for t in unplaced
        ],
        "failed": [{"task": t.id, "slot": t.slot, "error": t.last_error} for t in failed],
        "scored_below_the_bar": low[:limit],
        "red_lines_crossed": violations[:limit],
        "counts": journal.status_counts(),
    }


def describe_roster(slots: list[SlotSpec]) -> str:
    lines = []
    for spec in slots:
        if spec.terminal:
            continue
        lines.append(f"- {spec.name}: {spec.description or '(no description)'}")
        if spec.context:
            lines.append(f"    instructions: {spec.context.strip()[:300]}")
        if spec.can_route_to:
            lines.append(f"    may hand off to: {', '.join(spec.can_route_to)}")
    return "\n".join(lines)


def build_prompt(evidence: dict[str, Any], slots: list[SlotSpec]) -> str:
    body = json.dumps(evidence, ensure_ascii=False, indent=2, default=str)
    return (
        f"The roster as it stands:\n{describe_roster(slots)}\n\n"
        f"What happened:\n{body}"
    )


# ------------------------------------------------------------------- proposing


class GrokBrain:
    """Reads the evidence with Grok and comes back with proposals."""

    def __init__(self, model: str = DEFAULT_MODEL, max_tokens: int = 4096, timeout: float = 120.0) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.timeout = timeout
        self._client: Any = None

    def client(self) -> Any:
        if self._client is None:
            from .handlers.grok import _get_client

            self._client = _get_client()
        return self._client

    async def propose(self, evidence: str, slots: list[SlotSpec]) -> list[Proposal]:
        try:
            response = await self.client().with_options(timeout=self.timeout).chat.completions.create(
                model=self.model,
                max_tokens=self.max_tokens,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "proposals", "schema": PROPOSALS_SCHEMA, "strict": True},
                },
                messages=[
                    {"role": "system", "content": CONTRACT},
                    {"role": "user", "content": evidence},
                ],
            )
            text = response.choices[0].message.content
            raw = json.loads(text) if text else {}
        except Exception as exc:
            log.warning("the brain couldn't produce proposals: %r", exc)
            return []

        known = {spec.name for spec in slots}
        out = []
        for item in raw.get("proposals") or []:
            if not isinstance(item, dict):
                continue
            proposal = Proposal(
                kind=str(item.get("kind", "")),
                target=str(item.get("target", "")),
                rationale=str(item.get("rationale", "")),
                change=str(item.get("change", "")),
                evidence=[str(e) for e in item.get("evidence") or []],
                improves=[str(i) for i in item.get("improves") or []],
            )
            if not proposal.grounded:
                log.warning("dropped an ungrounded proposal: %s %s", proposal.kind, proposal.target)
                continue
            if proposal.kind != "new_role" and proposal.target not in known:
                # A proposal about a slot that doesn't exist can't be acted on.
                log.warning("dropped a proposal about an unknown slot: %r", proposal.target)
                continue
            out.append(proposal)
        return out


async def think(
    journal: Any,
    brain: Proposer,
    slots: list[SlotSpec],
    limit: int = 40,
) -> list[Proposal]:
    """One pass: read what happened, come back with proposals, record them.

    Recording is all that happens. Nothing in the colony reads a proposal.
    """
    evidence = gather(journal, limit=limit)
    if not any(
        evidence[key]
        for key in ("unplaced", "failed", "scored_below_the_bar", "red_lines_crossed")
    ):
        return []
    proposals = await brain.propose(build_prompt(evidence, slots), slots)
    for proposal in proposals:
        event_id = journal.record("proposal_made", None, proposal.target, **proposal.as_event())
        proposal.id = str(event_id)
    return proposals


# -------------------------------------------------------------------- consent


def pending(journal: Any, limit: int = 100) -> list[Proposal]:
    """Proposals nobody has answered yet."""
    made = journal.events(limit=limit, type="proposal_made")
    answered = set()
    for kind in ("proposal_accepted", "proposal_rejected"):
        for event in journal.events(limit=limit, type=kind):
            answered.add(str((event.get("data") or {}).get("proposal")))
    return [p for p in (Proposal.from_event(e) for e in made) if p.id not in answered]


def apply(journal: Any, proposal: Proposal, confirmed: bool, note: str = "") -> str:
    """Record a person's acceptance, and hand back the TOML to put in the config.

    `confirmed` is an argument and not a convention on purpose: a caller
    that forgets it gets an exception, not a silent change. Nothing here
    edits the config — the colony keeps running what the user typed until
    they type this too.
    """
    if not confirmed:
        raise NotConfirmed(
            f"proposal {proposal.id} ({proposal.kind} {proposal.target}) "
            "wasn't confirmed by a person — nothing was applied"
        )
    journal.record(
        "proposal_accepted", None, proposal.target, proposal=proposal.id, kind=proposal.kind, note=note
    )
    return proposal.change


def reject(journal: Any, proposal: Proposal, note: str = "") -> None:
    """A rejection is recorded too — it's the same evidence next time round."""
    journal.record(
        "proposal_rejected", None, proposal.target, proposal=proposal.id, kind=proposal.kind, note=note
    )


# --------------------------------------------------------------------- payoff


def payoff(journal: Any, slot: str, limit: int = 1000) -> dict[str, Any] | None:
    """Did an accepted change pay off, or should it be rolled back?

    Compares the scores for a slot before and after it was accepted. Says
    "not enough yet" rather than guessing: a verdict from two tasks is
    worse than no verdict, because it gets acted on.
    """
    accepted = [
        e for e in journal.events(limit=limit, type="proposal_accepted")
        if e.get("slot") == slot
    ]
    if not accepted:
        return None
    at = accepted[-1].get("seq", 0)

    before, after = [], []
    for event in journal.events(limit=limit, type="task_scored"):
        if event.get("slot") != slot:
            continue
        score = (event.get("data") or {}).get("score")
        if score is None:
            continue
        (before if event.get("seq", 0) < at else after).append(float(score))

    if len(before) < 3 or len(after) < 3:
        return {
            "slot": slot,
            "verdict": "not enough yet",
            "before": len(before),
            "after": len(after),
        }
    mean_before = sum(before) / len(before)
    mean_after = sum(after) / len(after)
    return {
        "slot": slot,
        "verdict": "better" if mean_after > mean_before else "worse or unchanged",
        "before": round(mean_before, 3),
        "after": round(mean_after, 3),
        "samples": {"before": len(before), "after": len(after)},
    }
