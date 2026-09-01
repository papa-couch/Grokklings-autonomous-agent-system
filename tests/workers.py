"""Workers for tests: each one exercises one branch of the contract."""

from __future__ import annotations

import asyncio
from collections import Counter

from grokklings.model import Task, Verdict
from grokklings.slots import WorkerContext

calls: Counter[str] = Counter()
# Side effects are counted separately from calls to the worker — that's the
# whole point: the worker gets called twice, but the outside world only
# gets touched once.
effects: Counter[str] = Counter()


def reset() -> None:
    calls.clear()
    effects.clear()
    routers_used.clear()


def _do_effect(name: str = "side_effect") -> dict:
    effects[name] += 1
    return {"name": name, "n": effects[name]}


def done(task: Task, ctx: WorkerContext) -> Verdict:
    calls[ctx.slot.name] += 1
    return Verdict.done({"seen": task.payload})


async def route_next(task: Task, ctx: WorkerContext) -> Verdict:
    calls[ctx.slot.name] += 1
    return Verdict.next(ctx.options["next"], {"from": ctx.slot.name})


def refuse(task: Task, ctx: WorkerContext) -> Verdict:
    calls[ctx.slot.name] += 1
    return Verdict.not_mine("not my part of it")


def fail(task: Task, ctx: WorkerContext) -> Verdict:
    calls[ctx.slot.name] += 1
    return Verdict.fail("bad data")


def garbage(task: Task, ctx: WorkerContext) -> dict:
    """Garbage instead of a verdict — the dispatcher must send it to retry."""
    calls[ctx.slot.name] += 1
    return {"decision": "sort of done I guess"}


def crash(task: Task, ctx: WorkerContext) -> Verdict:
    """Crashes until only the last attempt is left."""
    calls[ctx.slot.name] += 1
    if calls[ctx.slot.name] < ctx.options.get("succeed_on", 3):
        raise RuntimeError("the instance crashed")
    return Verdict.done({"recovered_on_attempt": ctx.attempt})


async def hang(task: Task, ctx: WorkerContext) -> Verdict:
    """Hangs longer than the slot's timeout."""
    calls[ctx.slot.name] += 1
    await asyncio.sleep(30)
    return Verdict.done({})


async def route_back(task: Task, ctx: WorkerContext) -> Verdict:
    """Tries to send the task back to a slot that already saw it."""
    calls[ctx.slot.name] += 1
    return Verdict.next(ctx.options["next"], {"from": ctx.slot.name})


# -------------------------------------------------------------------- routing


class ScriptedRouter:
    """A router whose answer is set in the config, so a test doesn't need a key.

    `pick` is the slot name to return; "none" means nobody fit, and "boom"
    raises — the case where the router itself is broken.
    """

    def __init__(self, pick: str = "none") -> None:
        self.answer = pick
        self.seen: list[list[str]] = []

    async def pick(self, task: Task, candidates: list) -> str | None:
        self.seen.append([spec.name for spec in candidates])
        routers_used.append(self)
        if self.answer == "boom":
            raise RuntimeError("the router is down")
        return None if self.answer == "none" else self.answer


# Every router a colony builds registers itself here when asked, so a test
# can assert it was never consulted.
routers_used: list[ScriptedRouter] = []


# ------------------------------------------------------- non-repeatable steps


def once_then_crash(task: Task, ctx: WorkerContext) -> Verdict:
    """A side effect via once, then a crash — before the verdict is returned."""
    recorded = ctx.once("side_effect", _do_effect)
    calls["once_then_crash"] += 1
    if calls["once_then_crash"] < 2:
        raise RuntimeError("crashed right after the side effect")
    return Verdict.done({"recorded": recorded})


def crashing_step(task: Task, ctx: WorkerContext) -> Verdict:
    """Crashes inside the step: the step never gets recorded, so it must retry."""
    def boom():
        effects["step_attempt"] += 1
        raise RuntimeError("the step failed")

    ctx.once("step", boom)
    return Verdict.done({})


def two_keys_then_crash(task: Task, ctx: WorkerContext) -> Verdict:
    """Two different steps: both must run exactly once."""
    ctx.once("first", _do_effect, "first")
    ctx.once("second", _do_effect, "second")
    calls["two_keys_then_crash"] += 1
    if calls["two_keys_then_crash"] < 2:
        raise RuntimeError("crashed after both steps")
    return Verdict.done({})


async def once_async_then_crash(task: Task, ctx: WorkerContext) -> Verdict:
    """The same, for a coroutine worker."""
    async def effect():
        return _do_effect("coroutine")

    recorded = await ctx.once_async("side_effect", effect)
    calls["once_async_then_crash"] += 1
    if calls["once_async_then_crash"] < 2:
        raise RuntimeError("crashed right after the side effect")
    return Verdict.done({"recorded": recorded})


def unserializable_step(task: Task, ctx: WorkerContext) -> Verdict:
    """A step that returned something that won't survive a restart."""
    ctx.once("bad", lambda: object())
    return Verdict.done({})


def flaky_file_output(task: Task, ctx: WorkerContext) -> Verdict:
    """The real file output, crashing right after it writes."""
    from grokklings.outputs import file as file_output

    verdict = file_output.handle(task, ctx)
    calls["flaky_file_output"] += 1
    if calls["flaky_file_output"] < 2:
        raise RuntimeError("crashed after writing the file")
    return verdict


def flaky_webhook_output(task: Task, ctx: WorkerContext) -> Verdict:
    """The real webhook output, crashing right after it sends."""
    from grokklings.outputs import webhook as webhook_output

    verdict = webhook_output.handle(task, ctx)
    calls["flaky_webhook_output"] += 1
    if calls["flaky_webhook_output"] < 2:
        raise RuntimeError("crashed after sending")
    return verdict
