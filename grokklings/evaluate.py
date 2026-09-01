"""What counts as success — answered by the user, applied by the colony.

Success isn't a property of the machinery. The same finished task is a good
result for one person and a useless one for another, and no metric baked
into the colony can know which. So the colony doesn't define success: the
user writes it down, in their own words, in a `[success]` section, the same
way they already write what each slot does.

Three questions, taken from section 9 of the design doc:

    [success]
    looking_for = "what we're after at all"
    counts_as = "what a good result looks like"
    never = "what must never happen, whatever the score"

A slot may add its own `success = "..."` for what a good result from *that*
slot looks like.

Scoring runs on finished tasks, on demand — never inside the pipeline. A
task's fate doesn't depend on being scored, and a colony with no
`[success]` section is scored not at all, which is the same colony it was
before.

Two things come back from a scoring pass, and they're deliberately
separate: a score, and whether a red line was crossed. A crossed red line
isn't a low score — a result can be excellent at what it was asked to do
and still have done something it must never do. Averaging those together
would hide exactly the case worth catching.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .model import SlotSpec, Task

log = logging.getLogger("grokklings.evaluate")

DEFAULT_MODEL = "grok-4.6"

ASSESSMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "score": {
            "type": "number",
            "description": "How well the result meets the criteria, from 0 to 1.",
        },
        "met": {
            "type": "boolean",
            "description": "Whether this counts as a success by the user's own definition.",
        },
        "feedback": {
            "type": "string",
            "description": (
                "What specifically was good or missing — concrete enough to act on. "
                "Not a restatement of the score."
            ),
        },
        "violations": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Red lines crossed, quoted from the user's 'never' list. Empty if none.",
        },
    },
    "required": ["score", "met", "feedback", "violations"],
    "additionalProperties": False,
}

CONTRACT = """You are judging a finished task against criteria its owner
wrote. You are not redoing the task and not improving it — you are saying
how well what came back matches what they asked for.

The criteria are theirs, not yours. Judge against what they wrote, not
against what you would have asked for. If they didn't ask for something,
its absence isn't a fault.

Score from 0 to 1, and say whether it counts as a success by their
definition. Feedback must be specific enough to act on — name what is
missing or wrong, not "could be better."

Red lines are separate from the score. If the result did something the
owner said must never happen, list it in violations, quoting which line was
crossed — even if the work was otherwise excellent. If nothing was crossed,
violations is empty."""


@dataclass
class Criteria:
    """What the user said success means. Empty means: don't score."""

    looking_for: str = ""
    counts_as: str = ""
    never: str = ""
    model: str = DEFAULT_MODEL

    def __bool__(self) -> bool:
        return bool(self.looking_for or self.counts_as or self.never)

    def as_prompt(self) -> str:
        parts = []
        if self.looking_for:
            parts.append(f"What they're after:\n{self.looking_for}")
        if self.counts_as:
            parts.append(f"What counts as a good result:\n{self.counts_as}")
        if self.never:
            parts.append(f"What must never happen:\n{self.never}")
        return "\n\n".join(parts)


@dataclass
class Assessment:
    """One scoring pass over one task."""

    task_id: str
    score: float
    met: bool
    feedback: str
    violations: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        """A success that also crossed no red line."""
        return self.met and not self.violations

    def as_event(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 3),
            "met": self.met,
            "feedback": self.feedback,
            "violations": self.violations,
        }


@runtime_checkable
class Evaluator(Protocol):
    async def score(self, task: Task, criteria: Criteria, slot: SlotSpec | None = None) -> Assessment | None: ...


def build_prompt(task: Task, criteria: Criteria, slot: SlotSpec | None) -> str:
    payload = json.dumps(task.payload, ensure_ascii=False, indent=2, default=str)
    result = json.dumps(task.merged_result(), ensure_ascii=False, indent=2, default=str)
    blocks = [criteria.as_prompt(), f"\nThe task it started from:\n{payload}"]
    if slot is not None and slot.success:
        blocks.append(f"\nWhat a good result from the '{slot.name}' slot looks like:\n{slot.success}")
    blocks.append(f"\nWhat the colony produced:\n{result}")
    if task.route:
        blocks.append(f"\nWho worked on it: {' -> '.join(task.route)}")
    return "\n".join(blocks)


