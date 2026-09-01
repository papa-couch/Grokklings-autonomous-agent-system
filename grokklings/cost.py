"""What the colony has spent, and stopping it before it spends more.

Every model call already writes its token usage to the journal. All that was
missing was the rates — and keeping those current by hand is the part nobody
does. So the rates are borrowed: litellm publishes a maintained table of
per-token prices as one JSON file, MIT, with no dependency on litellm
itself. This module pins a local copy and multiplies.

**Pinned, not fetched at runtime.** A colony that phones out mid-tick for
prices has a network dependency in its hot path and a spend figure that
changes under it. `grokklings cost --refresh` updates the copy, deliberately,
when you ask.

The one thing here that must not be got wrong:

**A model with no price is unknown cost, not zero cost.** The tempting
implementation treats a missing rate as 0.0 and carries on. A colony running
an unpriced model would then report spending nothing while spending
steadily, and a budget would never trigger — the exact failure a budget
exists to prevent. Unpriced calls are counted and named separately, and a
limit refuses to enforce against a total it knows is incomplete.

Hitting a limit **pauses** the colony rather than killing it: no new tasks
are taken, in-flight ones are seen through. That's the stop control the
design doc already asks for in section 12, reused rather than reinvented.

No new dependency — `urllib` fetches the table, `json` reads it.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("grokklings.cost")

# The table litellm maintains. One file, MIT, no package needed.
PRICES_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)
DEFAULT_PRICES = "prices.json"

# How a bare model name is qualified in that table.
PROVIDER_PREFIXES = {"grok": "xai", "claude": "anthropic", "gpt": "openai"}

# A rolling window, so no timezone has to be agreed on.
DAY = 24 * 60 * 60


class PricesMissing(Exception):
    """There's no pinned price table to read."""


def refresh(path: str | Path, url: str = PRICES_URL, timeout: float = 60.0) -> int:
    """Fetch the table and pin it. Returns how many models it holds."""
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - a pinned https URL
        raw = json.loads(response.read().decode("utf-8"))
    Path(path).write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return len(raw)


@dataclass
class Prices:
    """Per-token rates, from the pinned copy."""

    table: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> "Prices":
        path = Path(path)
        if not path.exists():
            raise PricesMissing(
                f"no price table at {path} — run `grokklings cost --refresh` to pin one"
            )
        return cls(table=json.loads(path.read_text(encoding="utf-8")))

    def entry(self, model: str) -> dict[str, Any] | None:
        """Find a model, trying the bare name and then the qualified one.

        Our configs say `grok-4.6`; the table says `xai/grok-4.6`.
        """
        if model in self.table:
            return self.table[model]
        lowered = model.lower()
        for prefix, provider in PROVIDER_PREFIXES.items():
            if prefix in lowered:
                qualified = f"{provider}/{model}"
                if qualified in self.table:
                    return self.table[qualified]
        return None

    def rate(self, model: str) -> tuple[float, float] | None:
        """(input, output) cost per token, or None when the model isn't priced."""
        entry = self.entry(model)
        if not entry:
            return None
        given_in = entry.get("input_cost_per_token")
        given_out = entry.get("output_cost_per_token")
        if given_in is None and given_out is None:
            return None
        return float(given_in or 0.0), float(given_out or 0.0)


@dataclass
class Spend:
    """What was spent, and how much of it we couldn't price."""

    total: float = 0.0
    calls: int = 0
    by_model: dict[str, float] = field(default_factory=dict)
    by_slot: dict[str, float] = field(default_factory=dict)
    unpriced_calls: int = 0
    unpriced_models: set[str] = field(default_factory=set)
    last_seq: int = 0

    @property
    def complete(self) -> bool:
        """Whether `total` is the whole story. It isn't if anything went unpriced."""
        return not self.unpriced_calls

    def add(self, other: "Spend") -> None:
        self.total += other.total
        self.calls += other.calls
        for model, amount in other.by_model.items():
            self.by_model[model] = self.by_model.get(model, 0.0) + amount
        for slot, amount in other.by_slot.items():
            self.by_slot[slot] = self.by_slot.get(slot, 0.0) + amount
        self.unpriced_calls += other.unpriced_calls
        self.unpriced_models |= other.unpriced_models
        self.last_seq = max(self.last_seq, other.last_seq)


