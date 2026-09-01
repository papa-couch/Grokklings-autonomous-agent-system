"""A page that shows what the colony is doing. Nothing else.

Everything here already existed — `grokklings status`, `journal`, `pile`,
`cost` and `proposals` print all of it. This is the same information
without typing five commands, refreshing itself.

Three things it deliberately is not:

**It cannot change anything.** The handler implements `do_GET` and nothing
else, so a POST gets 405 from `BaseHTTPRequestHandler` itself rather than
from a check someone could remove. That's the difference between a
read-only page and a page that currently has no buttons — accepting a
proposal or lifting a cost limit from a browser would turn the consent
guarantee in [0016](../docs/decisions/0016-brain-proposes-never-applies.md)
into a question about who can reach a port.

**It doesn't run inside the colony.** Like the brain and the scorer, it
reads the database from outside. Nothing it does can slow a tick or lose a
task, and it works on a stopped colony — the queue and journal live in
SQLite, so "what's waiting" is answerable whether or not anything is
running.

**It binds to loopback.** The journal holds task payloads: whatever the
user's sources feed in. Publishing that on every interface by accident is
a worse default than making someone type `--host`.

One honest limit: what it shows is what's on disk. A slot's *configured*
ceiling comes from the config and the tasks in progress come from the
database, but the live in-process instance count isn't visible from
another process, so it isn't claimed.
"""

from __future__ import annotations

import json
import logging
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import brain, cost as cost_module
from .scaling import UNASSIGNED
from .config import ColonyConfig
from .journal import Journal
from .model import TaskStatus

log = logging.getLogger("grokklings.dashboard")

LOOPBACK = {"127.0.0.1", "::1", "localhost"}
RECENT_EVENTS = 40


def snapshot(config: ColonyConfig, journal: Journal) -> dict[str, Any]:
    """Everything the page shows, read from the database."""
    depth = journal.queue_depth()
    in_progress = journal.in_progress()
    working = {}
    for task in in_progress:
        working[task.slot or "(none)"] = working.get(task.slot or "(none)", 0) + 1

    slots = [
        {
            "name": spec.name,
            "description": spec.description,
            "terminal": spec.terminal,
            "ceiling": spec.max_instances,
            "waiting": depth.get(spec.name, 0),
            "working": working.get(spec.name, 0),
        }
        for spec in config.all_slots
    ]

    paused_because = None
    tripped = journal.events(limit=1, type="cost_limit_reached")
    if tripped:
        paused_because = (tripped[-1].get("data") or {}).get("reason")

    return {
        "colony": config.name,
        "at": time.time(),
        "slots": slots,
        "unrouted": depth.get(UNASSIGNED, 0),
        "tasks": journal.status_counts(),
        "in_progress": [
            {"task": t.id, "slot": t.slot, "attempt": t.attempts} for t in in_progress
        ],
        "unplaced": [
            {"task": t.id, "why": t.last_error}
            for t in journal.by_status(TaskStatus.UNPLACED, limit=10)
        ],
        "proposals": [
            {"id": p.id, "kind": p.kind, "target": p.target, "why": p.rationale}
            for p in brain.pending(journal, limit=20)
        ],
        "cost": _cost(config, journal),
        "scores": _scores(journal),
        "events": [
            {
                "seq": e.get("seq"),
                "at": e.get("ts"),
                "type": e.get("type"),
                "task": e.get("task_id"),
                "slot": e.get("slot"),
            }
            for e in journal.events(limit=RECENT_EVENTS)
        ],
        "limit_reason": paused_because,
    }


def _cost(config: ColonyConfig, journal: Journal) -> dict[str, Any] | None:
    """None when there's no price table — an absent figure, not a zero."""
    budget = cost_module.budget_from(config.cost)
    if budget is None:
        return None
    try:
        prices = cost_module.Prices.load(Path(config.db).parent / budget.prices)
    except cost_module.PricesMissing:
        return {"unavailable": "no price table pinned — run `grokklings cost --refresh`"}
    total = cost_module.spend(journal, prices)
    today = cost_module.spend(journal, prices, since=time.time() - cost_module.DAY)
    return {
        "total": round(total.total, 4),
        "today": round(today.total, 4),
        "calls": total.calls,
        "limit": budget.limit,
        "daily_limit": budget.daily_limit,
        "complete": total.complete,
        "unpriced_models": sorted(total.unpriced_models),
    }


def _scores(journal: Journal) -> dict[str, Any] | None:
    scored = journal.events(limit=1000, type="task_scored")
    if not scored:
        return None
    values = [(e.get("data") or {}).get("score") for e in scored]
    values = [float(v) for v in values if v is not None]
    violations = sum(1 for e in scored if (e.get("data") or {}).get("violations"))
    return {
        "count": len(values),
        "average": round(sum(values) / len(values), 3) if values else None,
        "met": sum(1 for e in scored if (e.get("data") or {}).get("met")),
        "violations": violations,
    }


