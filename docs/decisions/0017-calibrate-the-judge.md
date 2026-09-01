# 0017. The judge gets checked against you, with CJE

- Status: **accepted**
- Date: 2026-08-27
- Related: [0015](0015-success-is-the-users-answer.md), [0016](0016-brain-proposes-never-applies.md), [the second survey](../research/2026-08-before-phase-5.md)

## Context

[0015](0015-success-is-the-users-answer.md) made success the user's own
definition and had Grok apply it. [0016](0016-brain-proposes-never-applies.md)
then built a brain whose every proposal rests on those scores. Nothing so
far establishes that the scores mean what the user meant.

That gap is worse than the router's, and for a specific reason: a misrouted
task usually surfaces as a bad result someone notices. A miscalibrated
judge produces confident numbers that look exactly like well-calibrated
ones, and the brain will build on them either way.

There's also a named reason to expect bias rather than merely fear it. Our
evaluator judges Grok's output with Grok — the setup for **self-preference
bias**, a judge rating its own model's work higher. The second survey found
it defined and tooled for; ⚠️ how large it is when a model judges another
instance of itself isn't established.

## Decision

Adopt `cimo-labs/cje` (MIT), rather than writing a comparison ourselves.
`grokklings calibrate` runs in two steps:

```bash
grokklings calibrate --export sheet.jsonl -n 30    # a sample to judge by hand
grokklings calibrate --labels sheet.jsonl          # how far the judge is from you
```

It reports the judge's own average, the calibrated estimate, a 95%
confidence interval, and the drift between them. CJE fits a judge→you
mapping on the slice labeled and re-estimates the mean over everything.

Three details, each of which would silently invalidate the check if got
wrong:

- **The worksheet hides the judge's score.** Shown the number being
  checked, a person agrees with it, and what gets measured is their
  anchoring rather than the judge's accuracy. The sheet carries the payload
  and the result — enough to judge — and nothing else.
- **The sample is random, not selected.** Labeling the cases that look
  wrong measures the judge on its worst day and calibrates against a
  distribution that doesn't occur. CJE requires a random slice; `worksheet`
  takes one, seeded so it's reproducible.
- **Ten labels is the floor.** Below that, `calibrate` raises instead of
  answering. An interval computed from four labels is still an interval,
  and it will be quoted.

**CJE's own coverage grade is respected, and its two levels are kept
apart.** `REFUSE-LEVEL` — too much of the judge's range lies outside what
was labeled — makes the command exit non-zero and say to label a wider
spread. `CAUTION` is printed as a note and nothing more: it means the
estimate stands but its edges are thin. Collapsing the two would cry wolf
on every honest run, which is how a warning stops being read.

CJE is an optional extra (`grokklings[calibrate]`), like the model backends.
A colony that never calibrates works exactly as before.

## Consequences

- **The last step needs the user, and can't be automated away.** Somebody
  has to judge thirty tasks by hand. That's the cost of knowing whether the
  scores mean anything, and there is no version of this that skips it.
- The result is a statement about the level of scores overall, not a
  correction applied to each one. It tells you the judge runs high by
  0.18; it doesn't rewrite past assessments. Whether to subtract that drift
  when the brain reads scores is a separate question, deliberately not
  answered here.
- A calibration goes stale. Change the criteria, the model, or the kind of
  work, and it describes a judge that no longer exists. Nothing tracks
  that yet — filed below.
- Six dependencies arrive with CJE (numpy, scipy, scikit-learn, pydantic,
  and friends). Confined to an extra, so [0001](0001-python-stdlib.md)
  still holds for anyone who doesn't calibrate.

## What's left, named honestly

- Nothing expires a calibration or notices that the criteria changed under
  it.
- Self-preference bias is measurable now but hasn't been measured: that
  needs running the judge as Grok and again as a different model on the
  same tasks. `[success]` already takes its own `model`, so the experiment
  needs no code.
- The brain doesn't read `judge_calibrated` events. It could weigh scores
  by a known drift; it doesn't.

## How to undo it

Delete `grokklings/calibrate.py`, the `calibrate` command, its test file,
and the `calibrate` extra. Nothing else imports any of it, and no other
part of the colony reads what it writes.

## Sources

- `github.com/cimo-labs/cje` ✅ — MIT, `pip install cje-eval`. Verified by
  installing 0.7.0 and running it: `calibrated_mean_ci(judge_scores,
  oracle_labels)` returns an estimate, a CI, and diagnostics carrying a
  `boundary_card` with `status`, `out_of_range`, and a note. On synthetic
  data with a judge planted 0.2 high, it recovered the true mean (0.505
  against a truth of 0.505, from a raw judge mean of 0.685) using 20 labels
  out of 60.