def spend(
    journal: Any,
    prices: Prices,
    after_seq: int = 0,
    since: float | None = None,
    limit: int = 100_000,
) -> Spend:
    """Add up what the model calls in the journal cost.

    `after_seq` lets a caller total only what's new, so a colony checking its
    budget every tick doesn't re-add its whole history each time.
    """
    out = Spend(last_seq=after_seq)
    for event in journal.events(limit=limit, type="worker_note"):
        seq = int(event.get("seq") or 0)
        if seq <= after_seq:
            continue
        if since is not None and (event.get("ts") or 0) < since:
            continue
        data = event.get("data") or {}
        model = data.get("model")
        tokens_in = data.get("input_tokens")
        if not model or tokens_in is None:
            continue  # a free-form note, not a model call

        out.last_seq = max(out.last_seq, seq)
        out.calls += 1
        rate = prices.rate(str(model))
        if rate is None:
            # Not zero. Saying zero here is how a budget silently stops working.
            out.unpriced_calls += 1
            out.unpriced_models.add(str(model))
            continue
        cost = rate[0] * float(tokens_in) + rate[1] * float(data.get("output_tokens") or 0)
        out.total += cost
        out.by_model[str(model)] = out.by_model.get(str(model), 0.0) + cost
        slot = event.get("slot") or "(none)"
        out.by_slot[slot] = out.by_slot.get(slot, 0.0) + cost
    return out


@dataclass
class Budget:
    """What the colony is allowed to spend before it stops taking work."""

    limit: float | None = None        # since the journal began
    daily_limit: float | None = None  # over a rolling 24 hours
    prices: str = DEFAULT_PRICES

    def __post_init__(self) -> None:
        for name, value in (("limit", self.limit), ("daily_limit", self.daily_limit)):
            if value is not None and value <= 0:
                raise ValueError(f"[cost]: {name} must be greater than zero")

    def __bool__(self) -> bool:
        return self.limit is not None or self.daily_limit is not None


def budget_from(config: dict[str, Any] | None) -> Budget | None:
    """No [cost] section, no accounting. An empty one asks for the defaults."""
    if config is None:
        return None
    return Budget(
        limit=float(config["limit"]) if config.get("limit") is not None else None,
        daily_limit=(
            float(config["daily_limit"]) if config.get("daily_limit") is not None else None
        ),
        prices=str(config.get("prices") or DEFAULT_PRICES),
    )


class Guard:
    """Watches the running total and pauses the colony when it's over.

    Pauses rather than kills: in-flight tasks are seen through, which is what
    the existing stop control already does. A paused colony can be resumed
    once the limit is raised or the window rolls past.
    """

    def __init__(self, budget: Budget, prices: Prices, journal: Any, now: Any = None) -> None:
        self.budget = budget
        self.prices = prices
        self.journal = journal
        self.running = Spend()
        self.tripped: str | None = None

    def total(self) -> Spend:
        """Everything spent so far, extended with whatever is new."""
        self.running.add(spend(self.journal, self.prices, after_seq=self.running.last_seq))
        return self.running

    def over(self, now: float) -> str | None:
        """Which limit is exceeded, if any. None when the colony may carry on."""
        running = self.total()
        if not running.complete:
            # Refusing to enforce against a total known to be incomplete is
            # the honest move — but silently allowing spend would be worse,
            # so it's said out loud, once per new unpriced model.
            log.warning(
                "cost is not being enforced: %d call(s) on unpriced model(s) %s — "
                "the total is incomplete",
                running.unpriced_calls, ", ".join(sorted(running.unpriced_models)),
            )
            return None
        if self.budget.limit is not None and running.total >= self.budget.limit:
            return f"total spend ${running.total:.2f} reached the limit of ${self.budget.limit:.2f}"
        if self.budget.daily_limit is not None:
            today = spend(self.journal, self.prices, since=now - DAY)
            if today.complete and today.total >= self.budget.daily_limit:
                return (
                    f"spend over the last 24h, ${today.total:.2f}, reached the daily "
                    f"limit of ${self.budget.daily_limit:.2f}"
                )
        return None
