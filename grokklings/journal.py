"""The journal and its storage: full history + a durable queue.

Principle from the doc: a task never disappears. So the queue doesn't live in
process memory — it lives in the same database as the journal: restarting the
colony loses no tasks, and a crashed instance hands its task back once the
lease expires.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any, Iterator

from .model import Task, TaskStatus, now

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id           TEXT PRIMARY KEY,
    source       TEXT NOT NULL,
    payload      TEXT NOT NULL,
    dedup_key    TEXT,
    status       TEXT NOT NULL,
    slot         TEXT,
    route        TEXT NOT NULL DEFAULT '[]',
    result       TEXT NOT NULL DEFAULT '[]',
    attempts     INTEGER NOT NULL DEFAULT 0,
    hops         INTEGER NOT NULL DEFAULT 0,
    visible_at   REAL NOT NULL DEFAULT 0,
    lease_until  REAL,
    duplicate_of TEXT,
    last_error   TEXT,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS tasks_queue ON tasks (status, visible_at);
CREATE INDEX IF NOT EXISTS tasks_lease ON tasks (status, lease_until);

-- Deduplication: the user supplies the key (a contract address, a package
-- name, a url). Whoever inserts it first owns the key; everyone after is a
-- duplicate.
CREATE TABLE IF NOT EXISTS dedup (
    key        TEXT PRIMARY KEY,
    task_id    TEXT NOT NULL,
    first_seen REAL NOT NULL
);

-- Completed worker steps. The worker itself picks the key and keeps it the
-- same across attempts, so the record survives a retry: what's already done
-- doesn't get done twice.
CREATE TABLE IF NOT EXISTS steps (
    task_id TEXT NOT NULL,
    slot    TEXT NOT NULL,
    key     TEXT NOT NULL,
    result  TEXT NOT NULL,
    at      REAL NOT NULL,
    PRIMARY KEY (task_id, slot, key)
);

-- The journal: append-only, nothing ever gets rewritten.
CREATE TABLE IF NOT EXISTS events (
    seq     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    type    TEXT NOT NULL,
    task_id TEXT,
    slot    TEXT,
    data    TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS events_task ON events (task_id, seq);
CREATE INDEX IF NOT EXISTS events_type ON events (type, seq);
"""

_COLUMNS = (
    "id, source, payload, dedup_key, status, slot, route, result, attempts, "
    "hops, duplicate_of, last_error, created_at, updated_at"
)