class GrokEvaluator:
    """Scores a finished task with Grok, against the user's own criteria.

    Needs the same key and package as the Grok worker. A failed call
    returns None rather than a zero: "we couldn't tell" and "it was bad"
    are different answers, and recording one as the other would poison
    every average built on top of it.
    """

    def __init__(self, model: str = DEFAULT_MODEL, max_tokens: int = 1024, timeout: float = 60.0) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.timeout = timeout
        self._client: Any = None

    def client(self) -> Any:
        if self._client is None:
            from .handlers.grok import _get_client

            self._client = _get_client()
        return self._client

    async def score(
        self, task: Task, criteria: Criteria, slot: SlotSpec | None = None
    ) -> Assessment | None:
        if not criteria:
            return None
        try:
            response = await self.client().with_options(timeout=self.timeout).chat.completions.create(
                model=criteria.model or self.model,
                max_tokens=self.max_tokens,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "assessment",
                        "schema": ASSESSMENT_SCHEMA,
                        "strict": True,
                    },
                },
                messages=[
                    {"role": "system", "content": CONTRACT},
                    {"role": "user", "content": build_prompt(task, criteria, slot)},
                ],
            )
            text = response.choices[0].message.content
            raw = json.loads(text) if text else None
        except Exception as exc:
            log.warning("couldn't score task %s: %r", task.id, exc)
            return None
        if not isinstance(raw, dict):
            log.warning("the evaluator returned something that isn't an assessment for %s", task.id)
            return None

        try:
            score = float(raw["score"])
        except (KeyError, TypeError, ValueError):
            log.warning("the evaluator returned no usable score for %s", task.id)
            return None
        violations = raw.get("violations") or []
        if not isinstance(violations, list):
            violations = [str(violations)]
        return Assessment(
            task_id=task.id,
            score=min(1.0, max(0.0, score)),
            met=bool(raw.get("met")),
            feedback=str(raw.get("feedback") or ""),
            violations=[str(v) for v in violations],
        )


def criteria_from(config: dict[str, Any]) -> Criteria:
    return Criteria(
        looking_for=str(config.get("looking_for") or ""),
        counts_as=str(config.get("counts_as") or ""),
        never=str(config.get("never") or ""),
        model=str(config.get("model") or DEFAULT_MODEL),
    )


async def score_finished(
    journal: Any,
    criteria: Criteria,
    evaluator: Evaluator,
    slots: dict[str, SlotSpec] | None = None,
    limit: int = 50,
    rescore: bool = False,
) -> list[Assessment]:
    """Score finished tasks that haven't been scored yet.

    Runs outside the pipeline, on demand: a task's fate never depends on
    whether anyone got around to scoring it. Each assessment is written to
    the journal as a `task_scored` event, which is what the brain reads.
    """
    from .model import TaskStatus

    if not criteria:
        return []
    done = journal.by_status(TaskStatus.DONE, limit=limit)
    if not rescore:
        already = journal.scored_ids([task.id for task in done])
        done = [task for task in done if task.id not in already]

    assessments = []
    for task in done:
        slot = None
        if slots and task.route:
            # The last non-terminal slot to touch it is the one whose own
            # success criteria apply.
            for name in reversed(task.route):
                spec = slots.get(name)
                if spec is not None and not spec.terminal:
                    slot = spec
                    break
        assessment = await evaluator.score(task, criteria, slot)
        if assessment is None:
            # "Couldn't tell" is not "was bad" — nothing is recorded, so it
            # gets picked up again on the next pass.
            continue
        journal.record("task_scored", task.id, slot.name if slot else None, **assessment.as_event())
        assessments.append(assessment)
    return assessments
