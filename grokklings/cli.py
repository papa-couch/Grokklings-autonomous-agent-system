"""Managing a colony from the command line."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
import time
from pathlib import Path
from typing import Any

from . import brain
from . import calibrate
from . import config as config_module
from . import cost as cost_module
from . import dashboard as dashboard_module
from . import evaluate
from . import optimize
from . import otel
from .colony import Colony
from .journal import Journal
from .model import TaskStatus

EXAMPLE = '''# Grokklings colony — a minimal working config.

[colony]
name = "example"
db = "colony.db"
max_hops = 10          # loop guard: the limit on hops between slots

[intake]
# Deduplication key: a path into the payload ("url", "repo.name",
# "contract.address"), or "@payload" — a hash of the whole thing.
dedup_key = "url"

[[source]]
name = "hook"
type = "webhook"       # manual | interval | webhook
host = "127.0.0.1"
port = 8099
path = "/ingest"

[[slot]]
name = "triage"
description = "first pass over incoming items"
context = """What to look for, what counts as success, what not to do."""
# Starts on the echo handler so `run` works with no keys and no installs.
# To think with Grok: pip install 'grokklings[grok]', export XAI_API_KEY, and
# swap the line below for "grokklings.handlers.grok:handle".
# Your own worker: "my_package.module:function".
handler = "grokklings.handlers.echo:handle"
timeout = 60
max_instances = 1
entry = true

[[output]]
name = "output"
type = "console"       # console | file | webhook
shape = "merged"
'''


def _colony(args: argparse.Namespace) -> Colony:
    return Colony(config_module.load(args.config))


def cmd_init(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if path.exists() and not args.force:
        print(f"{path} already exists — overwrite with --force", file=sys.stderr)
        return 1
    path.write_text(EXAMPLE, encoding="utf-8")
    print(f"config written: {path}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    colony = _colony(args)

    async def main() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:  # pragma: no cover — not POSIX
                pass
        runner = asyncio.create_task(colony.run(stop))
        await stop.wait()
        print("stopping: no new tasks are taken, in-flight ones are seen through", file=sys.stderr)
        await colony.dispatcher.drain()
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)

    try:
        asyncio.run(main())
    finally:
        colony.close()
    return 0


def cmd_submit(args: argparse.Namespace) -> int:
    payload = _read_payload(args)
    colony = _colony(args)
    try:
        admission = colony.submit(payload, source=args.source)
    finally:
        colony.close()
    if admission.accepted:
        print(f"accepted: {admission.task.id}")
        return 0
    print(f"duplicate: {admission.task.id} -> first instance {admission.duplicate_of}")
    return 0


def _read_payload(args: argparse.Namespace) -> Any:
    if args.json == "-":
        raw = sys.stdin.read()
    elif args.file:
        raw = Path(args.file).read_text(encoding="utf-8")
    elif args.json:
        raw = args.json
    else:
        raise SystemExit("need --json or --file")
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise SystemExit(f"couldn't parse JSON: {exc}") from None


def cmd_status(args: argparse.Namespace) -> int:
    colony = _colony(args)
    try:
        print(json.dumps(colony.status(), ensure_ascii=False, indent=2, default=str))
    finally:
        colony.close()
    return 0


def cmd_journal(args: argparse.Namespace) -> int:
    journal = Journal(config_module.load(args.config).db)
    try:
        for event in journal.events(limit=args.tail, task_id=args.task, type=args.type):
            data = json.dumps(event["data"], ensure_ascii=False, default=str)
            print(f"{event['seq']:>6} {event['type']:<16} {event['task_id'] or '-':<16} "
                  f"{event['slot'] or '-':<14} {data}")
    finally:
        journal.close()
    return 0


def cmd_task(args: argparse.Namespace) -> int:
    journal = Journal(config_module.load(args.config).db)
    try:
        task = journal.get(args.id)
        if task is None:
            print(f"task {args.id} not found", file=sys.stderr)
            return 1
        print(json.dumps(
            {
                "id": task.id,
                "status": task.status.value,
                "source": task.source,
                "dedup_key": task.dedup_key,
                "route": task.route,
                "hops": task.hops,
                "attempts": task.attempts,
                "error": task.last_error,
                "duplicate_of": task.duplicate_of,
                "result": task.merged_result(),
            },
            ensure_ascii=False, indent=2, default=str,
        ))
    finally:
        journal.close()
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    """Score finished tasks against the user's own success criteria."""
    config = config_module.load(args.config)
    criteria = evaluate.criteria_from(config.success)
    if not criteria:
        print(
            "no success criteria: add a [success] section saying what you're after, "
            "what counts as a good result, and what must never happen",
            file=sys.stderr,
        )
        return 1

    slots = {spec.name: spec for spec in config.all_slots}
    evaluator = evaluate.GrokEvaluator(model=criteria.model)
    journal = Journal(config.db)
    try:
        assessments = asyncio.run(
            evaluate.score_finished(
                journal, criteria, evaluator, slots, limit=args.limit, rescore=args.rescore
            )
        )
    finally:
        journal.close()

    if not assessments:
        print("nothing to score")
        return 0
    scored = len(assessments)
    met = sum(1 for a in assessments if a.met)
    flagged = [a for a in assessments if a.violations]
    average = sum(a.score for a in assessments) / scored
    print(f"scored {scored}: {met} met the bar, average {average:.2f}")
    for assessment in assessments:
        mark = "x" if assessment.violations else ("+" if assessment.met else "-")
        print(f"  {mark} {assessment.task_id}  {assessment.score:.2f}  {assessment.feedback[:100]}")
    if flagged:
        # A crossed red line is not a low score, and doesn't average away.
        print(f"\nred lines crossed in {len(flagged)} task(s):", file=sys.stderr)
        for assessment in flagged:
            for violation in assessment.violations:
                print(f"  {assessment.task_id}: {violation}", file=sys.stderr)
        return 1
    return 0