class Journal:
    """The single point of writing state. Thread-safe: sources live in their
    own threads, the dispatcher lives in the event loop."""

    def __init__(self, path: str = "colony.db") -> None:
        self.path = path
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.executescript(SCHEMA)
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ----------------------------------------------------------------- journal

    def record(self, type: str, task_id: str | None = None, slot: str | None = None, **data: Any) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO events (ts, type, task_id, slot, data) VALUES (?, ?, ?, ?, ?)",
                (now(), type, task_id, slot, json.dumps(data, ensure_ascii=False, default=str)),
            )
            self._db.commit()
            return int(cur.lastrowid)

    def events(self, limit: int = 100, task_id: str | None = None, type: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM events"
        where, args = [], []
        if task_id:
            where.append("task_id = ?")
            args.append(task_id)
        if type:
            where.append("type = ?")
            args.append(type)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY seq DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        out = []
        for r in reversed(rows):
            d = dict(r)
            d["data"] = json.loads(d["data"])
            out.append(d)
        return out

    # ------------------------------------------------------------- deduplication

    def claim_dedup_key(self, key: str, task_id: str) -> str:
        """Return the id that owns this key. Matches task_id — the task is
        new; otherwise it's a duplicate, and the returned id points at the
        first instance."""
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO dedup (key, task_id, first_seen) VALUES (?, ?, ?)",
                (key, task_id, now()),
            )
            self._db.commit()
            row = self._db.execute("SELECT task_id FROM dedup WHERE key = ?", (key,)).fetchone()
        return row["task_id"]

    def scored_ids(self, task_ids: list[str]) -> set[str]:
        """Which of these tasks already carry a score.

        Asked as a question about these particular tasks, not by scanning
        the newest N events and hoping they are all in there. A window
        forgets: once enough newer scores arrive, an old task's score falls
        out of it, the task reads as unscored, and it gets scored again —
        a wasted model call and a duplicate `task_scored` event skewing
        everything built on the numbers. The cost of asking properly is one
        indexed query bounded by the tasks in hand.
        """
        if not task_ids:
            return set()
        found: set[str] = set()
        with self._lock:
            for chunk in range(0, len(task_ids), 500):  # SQLite's parameter ceiling
                batch = task_ids[chunk:chunk + 500]
                rows = self._db.execute(
                    "SELECT DISTINCT task_id FROM events WHERE type = 'task_scored' "
                    f"AND task_id IN ({','.join('?' * len(batch))})",
                    batch,
                ).fetchall()
                found.update(r["task_id"] for r in rows)
        return found

    # ------------------------------------------------------------------- steps

    MISSING = object()

    def step_result(self, task_id: str, slot: str, key: str) -> Any:
        """The result of an already-completed step, or Journal.MISSING.

        Telling "the step returned None" apart from "the step never ran"
        can't be done by value alone, so the signal is whether a row exists.
        """
        with self._lock:
            row = self._db.execute(
                "SELECT result FROM steps WHERE task_id = ? AND slot = ? AND key = ?",
                (task_id, slot, key),
            ).fetchone()
        return Journal.MISSING if row is None else json.loads(row["result"])

    def record_step(self, task_id: str, slot: str, key: str, result: Any) -> None:
        """Mark a step as done. An incomplete step is never recorded — which
        is exactly why a step that failed honestly gets retried."""
        try:
            blob = json.dumps(result, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"the result of step {key!r} doesn't serialize to JSON: {exc}. "
                "A step must return something that survives a restart."
            ) from exc
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO steps (task_id, slot, key, result, at) VALUES (?, ?, ?, ?, ?)",
                (task_id, slot, key, blob, now()),
            )
            self._db.commit()

    def clear_steps(self, task_id: str) -> int:
        """The task reached its end — there's nothing left to replay."""
        with self._lock:
            cur = self._db.execute("DELETE FROM steps WHERE task_id = ?", (task_id,))
            self._db.commit()
        return cur.rowcount

    # ------------------------------------------------------------------- queue

    def enqueue(self, task: Task, delay: float = 0.0) -> None:
        row = task.to_row()
        row["visible_at"] = now() + delay
        row["lease_until"] = None
        with self._lock:
            self._db.execute(
                """INSERT INTO tasks
                   (id, source, payload, dedup_key, status, slot, route, result, attempts,
                    hops, visible_at, lease_until, duplicate_of, last_error, created_at, updated_at)
                   VALUES (:id, :source, :payload, :dedup_key, :status, :slot, :route, :result,
                           :attempts, :hops, :visible_at, :lease_until, :duplicate_of, :last_error,
                           :created_at, :updated_at)""",
                row,
            )
            self._db.commit()

    def peek(self, limit: int = 32) -> list[Task]:
        """Tasks ready to be handed out. This doesn't claim them — claim() does."""
        with self._lock:
            rows = self._db.execute(
                f"SELECT {_COLUMNS} FROM tasks WHERE status = ? AND visible_at <= ? "
                "ORDER BY visible_at, created_at LIMIT ?",
                (TaskStatus.QUEUED.value, now(), limit),
            ).fetchall()
        return [Task.from_row(r) for r in rows]

    def claim(self, task: Task, lease: float) -> bool:
        """Atomically move the task into progress. False means someone beat us to it."""
        with self._lock:
            cur = self._db.execute(
                "UPDATE tasks SET status = ?, slot = ?, attempts = ?, hops = ?, route = ?, "
                "lease_until = ?, updated_at = ? WHERE id = ? AND status = ?",
                (
                    TaskStatus.IN_PROGRESS.value,
                    task.slot,
                    task.attempts,
                    task.hops,
                    json.dumps(task.route, ensure_ascii=False),
                    now() + lease,
                    now(),
                    task.id,
                    TaskStatus.QUEUED.value,
                ),
            )
            self._db.commit()
        if cur.rowcount == 1:
            task.status = TaskStatus.IN_PROGRESS
            return True
        return False

    def save(self, task: Task, visible_at: float | None = None, lease_until: float | None = None) -> None:
        row = task.to_row()
        row["visible_at"] = visible_at if visible_at is not None else now()
        row["lease_until"] = lease_until
        with self._lock:
            self._db.execute(
                """UPDATE tasks SET source=:source, payload=:payload, dedup_key=:dedup_key,
                   status=:status, slot=:slot, route=:route, result=:result, attempts=:attempts,
                   hops=:hops, visible_at=:visible_at, lease_until=:lease_until,
                   duplicate_of=:duplicate_of, last_error=:last_error, updated_at=:updated_at
                   WHERE id=:id""",
                row,
            )
            self._db.commit()

    def requeue(self, task: Task, delay: float = 0.0) -> None:
        task.status = TaskStatus.QUEUED
        task.updated_at = now()
        self.save(task, visible_at=now() + delay, lease_until=None)

    def finish(self, task: Task, status: TaskStatus) -> None:
        task.status = status
        task.updated_at = now()
        self.save(task, visible_at=now(), lease_until=None)
        self.clear_steps(task.id)

    def get(self, task_id: str) -> Task | None:
        with self._lock:
            row = self._db.execute(
                f"SELECT {_COLUMNS} FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
        return Task.from_row(row) if row else None

    def recover_expired(self) -> list[Task]:
        """An instance crashed or the process restarted — the task goes back
        to the queue and will be picked up by someone else."""
        with self._lock:
            rows = self._db.execute(
                f"SELECT {_COLUMNS} FROM tasks WHERE status = ? AND (lease_until IS NULL OR lease_until <= ?)",
                (TaskStatus.IN_PROGRESS.value, now()),
            ).fetchall()
        recovered = []
        for r in rows:
            task = Task.from_row(r)
            # The route is written on assignment; the same slot must get the
            # task back — this is an explicit retry, not a second visit.
            if task.route and task.route[-1] == task.slot:
                task.route.pop()
            self.requeue(task)
            self.record("task_recovered", task.id, task.slot, reason="lease expired")
            recovered.append(task)
        return recovered

    # ------------------------------------------------------------- live state

    def status_counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._db.execute("SELECT status, COUNT(*) c FROM tasks GROUP BY status").fetchall()
        return {r["status"]: r["c"] for r in rows}

    def queue_depth(self) -> dict[str, int]:
        with self._lock:
            rows = self._db.execute(
                "SELECT COALESCE(slot, '(unassigned)') s, COUNT(*) c FROM tasks "
                "WHERE status = ? GROUP BY s",
                (TaskStatus.QUEUED.value,),
            ).fetchall()
        return {r["s"]: r["c"] for r in rows}

    def in_progress(self) -> list[Task]:
        with self._lock:
            rows = self._db.execute(
                f"SELECT {_COLUMNS} FROM tasks WHERE status = ? ORDER BY updated_at",
                (TaskStatus.IN_PROGRESS.value,),
            ).fetchall()
        return [Task.from_row(r) for r in rows]

    def by_status(self, status: TaskStatus, limit: int = 50) -> list[Task]:
        with self._lock:
            rows = self._db.execute(
                f"SELECT {_COLUMNS} FROM tasks WHERE status = ? ORDER BY updated_at DESC LIMIT ?",
                (status.value, limit),
            ).fetchall()
        return [Task.from_row(r) for r in rows]

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self.events(limit=10_000))