PAGE = """<!doctype html>
<meta charset="utf-8">
<title>grokklings</title>
<style>
  :root {
    --bg: #fbfbfa; --fg: #1c1c1a; --dim: #6b6b66; --line: #e2e2dd;
    --card: #ffffff; --warn: #a5401a; --ok: #2f6b3a; --accent: #3a4a8c;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #16161a; --fg: #e6e6e2; --dim: #8f8f88; --line: #2b2b31;
      --card: #1d1d22; --warn: #e08a5c; --ok: #7fb98a; --accent: #93a4e3;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 1.5rem; background: var(--bg); color: var(--fg);
    font: 14px/1.5 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  }
  header { display: flex; align-items: baseline; gap: 1rem; margin-bottom: 1.25rem; }
  h1 { font-size: 1.1rem; margin: 0; font-weight: 600; }
  .dim { color: var(--dim); }
  .grid { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }
  section {
    background: var(--card); border: 1px solid var(--line);
    border-radius: 8px; padding: 0.9rem 1rem;
  }
  h2 {
    font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.07em;
    color: var(--dim); margin: 0 0 0.6rem; font-weight: 600;
  }
  table { width: 100%; border-collapse: collapse; }
  td, th { text-align: left; padding: 0.22rem 0; vertical-align: top; }
  th { font-weight: 500; color: var(--dim); font-size: 0.8rem; }
  td.n, th.n { text-align: right; font-variant-numeric: tabular-nums; }
  .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.82rem; }
  .warn { color: var(--warn); }
  .ok { color: var(--ok); }
  .big { font-size: 1.5rem; font-variant-numeric: tabular-nums; }
  .bar { height: 4px; background: var(--line); border-radius: 2px; overflow: hidden; margin-top: 0.4rem; }
  .bar > i { display: block; height: 100%; background: var(--accent); }
  .bar > i.over { background: var(--warn); }
  /* A floor, not a measurement: hatched so it doesn't read like one. */
  .bar.floor > i {
    background: repeating-linear-gradient(45deg,
      var(--dim) 0 3px, transparent 3px 6px);
  }
  .note {
    margin-top: 0.6rem; padding: 0.5rem 0.6rem; border-radius: 6px;
    background: color-mix(in srgb, var(--warn) 12%, transparent); color: var(--warn);
    font-size: 0.85rem;
  }
  ul { margin: 0; padding-left: 1.1rem; }
  li { margin-bottom: 0.3rem; }
  footer { margin-top: 1.25rem; color: var(--dim); font-size: 0.8rem; }
  .events td { border-top: 1px solid var(--line); padding: 0.3rem 0.5rem 0.3rem 0; }
  .wide { grid-column: 1 / -1; }
</style>
<header>
  <h1 id="name">grokklings</h1>
  <span class="dim" id="when"></span>
  <span class="dim" style="margin-left:auto">read-only</span>
</header>
<div class="grid" id="grid"></div>
<footer>
  Reads the database, never writes to it. Refreshes every 5s.
</footer>
<script>
const fmt = (n) => n === null || n === undefined ? "—" : n;
const money = (n) => "$" + Number(n).toFixed(4);
const ago = (t) => {
  if (!t) return "";
  const s = Math.max(0, Math.round(Date.now() / 1000 - t));
  if (s < 60) return s + "s ago";
  if (s < 3600) return Math.round(s / 60) + "m ago";
  return Math.round(s / 3600) + "h ago";
};
const el = (html) => { const d = document.createElement("div"); d.innerHTML = html.trim(); return d.firstChild; };

function rows(obj) {
  const keys = Object.keys(obj || {});
  if (!keys.length) return '<tr><td class="dim">nothing yet</td></tr>';
  return keys.map(k => `<tr><td>${k}</td><td class="n">${obj[k]}</td></tr>`).join("");
}

function render(s) {
  document.getElementById("name").textContent = s.colony;
  document.getElementById("when").textContent = "updated " + new Date(s.at * 1000).toLocaleTimeString();
  const grid = document.getElementById("grid");
  grid.innerHTML = "";

  if (s.limit_reason) {
    grid.appendChild(el(`<section class="wide"><h2>cost limit reached</h2>
      <div class="warn">${s.limit_reason}</div>
      <div class="dim" style="margin-top:.4rem">The colony pauses when this trips: no new tasks are
      taken, in-flight ones are seen through. Lifting it is a config change and a restart.</div></section>`));
  }

  grid.appendChild(el(`<section><h2>tasks</h2><table>${rows(s.tasks)}</table>
    ${s.unrouted ? `<div class="dim" style="margin-top:.4rem">${s.unrouted} not routed yet</div>` : ""}
    </section>`));

  const slotRows = s.slots.map(x => `<tr>
      <td>${x.name}${x.terminal ? ' <span class="dim">out</span>' : ""}</td>
      <td class="n">${x.working}</td><td class="n">${x.waiting}</td><td class="n dim">${x.ceiling}</td>
    </tr>`).join("");
  grid.appendChild(el(`<section><h2>slots</h2><table>
    <tr><th>name</th><th class="n">working</th><th class="n">waiting</th><th class="n">max</th></tr>
    ${slotRows}</table></section>`));

  if (s.cost) {
    let body;
    if (s.cost.unavailable) {
      body = `<div class="dim">${s.cost.unavailable}</div>`;
    } else {
      const cap = s.cost.limit, used = s.cost.total;
      const pct = cap ? Math.min(100, (used / cap) * 100) : null;
      // When something went unpriced the figure is a floor, not a measurement,
      // and the bar would otherwise read like one.
      const floor = !s.cost.complete;
      body = `<div class="big">${floor ? '<span class="dim" style="font-size:.9rem">at least </span>' : ""}${money(used)}</div>
        <div class="dim">${money(s.cost.today)} in the last 24h · ${s.cost.calls} call(s)</div>
        ${cap ? `<div class="bar${floor ? " floor" : ""}"><i class="${used >= cap ? "over" : ""}" style="width:${pct}%"></i></div>
          <div class="dim">of ${money(cap)}${floor ? " — at least this much" : ""}</div>` : ""}
        ${s.cost.complete ? "" : `<div class="note">Incomplete: unpriced model(s)
          ${s.cost.unpriced_models.join(", ")}. The figure is lower than what was spent, and no
          limit is enforced while this is true.</div>`}`;
    }
    grid.appendChild(el(`<section><h2>spend</h2>${body}</section>`));
  }

  if (s.scores) {
    grid.appendChild(el(`<section><h2>quality</h2>
      <div class="big">${fmt(s.scores.average)}</div>
      <div class="dim">${s.scores.met} of ${s.scores.count} met the bar</div>
      ${s.scores.violations ? `<div class="note">${s.scores.violations} task(s) crossed a red line</div>` : ""}
      </section>`));
  }

  if (s.proposals.length) {
    grid.appendChild(el(`<section><h2>proposals waiting</h2><ul>${
      s.proposals.map(p => `<li><span class="mono">[${p.id}]</span> ${p.kind}: <b>${p.target}</b>
        <div class="dim">${p.why}</div></li>`).join("")
    }</ul><div class="dim" style="margin-top:.5rem">Accept from the CLI:
      <span class="mono">grokklings proposals --accept &lt;id&gt;</span></div></section>`));
  }

  if (s.unplaced.length) {
    grid.appendChild(el(`<section><h2>unplaced pile</h2><ul>${
      s.unplaced.map(u => `<li><span class="mono">${u.task}</span>
        <div class="dim">${u.why || ""}</div></li>`).join("")
    }</ul></section>`));
  }

  if (s.in_progress.length) {
    grid.appendChild(el(`<section><h2>in progress</h2><table>${
      s.in_progress.map(t => `<tr><td class="mono">${t.task}</td><td>${t.slot || ""}</td>
        <td class="n dim">try ${t.attempt}</td></tr>`).join("")
    }</table></section>`));
  }

  grid.appendChild(el(`<section class="wide"><h2>recent events</h2>
    <table class="events">${s.events.slice().reverse().map(e => `<tr>
      <td class="dim n mono">${e.seq}</td><td class="mono">${e.type}</td>
      <td class="mono dim">${e.task || ""}</td><td class="dim">${e.slot || ""}</td>
      <td class="dim n">${ago(e.at)}</td></tr>`).join("")}</table></section>`));
}

async function tick() {
  try {
    render(await (await fetch("/api")).json());
  } catch (err) {
    document.getElementById("when").textContent = "can't reach the colony";
  }
}
tick();
setInterval(tick, 5000);
</script>
"""