def cmd_brain(args: argparse.Namespace) -> int:
    """Read what happened and propose roster changes. Changes nothing."""
    config = config_module.load(args.config)
    slots = [spec for spec in config.all_slots if not spec.terminal]
    journal = Journal(config.db)
    try:
        proposals = asyncio.run(
            brain.think(journal, brain.GrokBrain(model=args.model), slots, limit=args.limit)
        )
    finally:
        journal.close()

    if not proposals:
        print("nothing to propose")
        return 0
    print(f"{len(proposals)} proposal(s) — none of them applied; review with `grokklings proposals`\n")
    for proposal in proposals:
        print(f"  [{proposal.id}] {proposal.kind}: {proposal.target}")
        print(f"      {proposal.rationale}")
    return 0


def cmd_proposals(args: argparse.Namespace) -> int:
    """Review what the brain proposed, and accept or reject it."""
    config = config_module.load(args.config)
    journal = Journal(config.db)
    try:
        waiting = brain.pending(journal)
        chosen = args.accept or args.reject
        if chosen:
            match = next((p for p in waiting if p.id == chosen), None)
            if match is None:
                print(f"no pending proposal {chosen}", file=sys.stderr)
                return 1
            if args.reject:
                brain.reject(journal, match, note=args.note or "")
                print(f"rejected {match.id}")
                return 0
            # Consent is the argument, not the intent: passing --accept is
            # what sets it, and nothing else can.
            change = brain.apply(journal, match, confirmed=True, note=args.note or "")
            print(f"accepted {match.id} ({match.kind}: {match.target})\n")
            print("Nothing was changed. Put this in your config:\n")
            print(change)
            return 0

        if not waiting:
            print("no proposals waiting")
            return 0
        print(f"{len(waiting)} proposal(s) waiting:\n")
        for proposal in waiting:
            print(f"  [{proposal.id}] {proposal.kind}: {proposal.target}")
            print(f"      why: {proposal.rationale}")
            print(f"      improves: {', '.join(proposal.improves)}")
            print(f"      evidence: {', '.join(proposal.evidence[:5])}")
        print("\naccept with: grokklings proposals --accept <id>")
    finally:
        journal.close()
    return 0


