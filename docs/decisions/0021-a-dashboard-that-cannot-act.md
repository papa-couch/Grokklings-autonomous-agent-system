# 0021. A dashboard that can look and cannot act

- Status: **accepted**
- Date: 2026-08-27
- Related: [0016](0016-brain-proposes-never-applies.md), [0020](0020-cost-and-limits.md), [0007](0007-otel-event-names.md)

## Context

Section 11 asks for live state and a dashboard. Every piece of the data
already existed — `status`, `journal`, `pile`, `cost` and `proposals` print
all of it — so what was missing was only the showing.

"Dashboard" turned out to mean three different things, and choosing which
one was most of the decision:

1. **Someone else's**, fed by `grokklings export`
   ([0007](0007-otel-event-names.md)) — nothing to build, though Phoenix is
   Elastic-licensed and Langfuse is four services.
2. **Ours, read-only** — one page showing what's on disk.
3. **A control panel** — the same page with buttons: pause, resume, accept
   a proposal, raise a limit.

## Decision

The second. `grokklings dashboard` serves one page from
`grokklings/dashboard.py` on stdlib `http.server`, with no dependency.

**It cannot write, by construction.** The handler defines `do_GET` and
nothing else, so a POST is refused by `BaseHTTPRequestHandler` itself —
verified against a real socket, which answers 501. The distinction is not
pedantry: a page with no buttons is one commit away from a page with
buttons, while a handler with no write method has to gain one deliberately.

That is also why the third option isn't built. Accepting a proposal from a
browser would move the guarantee in
[0016](0016-brain-proposes-never-applies.md) — consent is a required
argument, and `apply` raises without it — into a question about who can
reach a port. Lifting a cost limit from a browser would let anyone with
that access undo the stop [0020](0020-cost-and-limits.md) put there. Both
are defensible features; neither is defensible as a side effect of adding
a page.

**It runs outside the colony**, reading the database like the brain and the
scorer do. It can't slow a tick or lose a task, and it works on a stopped
colony — the queue lives in SQLite, so "what's waiting" is answerable
either way.

**It binds to loopback.** The journal holds task payloads: whatever the
sources fed in. Publishing that on every interface by accident is a worse
default than making someone type `--host`, and a non-loopback bind says so
in the log.

**An absent figure is shown as absent.** With no pinned price table, spend
reads "no price table pinned", not `$0.00`. And when the total is
incomplete because something went unpriced, the page says **"at least"**
and hatches the progress bar. A floor drawn as a measurement is the same
lie [0020](0020-cost-and-limits.md) refused to tell in the CLI, and the
first version of this page told it — an 84% bar sitting directly above a
warning that the number was too low.

## Consequences

- Anything the page can't show has to be fetched from the CLI. That is the
  trade for its being unable to act.
- It polls every 5 seconds and re-reads the journal each time. Fine for one
  colony on one machine; it is not an efficient query pattern, and a large
  journal makes each poll slower.
- The live in-process instance count isn't visible from another process, so
  the page shows the configured ceiling and the tasks actually in progress
  rather than claiming a number it can't know.
- **No authentication at all.** Loopback is the entire access control,
  which is why the default matters and why `--host` warns.

## How to undo it

Delete `grokklings/dashboard.py`, the `dashboard` command and
`tests/test_dashboard.py`. Nothing imports it and it writes nothing, so
there is no state to unwind.
