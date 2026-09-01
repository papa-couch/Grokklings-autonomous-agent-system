"""Spawning and collapsing clones.

A slot's instance count has been static: `max_instances` decided it at
startup and nothing moved it. This is what moves it — a watcher over the
queue that spawns a clone when work piles up and gives it back when it
doesn't.

The whole difficulty is not the spawning. It's not doing it constantly.
A rule that reacts to the instantaneous queue depth will spawn on a burst,
collapse on the lull a second later, and spawn again — churning instances
without ever helping. So every decision has to hold: pressure must persist
for `grow_after` seconds before a clone appears, and idleness for
`shrink_after` before one goes away. The two are deliberately asymmetric —
being slow to grow costs latency, being slow to shrink costs nothing, so
shrinking waits much longer.

**Nothing scales without a `[scaling]` section.** Without one, a slot gets
`max_instances` instances at startup and keeps them, exactly as before.

The counts are the only thing this touches. It never routes, never
retries, and never decides what a task is — it reads the queue and adjusts
how many hands are available.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("grokklings.scaling")

DEFAULT_MIN_INSTANCES = 1
DEFAULT_QUEUE_PER_INSTANCE = 3
DEFAULT_GROW_AFTER = 10.0
DEFAULT_SHRINK_AFTER = 60.0

# Tasks that haven't been routed yet sit under this key in queue_depth().
UNASSIGNED = "(unassigned)"


@dataclass
class Policy:
    """When a slot deserves another pair of hands, and when it doesn't."""

    min_instances: int = DEFAULT_MIN_INSTANCES
    queue_per_instance: int = DEFAULT_QUEUE_PER_INSTANCE
    grow_after: float = DEFAULT_GROW_AFTER
    shrink_after: float = DEFAULT_SHRINK_AFTER

    def __post_init__(self) -> None:
        if self.min_instances < 1:
            raise ValueError("[scaling]: min_instances must be at least 1")
        if self.queue_per_instance < 1:
            raise ValueError("[scaling]: queue_per_instance must be at least 1")
        if self.grow_after < 0 or self.shrink_after < 0:
            raise ValueError("[scaling]: grow_after and shrink_after can't be negative")


def policy_from(config: dict[str, Any] | None) -> Policy | None:
    """A colony with no [scaling] section gets no scaler.

    An empty `[scaling]` section is not the same as an absent one — writing
    the header and no keys asks for these defaults, and silently doing
    nothing there would be a trap.
    """
    if config is None:
        return None
    return Policy(
        min_instances=int(config.get("min_instances", DEFAULT_MIN_INSTANCES)),
        queue_per_instance=int(config.get("queue_per_instance", DEFAULT_QUEUE_PER_INSTANCE)),
        grow_after=float(config.get("grow_after", DEFAULT_GROW_AFTER)),
        shrink_after=float(config.get("shrink_after", DEFAULT_SHRINK_AFTER)),
    )


@dataclass
class Pressure:
    """How long a slot has been asking for more, or for less.

    Kept per slot rather than globally: one busy slot shouldn't start the
    clock for a quiet one.
    """

    crowded_since: float | None = None
    idle_since: float | None = None

    def crowded(self, now: float) -> None:
        self.idle_since = None
        if self.crowded_since is None:
            self.crowded_since = now

    def idle(self, now: float) -> None:
        self.crowded_since = None
        if self.idle_since is None:
            self.idle_since = now

    def steady(self) -> None:
        self.crowded_since = None
        self.idle_since = None

    def held(self, since: float | None, now: float, seconds: float) -> bool:
        return since is not None and (now - since) >= seconds


class Scaler:
    """Adjusts instance counts from the queue, one step at a time.

    One at a time on purpose: a rule that jumps straight to the count the
    current backlog seems to justify overshoots every burst, and the
    correction is another spawn-collapse cycle.
    """

    def __init__(self, policy: Policy, journal: Any, entry: str | None = None) -> None:
        self.policy = policy
        self.journal = journal
        # Freshly submitted tasks aren't routed yet, so they sit under
        # "(unassigned)". With one entry slot that backlog is unambiguously
        # its own; with several the router decides later and guessing here
        # would scale the wrong slot.
        self.entry = entry
        self.pressure: dict[str, Pressure] = {}

    def backlog(self, depth: dict[str, int], slot: str) -> int:
        waiting = depth.get(slot, 0)
        if slot == self.entry:
            waiting += depth.get(UNASSIGNED, 0)
        return waiting

    def tick(self, slots: dict[str, Any], now: float) -> list[tuple[str, str]]:
        """One pass. Returns the (slot, action) pairs it actually carried out."""
        depth = self.journal.queue_depth()
        done: list[tuple[str, str]] = []

        for name, runtime in slots.items():
            if runtime.spec.terminal:
                continue  # an output scales with its own traffic, not the queue's
            state = self.pressure.setdefault(name, Pressure())
            count = len(runtime.instances)
            waiting = self.backlog(depth, name)
            ceiling = max(1, runtime.spec.max_instances)

            wants_more = waiting > self.policy.queue_per_instance * count and count < ceiling
            wants_fewer = waiting == 0 and runtime.load == 0 and count > self.policy.min_instances

            if wants_more:
                state.crowded(now)
            elif wants_fewer:
                state.idle(now)
            else:
                state.steady()
                continue

            if wants_more and state.held(state.crowded_since, now, self.policy.grow_after):
                runtime.add_instance()
                self._record(name, "spawned", waiting, count + 1)
                state.steady()
                done.append((name, "spawned"))
            elif wants_fewer and state.held(state.idle_since, now, self.policy.shrink_after):
                spare = runtime.free_instance()
                # remove_instance refuses the last one and a busy one; if it
                # declines, nothing was wrong — just nothing to give back.
                if spare is not None and runtime.remove_instance(spare):
                    self._record(name, "collapsed", waiting, count - 1)
                    done.append((name, "collapsed"))
                state.steady()
        return done

    def _record(self, slot: str, action: str, backlog: int, instances: int) -> None:
        log.info("slot %s %s: backlog %d, now %d instance(s)", slot, action, backlog, instances)
        self.journal.record(
            "scaling_decision", None, slot, action=action, backlog=backlog, instances=instances
        )