def cmd_calibrate(args: argparse.Namespace) -> int:
    """Check the judge against your own labels."""
    config = config_module.load(args.config)
    journal = Journal(config.db)
    try:
        rows = calibrate.scored_rows(journal)
        if not rows:
            print("nothing scored yet — run `grokklings score` first", file=sys.stderr)
            return 1

        if args.export:
            sample = calibrate.worksheet(rows, args.size, seed=args.seed)
            written = calibrate.write_worksheet(sample, args.export)
            print(f"{written} task(s) written to {args.export}")
            print(
                "\nFill in each line's \"label\" with your own score, 0 to 1.\n"
                "The judge's score is deliberately not shown: seeing it is how you\n"
                "end up agreeing with it, and then the check measures nothing."
            )
            return 0

        labels = calibrate.read_labels(args.labels)
        if not args.verbose:
            # CJE narrates its own fitting at INFO. Useful when something
            # looks wrong, noise in front of the answer otherwise.
            logging.getLogger("cje").setLevel(logging.WARNING)
        try:
            result = calibrate.calibrate(rows, labels)
        except calibrate.NotInstalled as exc:
            print(str(exc), file=sys.stderr)
            return 2
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1

        journal.record("judge_calibrated", None, None, **result.as_event())
        print(f"judge's own average:  {result.judge_mean:.3f}")
        print(f"calibrated against you: {result.calibrated:.3f}"
              f"   (95% CI {result.ci[0]:.3f}–{result.ci[1]:.3f})")
        print(f"drift: {result.drift:+.3f}"
              f"   — {'the judge scores higher than you' if result.drift > 0 else 'the judge scores lower than you'}")
        print(f"\nbased on {result.n_labelled} label(s) over {result.n} scored task(s)")
        if result.rmse is not None:
            print(f"out-of-fold RMSE of the fit: {result.rmse:.3f}")
        if result.refused:
            print(
                f"\nDon't trust that number: {result.out_of_range:.1%} of the judge's scores "
                "fall outside the range you labeled.\nLabel a wider spread — a larger sample, "
                "or one that reaches the scores you skipped.",
                file=sys.stderr,
            )
            return 1
        if result.note:
            # CJE's own CAUTION: the estimate stands, the edges are thin.
            print(f"\nnote: {result.note}")
    finally:
        journal.close()
    return 0


def cmd_optimize(args: argparse.Namespace) -> int:
    """Search for better instructions for one slot, with GEPA."""
    config = config_module.load(args.config)
    spec = next((s for s in config.all_slots if s.name == args.slot), None)
    if spec is None:
        print(f"no slot named {args.slot!r}", file=sys.stderr)
        return 1

    criteria = evaluate.criteria_from(config.success)
    journal = Journal(config.db)
    try:
        scored = {e["task_id"] for e in journal.events(limit=10_000, type="task_scored")
                  if e.get("slot") == args.slot}
        tasks = [t for t in (journal.get(tid) for tid in scored) if t is not None]
        if not args.verbose:
            logging.getLogger("gepa").setLevel(logging.WARNING)

        run, score = optimize.bridge(spec, journal, criteria)
        try:
            result = optimize.optimize_context(
                spec, tasks, criteria, run, score, budget=args.budget, seed=args.seed
            )
        except optimize.NotInstalled as exc:
            print(str(exc), file=sys.stderr)
            return 2
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1

        print(f"{result.metric_calls} run(s) spent; "
              f"score {result.score_before:.2f} -> {result.score_after:.2f}")
        if not result.improved:
            print("\nnothing better found — the current instructions stand")
            return 0

        proposal = result.as_proposal()
        event_id = journal.record("proposal_made", None, proposal.target, **proposal.as_event())
        print(f"\nrecorded as proposal [{event_id}] — nothing changed.")
        print(f"review it with: grokklings proposals --accept {event_id}")
    finally:
        journal.close()
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    """Serve a read-only page showing what the colony is doing."""
    config = config_module.load(args.config)
    print(f"dashboard on http://{args.host}:{args.port} — read-only, Ctrl-C to stop")
    dashboard_module.serve(config, host=args.host, port=args.port)
    return 0


