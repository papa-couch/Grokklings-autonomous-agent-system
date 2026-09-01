# 0008. Picking a slot in phase 2 — with a ready-made library

- Status: **superseded** by [0014](0014-grok-router.md)
- Date: 2026-08-27
- Related: [plan.md](../plan.md), [0014](0014-grok-router.md)

> Superseded 2026-08-27. Phase 2's slot-picking was built, but not this
> way: the router asks Grok instead of a local encoder. The reasoning below
> predates [0013](0013-grok-primary-backend.md), which made a model call
> the colony's normal mode rather than its exception — see
> [0014](0014-grok-router.md) for why that flips the trade. Two things from
> this record survived into 0014: making slot-picking an optional extra
> rather than a core dependency (the compromise sketched below), and the
> hit-rate measurement, which still hasn't been run.

## Context

Phase 2 requires: the dispatcher figures out the right slot from its
description and the task's content, when a worker hasn't named a receiver
explicitly. Right now `Dispatcher.route()` returns either the
already-assigned slot or the entry slot.

The category is called semantic routing. Verified against the primary
source: `aurelio-labs/semantic-router`, MIT license, 3.8k stars; a route is
described by a name and example phrases; runs fully locally via a
HuggingFace encoder, no API call needed.

## Proposal

Make the body of `Dispatcher.route()` a call into the ready-made library
instead of our own algorithm. `SlotSpec.description` becomes the source of
examples for a route.

## What it buys

The routing decision gets made by comparing vectors instead of calling a
model: cheaper and faster by an order of magnitude. Phase 2 ends up with
almost no code of our own.

## What it costs

The first external dependency in the core — a direct break from
[0001](0001-python-stdlib.md). A local encoder drags a model onto disk, and
"install it and run it" stops being true.

A compromise is possible: make slot-picking an optional extra, like the LLM
worker. The colony still works without it, but then a worker has to name
its own route.

## How to verify it paid off

Build a set of tasks with a known correct slot and measure the hit rate.
Set the bar before measuring, not after.

## How to undo it

`route()` is one function. Put the old body back.
