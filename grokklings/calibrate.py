"""Checking the judge against yourself.

The evaluator ([0015](../docs/decisions/0015-success-is-the-users-answer.md))
scores finished work against criteria you wrote. Nothing so far says
whether it scores the way you meant. That gap matters more than it looks:
a misrouted task shows up as a bad result somewhere, but a miscalibrated
judge produces confident numbers, and everything the brain later decides
rests on them.

The fix is not clever, it's arithmetic: judge a sample yourself, and see
how far the judge is from you. `cimo-labs/cje` does that properly —
it fits a judge→you mapping on the slice you labeled and re-estimates the
mean over everything, with a confidence interval and a warning when the
judge's scores run outside the range you actually labeled.

Two things this module is careful about, both easy to get wrong in a way
that quietly invalidates the answer:

**The sample is random, not chosen.** Labeling the cases that look wrong
would measure the judge on its worst day and calibrate against a
distribution that doesn't exist. CJE needs a randomly sampled slice, and
that's what `worksheet` takes.

**You don't get to see the judge's score while labeling.** Shown the
number you're checking, you agree with it — and the agreement you measure
is your own anchoring, not the judge's accuracy. The worksheet omits it.

Needs `pip install 'grokklings[calibrate]'`.
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger("grokklings.calibrate")

# Below this many labels the answer is noise wearing a confidence interval.
MIN_LABELS = 10

# CJE grades its own coverage. REFUSE-LEVEL means the judge's scores run
# too far outside what you labeled for the level to mean anything; CAUTION
# means the estimate stands but the edges are thin. Only the first is a
# reason to disbelieve the number.
REFUSE = "REFUSE"


class NotInstalled(Exception):
    """CJE isn't available in this environment."""


@dataclass
class Row:
    """One task, ready to be judged by hand."""

    task_id: str
    slot: str
    payload: Any
    result: Any
    judge_score: float
    label: float | None = None

    def for_labeling(self) -> dict[str, Any]:
        """What the person sees. Deliberately not the judge's score."""
        return {
            "task_id": self.task_id,
            "slot": self.slot,
            "payload": self.payload,
            "result": self.result,
            "label": None,
        }


@dataclass
class Calibration:
    """How far the judge is from you, and how much to trust that number."""

    judge_mean: float
    calibrated: float
    ci: tuple[float, float]
    n: int
    n_labelled: int
    out_of_range: float = 0.0
    rmse: float | None = None
    status: str = ""
    note: str = ""

    @property
    def drift(self) -> float:
        """Positive means the judge scores higher than you do."""
        return self.judge_mean - self.calibrated

    @property
    def refused(self) -> bool:
        """CJE won't stand behind the level: too much of the judge's range
        is outside what was labeled. Its own CAUTION is not this — that one
        says the estimate holds but the edges are thin."""
        return self.status.upper().startswith(REFUSE)

    @property
    def trustworthy(self) -> bool:
        """Whether the level estimate is safe to quote at all."""
        return not self.refused and self.n_labelled >= MIN_LABELS

    def as_event(self) -> dict[str, Any]:
        return {
            "judge_mean": round(self.judge_mean, 4),
            "calibrated": round(self.calibrated, 4),
            "drift": round(self.drift, 4),
            "ci": [round(self.ci[0], 4), round(self.ci[1], 4)],
            "n": self.n,
            "n_labelled": self.n_labelled,
            "out_of_range": round(self.out_of_range, 4),
            "status": self.status,
            "refused": self.refused,
        }


def scored_rows(journal: Any, limit: int = 1000) -> list[Row]:
    """Every task the evaluator has scored, paired with what it scored."""
    rows = []
    for event in journal.events(limit=limit, type="task_scored"):
        data = event.get("data") or {}
        score = data.get("score")
        task_id = event.get("task_id")
        if score is None or not task_id:
            continue
        task = journal.get(task_id)
        if task is None:
            continue
        rows.append(
            Row(
                task_id=task_id,
                slot=event.get("slot") or "",
                payload=task.payload,
                result=task.merged_result(),
                judge_score=float(score),
            )
        )
    return rows


def worksheet(rows: list[Row], size: int, seed: int = 0) -> list[Row]:
    """A random slice to label by hand.

    Random on purpose. Picking the ones that look wrong would measure the
    judge where you already suspect it and calibrate against a distribution
    that never occurs.
    """
    if size >= len(rows):
        return list(rows)
    return random.Random(seed).sample(rows, size)


def write_worksheet(rows: list[Row], path: str | Path) -> int:
    path = Path(path)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row.for_labeling(), ensure_ascii=False, default=str) + "\n")
    return len(rows)


def read_labels(path: str | Path) -> dict[str, float]:
    """Read back what was filled in. Anything left blank is just unlabeled."""
    labels: dict[str, float] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        entry = json.loads(line)
        value = entry.get("label")
        if value is None or value == "":
            continue
        try:
            score = float(value)
        except (TypeError, ValueError):
            log.warning("task %s: label %r isn't a number, skipping", entry.get("task_id"), value)
            continue
        if not 0.0 <= score <= 1.0:
            log.warning("task %s: label %s is outside 0..1, skipping", entry.get("task_id"), score)
            continue
        labels[str(entry.get("task_id"))] = score
    return labels


def calibrate(rows: list[Row], labels: dict[str, float]) -> Calibration:
    """Fit the judge against your labels and re-estimate the mean.

    Raises rather than guessing when there's too little to work with: a
    confidence interval computed from four labels is still a confidence
    interval, and it will be believed.
    """
    try:
        import numpy as np
        from cje import calibrated_mean_ci
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on the environment
        raise NotInstalled(
            "calibrating the judge needs CJE: pip install 'grokklings[calibrate]'"
        ) from exc

    usable = {task_id: value for task_id, value in labels.items()
              if any(r.task_id == task_id for r in rows)}
    if len(usable) < MIN_LABELS:
        raise ValueError(
            f"only {len(usable)} usable label(s); {MIN_LABELS} is the floor — "
            "below that the interval is noise with a number on it"
        )

    judge = np.array([r.judge_score for r in rows], dtype=float)
    oracle = np.array([usable.get(r.task_id, np.nan) for r in rows], dtype=float)

    result = calibrated_mean_ci(judge, oracle)
    diagnostics = result.diagnostics or {}
    boundary = diagnostics.get("boundary_card") or {}
    fit = diagnostics.get("calibration") or {}

    return Calibration(
        judge_mean=float(judge.mean()),
        calibrated=float(result.estimate),
        ci=(float(result.ci[0]), float(result.ci[1])),
        n=int(result.n),
        n_labelled=int(result.n_oracle),
        out_of_range=float(boundary.get("out_of_range") or 0.0),
        rmse=float(fit["oof_rmse"]) if fit.get("oof_rmse") is not None else None,
        status=str(boundary.get("status") or ""),
        note=str(boundary.get("note") or ""),
    )