def cmd_cost(args: argparse.Namespace) -> int:
    """What the colony has spent, from the journal and a pinned price table."""
    config = config_module.load(args.config)
    budget = cost_module.budget_from(config.cost) or cost_module.Budget()
    prices_path = Path(config.db).parent / budget.prices

    if args.refresh:
        try:
            count = cost_module.refresh(prices_path)
        except Exception as exc:
            print(f"couldn't fetch the price table: {exc}", file=sys.stderr)
            return 1
        print(f"{count} model(s) pinned to {prices_path}")
        return 0

    try:
        prices = cost_module.Prices.load(prices_path)
    except cost_module.PricesMissing as exc:
        print(str(exc), file=sys.stderr)
        return 1

    journal = Journal(config.db)
    try:
        total = cost_module.spend(journal, prices)
        today = cost_module.spend(journal, prices, since=time.time() - cost_module.DAY)
    finally:
        journal.close()

    if not total.calls:
        print("no model calls in the journal yet")
        return 0
    print(f"${total.total:.4f} over {total.calls} model call(s)")
    print(f"${today.total:.4f} in the last 24 hours")
    if total.by_model:
        print("\nby model:")
        for model, amount in sorted(total.by_model.items(), key=lambda kv: -kv[1]):
            print(f"  ${amount:>10.4f}  {model}")
    if total.by_slot:
        print("\nby slot:")
        for slot_name, amount in sorted(total.by_slot.items(), key=lambda kv: -kv[1]):
            print(f"  ${amount:>10.4f}  {slot_name}")
    for name, cap in (("total", budget.limit), ("daily", budget.daily_limit)):
        if cap is not None:
            used = total.total if name == "total" else today.total
            print(f"\n{name} limit ${cap:.2f} — ${used:.4f} used ({used / cap:.0%})")

    if not total.complete:
        # The number above is not the whole number, and saying so matters
        # more than the number: an unpriced model spends invisibly.
        print(
            f"\nIncomplete: {total.unpriced_calls} call(s) on model(s) with no price — "
            f"{', '.join(sorted(total.unpriced_models))}.\n"
            "The figure above is lower than what was actually spent, and a limit "
            "will not be enforced while this is true.\n"
            "Try `grokklings cost --refresh`; if the model still isn't in the table, "
            "it has to be priced by hand.",
            file=sys.stderr,
        )
        return 1
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """Write the journal's model-facing events in OpenTelemetry's shape."""
    journal = Journal(config_module.load(args.config).db)
    try:
        events = journal.events(limit=args.limit)
        counts = otel.coverage(events)
        rows = list(otel.spans(events))
        if args.out:
            with Path(args.out).open("w", encoding="utf-8") as fh:
                for span in rows:
                    fh.write(json.dumps(span, ensure_ascii=False, default=str) + "\n")
            print(f"{len(rows)} span(s) written to {args.out}")
        else:
            for span in rows:
                print(json.dumps(span, ensure_ascii=False, default=str))
    finally:
        journal.close()

    # Say the split out loud: most of this journal is deliberately not
    # exportable, and a reader seeing few spans should know that's the design.
    print(
        f"\n{counts['exported']} of {counts['total']} event(s) speak the convention; "
        f"{counts['ours_only']} are ours alone and stay in the journal.",
        file=sys.stderr,
    )
    return 0


