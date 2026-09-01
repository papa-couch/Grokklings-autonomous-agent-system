"""Optimizing a slot's instructions with GEPA.

The brain can already propose rewriting a slot's context, but it writes the
replacement in one pass — a guess informed by the evidence, not a tested
improvement. GEPA does the other thing: it proposes a change, runs it,
scores it, reads why it scored that way, and proposes again, keeping what
survives.

The fit is closer than it looks. GEPA needs three things, and
[0015](../docs/decisions/0015-success-is-the-users-answer.md) supplied all
three without knowing it would:

| GEPA needs | Ours |
|---|---|
| a metric | `Assessment.score`, from the user's own criteria |
| a training set | finished, scored tasks in the journal |
| feedback, not just a score | `Assessment.feedback` — the schema already demands it be actionable |

That third one is the whole reason GEPA works, and the reason a plain score
wouldn't be enough: the teacher model reads *why* an answer was poor, not
just that it was.

**What comes out is a proposal, not a change.** The optimized context goes
through the same consent gate as everything the brain suggests
([0016](../docs/decisions/0016-brain-proposes-never-applies.md)) — a better
context that installed itself would still be a system rewriting its own
instructions.

**This spends real money.** Every metric call runs the worker once and the
judge once. `budget` is the cap and it is not optional.

Needs `pip install 'grokklings[optimize]'`.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping, Sequence

from .brain import Proposal
from .evaluate import Assessment, Criteria
from .model import SlotSpec, Task

log = logging.getLogger("grokklings.optimize")

# The component GEPA edits. One slot, one text.
COMPONENT = "context"

# Below this, the search has nothing to work with — GEPA itself says three
# examples can be enough, but three tasks of ours is one afternoon's noise.
MIN_TASKS = 5


class NotInstalled(Exception):
    """GEPA isn't available in this environment."""


@dataclass
class Trace:
    """What happened on one task, kept so the teacher can read it.

    A run that crashed and a run the judge couldn't score are both "no
    assessment," and they teach opposite lessons — the first says the
    instructions produce something broken, the second says nothing at all.
    `error` is what keeps them apart.
    """

    task: Task
    output: Any
    assessment: Assessment | None
    error: str | None = None


@dataclass
class Optimized:
    """The result of a search, before anyone agrees to it."""

    slot: str
    before: str
    after: str
    score_before: float
    score_after: float
    metric_calls: int

    @property
    def improved(self) -> bool:
        return self.score_after > self.score_before and self.after.strip() != self.before.strip()

    def as_proposal(self) -> Proposal:
        """The optimized context, as something the user can turn down."""
        gain = self.score_after - self.score_before
        return Proposal(
            kind="edit_context",
            target=self.slot,
            rationale=(
                f"GEPA searched {self.metric_calls} scored run(s) against your success "
                f"criteria and found instructions scoring {self.score_after:.2f} where the "
                f"current ones score {self.score_before:.2f} (+{gain:.2f})."
            ),
            change=self.after,
            evidence=[f"{self.metric_calls} scored runs"],
            improves=["prompt clarity"],
        )


class SlotAdapter:
    """Teaches GEPA how to run and judge one slot of ours.

    `run` and `score` are plain callables so this stays testable without a
    key: the adapter's job is bookkeeping between our shapes and GEPA's,
    and that part should be provable on its own.
    """

    # GEPA documents `propose_new_texts` as optional and says it provides a
    # default. In 0.1.4 the engine reads `self.adapter.propose_new_texts`
    # directly rather than through getattr, so an adapter that simply omits
    # the method raises AttributeError mid-search — reflection silently
    # stops proposing and the "optimized" context comes back unchanged.
    # Declaring it None is what actually selects that documented default.
    propose_new_texts = None

    def __init__(
        self,
        spec: SlotSpec,
        criteria: Criteria,
        run: Callable[[Task, str], Any],
        score: Callable[[Task, Any], Assessment | None],
    ) -> None:
        self.spec = spec
        self.criteria = criteria
        self.run = run
        self.score = score
        self.metric_calls = 0

    def evaluate(self, batch: list[Task], candidate: dict[str, str], capture_traces: bool = False) -> Any:
        from gepa.core.adapter import EvaluationBatch

        context = candidate.get(COMPONENT, self.spec.context)
        outputs: list[Any] = []
        scores: list[float] = []
        traces: list[Trace] = []

        for task in batch:
            self.metric_calls += 1
            try:
                output = self.run(task, context)
                assessment = self.score(task, output)
            except Exception as exc:
                # GEPA's contract: never raise for one example. A failure is
                # a zero with a reason attached, so the teacher can read it.
                log.warning("task %s failed under the candidate context: %r", task.id, exc)
                outputs.append(None)
                scores.append(0.0)
                traces.append(
                    Trace(task=task, output=None, assessment=None, error=f"{type(exc).__name__}: {exc}")
                )
                continue

            outputs.append(output)
            scores.append(assessment.score if assessment else 0.0)
            traces.append(Trace(task=task, output=output, assessment=assessment))

        return EvaluationBatch(
            outputs=outputs,
            scores=scores,
            trajectories=traces if capture_traces else None,
        )

    def make_reflective_dataset(
        self,
        candidate: dict[str, str],
        eval_batch: Any,
        components_to_update: list[str],
    ) -> Mapping[str, Sequence[Mapping[str, Any]]]:
        """The records the teacher model reads to propose better instructions.

        GEPA's recommended shape is Inputs / Generated Outputs / Feedback.
        Feedback is the part that matters, and it's the part we already
        have: the judge is required to say what was missing, not just how
        much it disliked it.
        """
        records = []
        for trace in eval_batch.trajectories or []:
            assessment = trace.assessment
            if trace.error is not None:
                feedback = f"These instructions produced a run that failed: {trace.error}"
            elif assessment is None:
                # Not a criticism of the instructions — the judge simply
                # couldn't say. Telling the teacher this was bad would have
                # it rewrite instructions that may be fine.
                feedback = "No feedback: this run completed but could not be judged."
            else:
                feedback = assessment.feedback or "No feedback was given."
                if assessment.violations:
                    # A crossed red line is the strongest possible signal
                    # about what the instructions are permitting.
                    feedback += " Red lines crossed: " + "; ".join(assessment.violations)
            records.append(
                {
                    "Inputs": {"task": str(trace.task.payload)},
                    "Generated Outputs": str(trace.output),
                    "Feedback": feedback,
                }
            )
        return {component: records for component in components_to_update}