def _handler(config: ColonyConfig) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        # Only do_GET exists. Anything else is refused by the base class
        # with 501/405 — the read-only guarantee is the absence of the
        # methods, not a check inside them.

        def do_GET(self) -> None:  # noqa: N802 - the base class's name
            if self.path.startswith("/api"):
                journal = Journal(config.db)
                try:
                    body = json.dumps(
                        snapshot(config, journal), ensure_ascii=False, default=str
                    ).encode("utf-8")
                finally:
                    journal.close()
                self._send(body, "application/json; charset=utf-8")
            elif self.path in ("/", "/index.html"):
                self._send(PAGE.encode("utf-8"), "text/html; charset=utf-8")
            else:
                self.send_error(404)

        def _send(self, body: bytes, content_type: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: Any) -> None:
            log.debug(fmt, *args)

    return Handler


def serve(config: ColonyConfig, host: str = "127.0.0.1", port: int = 8100) -> None:
    """Run until interrupted. A separate process from the colony."""
    if host not in LOOPBACK:
        # Said once, loudly: the journal holds whatever the sources fed in.
        log.warning(
            "listening on %s, not loopback — this page shows task payloads from the "
            "journal to anyone who can reach it",
            host,
        )
    server = ThreadingHTTPServer((host, port), _handler(config))
    log.info("dashboard on http://%s:%d (read-only)", host, port)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