def cmd_pile(args: argparse.Namespace) -> int:
    """The unplaced pile — the input for the brain (phase 5)."""
    journal = Journal(config_module.load(args.config).db)
    try:
        tasks = journal.by_status(TaskStatus.UNPLACED, limit=args.limit)
        print(f"unplaced tasks: {len(tasks)}")
        for task in tasks:
            print(f"  {task.id}  {task.last_error}  <- {json.dumps(task.payload, ensure_ascii=False)[:120]}")
    finally:
        journal.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="grokklings", description="a grokklings colony")
    parser.add_argument("-v", "--verbose", action="store_true", help="verbose logging")
    subs = parser.add_subparsers(dest="command", required=True)

    def with_config(sub: argparse.ArgumentParser) -> argparse.ArgumentParser:
        sub.add_argument("-c", "--config", default="colony.toml", help="config file")
        return sub

    init = subs.add_parser("init", help="write an example config")
    init.add_argument("path", nargs="?", default="colony.toml")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init)

    with_config(subs.add_parser("run", help="bring the colony up")).set_defaults(func=cmd_run)

    submit = with_config(subs.add_parser("submit", help="manually submit a task"))
    submit.add_argument("--json", help="payload as JSON ('-' for stdin)")
    submit.add_argument("--file", help="a file with JSON")
    submit.add_argument("--source", default="manual")
    submit.set_defaults(func=cmd_submit)

    with_config(subs.add_parser("status", help="live state")).set_defaults(func=cmd_status)

    journal = with_config(subs.add_parser("journal", help="event history"))
    journal.add_argument("-n", "--tail", type=int, default=50)
    journal.add_argument("--task", help="filter by task")
    journal.add_argument("--type", help="filter by event type")
    journal.set_defaults(func=cmd_journal)

    task = with_config(subs.add_parser("task", help="a single task in full"))
    task.add_argument("id")
    task.set_defaults(func=cmd_task)

    pile = with_config(subs.add_parser("pile", help="the unplaced pile"))
    pile.add_argument("-n", "--limit", type=int, default=50)
    pile.set_defaults(func=cmd_pile)

    board = with_config(subs.add_parser("dashboard", help="a read-only page of live state"))
    board.add_argument("--host", default="127.0.0.1",
                       help="loopback by default: the page shows task payloads")
    board.add_argument("--port", type=int, default=8100)
    board.set_defaults(func=cmd_dashboard)

    spend = with_config(subs.add_parser("cost", help="what the colony has spent"))
    spend.add_argument("--refresh", action="store_true", help="fetch and pin the price table")
    spend.set_defaults(func=cmd_cost)

    export = with_config(subs.add_parser("export", help="journal events in OpenTelemetry's shape"))
    export.add_argument("--out", metavar="PATH", help="write JSON lines here instead of stdout")
    export.add_argument("-n", "--limit", type=int, default=10_000)
    export.set_defaults(func=cmd_export)

    check = with_config(subs.add_parser("calibrate", help="check the judge against your own labels"))
    group = check.add_mutually_exclusive_group(required=True)
    group.add_argument("--export", metavar="PATH", help="write a worksheet to label by hand")
    group.add_argument("--labels", metavar="PATH", help="read a filled worksheet and calibrate")
    check.add_argument("-n", "--size", type=int, default=30, help="how many tasks to label")
    check.add_argument("--seed", type=int, default=0, help="which random sample to draw")
    check.set_defaults(func=cmd_calibrate)

    tune = with_config(subs.add_parser("optimize", help="search for better instructions for a slot"))
    tune.add_argument("--slot", required=True, help="which slot's context to optimize")
    tune.add_argument("--budget", type=int, default=60,
                      help="max runs to spend; each is a worker call plus a judging call")
    tune.add_argument("--seed", type=int, default=0)
    tune.set_defaults(func=cmd_optimize)

    think = with_config(subs.add_parser("brain", help="propose roster changes; changes nothing"))
    think.add_argument("-n", "--limit", type=int, default=40)
    think.add_argument("--model", default=brain.DEFAULT_MODEL)
    think.set_defaults(func=cmd_brain)

    proposals = with_config(subs.add_parser("proposals", help="review, accept or reject proposals"))
    proposals.add_argument("--accept", metavar="ID", help="accept a proposal (prints the TOML to add)")
    proposals.add_argument("--reject", metavar="ID", help="reject a proposal")
    proposals.add_argument("--note", help="why, for the record")
    proposals.set_defaults(func=cmd_proposals)

    score = with_config(subs.add_parser("score", help="score finished tasks against [success]"))
    score.add_argument("-n", "--limit", type=int, default=50)
    score.add_argument("--rescore", action="store_true", help="score tasks that already have a score")
    score.set_defaults(func=cmd_score)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    try:
        return int(args.func(args))
    except config_module.ConfigError as exc:
        print(f"config: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