def reflection_lm(model: str = "grok-4.6", max_tokens: int = 8192) -> Callable[[Any], str]:
    """The teacher: the model that reads the feedback and rewrites the text."""

    def ask(prompt: Any) -> str:
        from .handlers.grok import _get_client

        messages = prompt if isinstance(prompt, list) else [{"role": "user", "content": prompt}]
        client = _get_client()

        async def call() -> str:
            response = await client.chat.completions.create(
                model=model, max_tokens=max_tokens, messages=messages
            )
            return response.choices[0].message.content or ""

        return asyncio.run(call())

    return ask


def bridge(spec: SlotSpec, journal: Any, criteria: Criteria) -> tuple[Callable[..., Any], Callable[..., Any]]:
    """Wire a real slot and the real judge into the sync callables GEPA drives.

    GEPA's `evaluate` is synchronous and our workers are coroutines, so the
    bridging happens here, once, rather than being scattered through the
    adapter.
    """
    from .evaluate import GrokEvaluator
    from .slots import WorkerContext, resolve

    handler = resolve(spec.handler)
    evaluator = GrokEvaluator(model=criteria.model)

    def run(task: Task, context: str) -> Any:
        candidate = replace(spec, context=context)
        ctx = WorkerContext(slot=candidate, instance=f"{spec.name}#optimize", journal=journal)
        verdict = handler(task, ctx)
        if asyncio.iscoroutine(verdict):
            verdict = asyncio.run(verdict)
        return verdict.result

    def score(task: Task, output: Any) -> Assessment | None:
        judged = replace(task)
        judged.result = list(task.result)
        judged.add(spec.name, f"{spec.name}#optimize", output)
        return asyncio.run(evaluator.score(judged, criteria, spec))

    return run, score


def optimize_context(
    spec: SlotSpec,
    tasks: list[Task],
    criteria: Criteria,
    run: Callable[[Task, str], Any],
    score: Callable[[Task, Any], Assessment | None],
    budget: int = 60,
    teacher: Callable[[Any], str] | None = None,
    seed: int = 0,
) -> Optimized:
    """Search for better instructions for one slot.

    `budget` caps metric calls, and each one costs a worker run plus a
    judging run. It is the only thing standing between a search and an
    unbounded bill.
    """
    # The caller's arguments are checked before the environment is. Telling
    # someone to install GEPA and letting them discover afterwards that
    # their call was wrong anyway wastes their time, and it makes these
    # guards untestable without the optional dependency — which is how they
    # came to be the only two tests that failed on a bare install.
    if len(tasks) < MIN_TASKS:
        raise ValueError(
            f"only {len(tasks)} scored task(s) for slot {spec.name!r}; {MIN_TASKS} is the floor — "
            "below that the search fits noise"
        )
    if not criteria:
        raise ValueError("nothing to optimize against: write a [success] section first")

    try:
        import gepa
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on the environment
        raise NotInstalled(
            "optimizing a context needs GEPA: pip install 'grokklings[optimize]'"
        ) from exc

    adapter = SlotAdapter(spec, criteria, run, score)
    result = gepa.optimize(
        seed_candidate={COMPONENT: spec.context},
        trainset=tasks,
        adapter=adapter,
        reflection_lm=teacher or reflection_lm(criteria.model),
        max_metric_calls=budget,
        display_progress_bar=False,
        seed=seed,
        raise_on_exception=False,
    )

    best = dict(result.best_candidate or {}).get(COMPONENT, spec.context)
    scores = list(getattr(result, "val_aggregate_scores", None) or [])
    return Optimized(
        slot=spec.name,
        before=spec.context,
        after=best,
        score_before=float(scores[0]) if scores else 0.0,
        score_after=float(max(scores)) if scores else 0.0,
        metric_calls=adapter.metric_calls,
    )
