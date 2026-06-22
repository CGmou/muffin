"""Thin SQLite data layer. We use the stdlib sqlite3 directly (no ORM) to keep
dependencies minimal and behaviour predictable. All rows are returned as plain
dicts. JSON columns (capabilities, extra) are encoded/decoded transparently."""

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from .. import config, schedule as schedule_mod

# One connection guarded by a lock. The manager is I/O light (a small studio /
# LAN farm), so a single serialized connection is plenty and avoids "database is
# locked" surprises that come with multi-connection SQLite.
_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    dcc         TEXT NOT NULL,
    renderer    TEXT DEFAULT '',
    scene_path  TEXT NOT NULL,
    output_path TEXT DEFAULT '',
    frame_start INTEGER NOT NULL,
    frame_end   INTEGER NOT NULL,
    frame_step  INTEGER NOT NULL DEFAULT 1,
    chunk_size  INTEGER NOT NULL DEFAULT 1,
    priority    INTEGER NOT NULL DEFAULT 50,
    status      TEXT NOT NULL DEFAULT 'queued',
    extra       TEXT NOT NULL DEFAULT '{}',
    submitter   TEXT NOT NULL DEFAULT '',
    batch       TEXT NOT NULL DEFAULT '',
    pool        TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    finished_at REAL
);

CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    job_id      TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    frame_start INTEGER NOT NULL,
    frame_end   INTEGER NOT NULL,
    status      TEXT NOT NULL DEFAULT 'queued',
    worker_id   TEXT,
    progress    REAL NOT NULL DEFAULT 0,
    attempts    INTEGER NOT NULL DEFAULT 0,
    log         TEXT NOT NULL DEFAULT '',
    started_at  REAL,
    finished_at REAL
);

CREATE TABLE IF NOT EXISTS workers (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    host            TEXT DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'idle',
    capabilities    TEXT NOT NULL DEFAULT '[]',
    pool            TEXT NOT NULL DEFAULT '',
    pools           TEXT NOT NULL DEFAULT '[]',
    current_task_id TEXT,
    last_job_name   TEXT NOT NULL DEFAULT '',
    last_task_id    TEXT NOT NULL DEFAULT '',
    cpu             TEXT NOT NULL DEFAULT '',
    gpu             TEXT NOT NULL DEFAULT '',
    ram             TEXT NOT NULL DEFAULT '',
    last_seen       REAL NOT NULL,
    registered_at   REAL NOT NULL,
    enabled         INTEGER NOT NULL DEFAULT 1,
    tz_offset        INTEGER,
    schedule_enabled INTEGER NOT NULL DEFAULT 0,
    schedule         TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS pools (
    name       TEXT PRIMARY KEY,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_job ON tasks(job_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
"""


def init() -> None:
    global _conn
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(str(config.DB_PATH), check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA foreign_keys = ON")
    _conn.executescript(SCHEMA)
    _migrate()
    _conn.commit()


def _migrate() -> None:
    """Add columns to pre-existing databases that predate a feature."""
    cols = [r[1] for r in _conn.execute("PRAGMA table_info(workers)").fetchall()]
    if "pool" not in cols:
        _conn.execute("ALTER TABLE workers ADD COLUMN pool TEXT NOT NULL DEFAULT ''")
    if "last_job_name" not in cols:
        _conn.execute("ALTER TABLE workers ADD COLUMN last_job_name TEXT NOT NULL DEFAULT ''")
        _conn.execute("ALTER TABLE workers ADD COLUMN last_task_id TEXT NOT NULL DEFAULT ''")
    if "cpu" not in cols:
        _conn.execute("ALTER TABLE workers ADD COLUMN cpu TEXT NOT NULL DEFAULT ''")
        _conn.execute("ALTER TABLE workers ADD COLUMN gpu TEXT NOT NULL DEFAULT ''")
        _conn.execute("ALTER TABLE workers ADD COLUMN ram TEXT NOT NULL DEFAULT ''")
    if "pools" not in cols:
        # Workers may now belong to SEVERAL pools (JSON list). Carry over the
        # old single-pool value.
        _conn.execute("ALTER TABLE workers ADD COLUMN pools TEXT NOT NULL DEFAULT '[]'")
        for row in _conn.execute("SELECT id, pool FROM workers WHERE pool != ''").fetchall():
            _conn.execute("UPDATE workers SET pools=? WHERE id=?",
                          (json.dumps([row["pool"]]), row["id"]))
    if "schedule" not in cols:
        # Per-worker render schedule (see muffin/schedule.py). tz_offset lets the
        # manager evaluate the schedule in the worker's own local time.
        _conn.execute("ALTER TABLE workers ADD COLUMN tz_offset INTEGER")
        _conn.execute("ALTER TABLE workers ADD COLUMN schedule_enabled INTEGER NOT NULL DEFAULT 0")
        _conn.execute("ALTER TABLE workers ADD COLUMN schedule TEXT NOT NULL DEFAULT ''")
    jcols = [r[1] for r in _conn.execute("PRAGMA table_info(jobs)").fetchall()]
    if "submitter" not in jcols:
        _conn.execute("ALTER TABLE jobs ADD COLUMN submitter TEXT NOT NULL DEFAULT ''")
    if "finished_at" not in jcols:
        _conn.execute("ALTER TABLE jobs ADD COLUMN finished_at REAL")
    if "batch" not in jcols:
        _conn.execute("ALTER TABLE jobs ADD COLUMN batch TEXT NOT NULL DEFAULT ''")
    if "pool" not in jcols:
        _conn.execute("ALTER TABLE jobs ADD COLUMN pool TEXT NOT NULL DEFAULT ''")


def _row_to_dict(row: sqlite3.Row, json_fields: tuple[str, ...] = ()) -> dict[str, Any]:
    d = dict(row)
    for f in json_fields:
        if f in d and isinstance(d[f], str):
            d[f] = json.loads(d[f])
    return d


def new_id() -> str:
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------- jobs --------
def create_job(data: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    job_id = new_id()
    with _lock:
        # Suspended submissions land as 'requeued' — visible but not scheduled
        # until the user presses Start.
        status = "requeued" if data.get("suspended") else "queued"
        _conn.execute(
            """INSERT INTO jobs (id, name, dcc, renderer, scene_path, output_path,
                   frame_start, frame_end, frame_step, chunk_size, priority,
                   status, extra, submitter, batch, pool, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                job_id, data["name"], data["dcc"], data.get("renderer", ""),
                data["scene_path"], data.get("output_path", ""),
                data["frame_start"], data["frame_end"], data.get("frame_step", 1),
                data.get("chunk_size", 1), data.get("priority", 50),
                status, json.dumps(data.get("extra", {})),
                data.get("submitter", ""), data.get("batch", ""),
                data.get("pool", ""), now, now,
            ),
        )
        # Build the tasks (chunks of frames).
        _create_tasks_for_job(job_id, data)
        _conn.commit()
    return get_job(job_id)


def _create_tasks_for_job(job_id: str, data: dict[str, Any]) -> None:
    start = data["frame_start"]
    end = data["frame_end"]
    step = max(1, data.get("frame_step", 1))
    chunk = max(1, data.get("chunk_size", 1))
    # kick renders exactly one .ass file per invocation, so a kick job is always
    # one frame per task — never let an edited chunk_size silently drop frames.
    if str(data.get("dcc", "")).lower() == "kick":
        chunk = 1
    frames = list(range(start, end + 1, step))
    for i in range(0, len(frames), chunk):
        block = frames[i:i + chunk]
        _conn.execute(
            """INSERT INTO tasks (id, job_id, frame_start, frame_end, status)
               VALUES (?,?,?,?,'queued')""",
            (new_id(), job_id, block[0], block[-1]),
        )


def get_job(job_id: str) -> Optional[dict[str, Any]]:
    with _lock:
        row = _conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return _row_to_dict(row, ("extra",)) if row else None


def list_jobs() -> list[dict[str, Any]]:
    # Newest first — the latest submission shows at the top of the Monitor.
    with _lock:
        rows = _conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC"
        ).fetchall()
    jobs = [_row_to_dict(r, ("extra",)) for r in rows]
    for j in jobs:
        j.update(task_stats(j["id"]))
        j["current_tasks"] = _job_task_ids(j["id"])
        j["workers"] = _job_workers(j["id"])
        j["render_start"], j["render_end"] = _job_render_span(j["id"])
    return jobs


def _job_workers(job_id: str) -> str:
    """Names of the workers that rendered (or are rendering) this job's tasks."""
    with _lock:
        rows = _conn.execute(
            """SELECT DISTINCT w.name FROM tasks t
               JOIN workers w ON w.id = t.worker_id
               WHERE t.job_id=? ORDER BY w.name""", (job_id,),
        ).fetchall()
    return ", ".join(r["name"] for r in rows)


def _job_render_span(job_id: str) -> tuple:
    """(first task start, last task end). End is None while anything is
    unfinished — render time is start→end, not submit→end."""
    with _lock:
        row = _conn.execute(
            "SELECT MIN(started_at) s, MAX(finished_at) f FROM tasks WHERE job_id=?",
            (job_id,),
        ).fetchone()
        unfinished = _conn.execute(
            "SELECT COUNT(*) c FROM tasks WHERE job_id=? AND finished_at IS NULL",
            (job_id,),
        ).fetchone()["c"]
    return row["s"], (row["f"] if unfinished == 0 else None)


def _job_task_ids(job_id: str) -> str:
    """IDs of this job's in-flight tasks; once nothing is running, fall back to
    all task IDs so the Monitor keeps them visible for tracking."""
    with _lock:
        rows = _conn.execute(
            """SELECT id FROM tasks
               WHERE job_id=? AND status IN ('assigned','running')
               ORDER BY frame_start""", (job_id,),
        ).fetchall()
        if not rows:
            rows = _conn.execute(
                "SELECT id FROM tasks WHERE job_id=? ORDER BY frame_start", (job_id,),
            ).fetchall()
    return ", ".join(r["id"] for r in rows)


def task_stats(job_id: str) -> dict[str, Any]:
    with _lock:
        rows = _conn.execute(
            "SELECT status, COUNT(*) c, AVG(progress) p FROM tasks WHERE job_id=? GROUP BY status",
            (job_id,),
        ).fetchall()
        total = _conn.execute(
            "SELECT COUNT(*) c, AVG(progress) p FROM tasks WHERE job_id=?", (job_id,)
        ).fetchone()
    counts = {r["status"]: r["c"] for r in rows}
    return {
        "task_total": total["c"] or 0,
        "task_done": counts.get("done", 0),
        "task_failed": counts.get("failed", 0),
        "task_running": counts.get("running", 0),
        "progress": round((total["p"] or 0.0), 3),
    }


def update_job(job_id: str, fields: dict[str, Any]) -> None:
    """Update editable job columns (name, priority, frame range, chunk size)."""
    allowed = ("name", "priority", "frame_start", "frame_end", "frame_step", "chunk_size")
    sets, vals = [], []
    for k in allowed:
        if k in fields:
            sets.append(f"{k}=?")
            vals.append(fields[k])
    if not sets:
        return
    vals += [time.time(), job_id]
    with _lock:
        _conn.execute(f"UPDATE jobs SET {', '.join(sets)}, updated_at=? WHERE id=?", vals)
        _conn.commit()


def regenerate_tasks(job_id: str) -> None:
    """Delete and recreate a job's tasks from its (possibly edited) frame range.
    Only safe before the job has started — the caller enforces that."""
    job = get_job(job_id)
    if not job:
        return
    with _lock:
        _conn.execute("DELETE FROM tasks WHERE job_id=?", (job_id,))
        _create_tasks_for_job(job_id, job)
        _conn.execute(
            "UPDATE jobs SET status='queued', updated_at=? WHERE id=?",
            (time.time(), job_id),
        )
        _conn.commit()


def set_job_status(job_id: str, status: str) -> None:
    now = time.time()
    with _lock:
        if status == "done":
            _conn.execute(
                "UPDATE jobs SET status=?, updated_at=?, finished_at=? WHERE id=?",
                (status, now, now, job_id),
            )
        else:
            # Leaving 'done' (requeue/resume) clears the finish stamp.
            _conn.execute(
                "UPDATE jobs SET status=?, updated_at=?, finished_at=NULL WHERE id=?",
                (status, now, job_id),
            )
        _conn.commit()


def delete_job(job_id: str) -> None:
    with _lock:
        _conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        _conn.commit()


def requeue_job(job_id: str) -> None:
    """Reset every task back to queued, but park the job as 'requeued' — it
    won't be scheduled until the user explicitly starts it."""
    with _lock:
        _conn.execute(
            """UPDATE tasks SET status='queued', worker_id=NULL, progress=0,
                   started_at=NULL, finished_at=NULL WHERE job_id=?""",
            (job_id,),
        )
        _conn.execute(
            "UPDATE jobs SET status='requeued', updated_at=?, finished_at=NULL WHERE id=?",
            (time.time(), job_id),
        )
        _conn.commit()


# --------------------------------------------------------------- tasks --------
def list_tasks(job_id: str) -> list[dict[str, Any]]:
    with _lock:
        rows = _conn.execute(
            "SELECT * FROM tasks WHERE job_id=? ORDER BY frame_start ASC", (job_id,)
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_task(task_id: str) -> Optional[dict[str, Any]]:
    with _lock:
        row = _conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    return _row_to_dict(row) if row else None


def update_task_progress(task_id: str, progress: float, log_chunk: str) -> None:
    with _lock:
        if log_chunk:
            _conn.execute(
                "UPDATE tasks SET progress=?, log=substr(log || ?, -20000) WHERE id=?",
                (progress, log_chunk, task_id),
            )
        else:
            _conn.execute(
                "UPDATE tasks SET progress=? WHERE id=?", (progress, task_id)
            )
        # A progress report proves the worker is alive — keep it from being
        # marked offline mid-render.
        _conn.execute(
            "UPDATE workers SET last_seen=? WHERE current_task_id=?",
            (time.time(), task_id),
        )
        _conn.commit()


def finish_task(task_id: str, status: str, log_chunk: str = "") -> None:
    with _lock:
        prog = 1.0 if status == "done" else None
        _conn.execute(
            """UPDATE tasks
               SET status=?, finished_at=?,
                   progress=COALESCE(?, progress),
                   log=substr(log || ?, -20000)
               WHERE id=?""",
            (status, time.time(), prog, log_chunk, task_id),
        )
        _conn.commit()


# ------------------------------------------------------------- workers --------
def upsert_worker(data: dict[str, Any]) -> dict[str, Any]:
    """Register a worker (or update if a worker with the same name re-registers)."""
    now = time.time()
    with _lock:
        existing = _conn.execute(
            "SELECT id FROM workers WHERE name=?", (data["name"],)
        ).fetchone()
        if existing:
            wid = existing["id"]
            _conn.execute(
                """UPDATE workers SET host=?, capabilities=?, status='idle',
                       cpu=?, gpu=?, ram=?, tz_offset=?, last_seen=? WHERE id=?""",
                (data.get("host", ""), json.dumps(data.get("capabilities", [])),
                 data.get("cpu", ""), data.get("gpu", ""), data.get("ram", ""),
                 data.get("tz_offset"), now, wid),
            )
        else:
            wid = new_id()
            _conn.execute(
                """INSERT INTO workers (id, name, host, status, capabilities,
                       cpu, gpu, ram, tz_offset, last_seen, registered_at)
                   VALUES (?,?,?,'idle',?,?,?,?,?,?,?)""",
                (wid, data["name"], data.get("host", ""),
                 json.dumps(data.get("capabilities", [])),
                 data.get("cpu", ""), data.get("gpu", ""), data.get("ram", ""),
                 data.get("tz_offset"), now, now),
            )
        _conn.commit()
    return get_worker(wid)


def _worker_dict(row) -> dict[str, Any]:
    d = _row_to_dict(row, ("capabilities", "pools"))
    # "pool" stays as the display string; "pools" is the real membership list.
    d["pool"] = ", ".join(d.get("pools") or [])
    # Decode the render schedule (stored as a JSON list of 7 hour-bitmasks) and
    # surface live derived fields the UIs need: a normalized grid, the enabled
    # flag as a bool, and whether the worker is right now OUTSIDE its window.
    raw = d.get("schedule")
    grid = None
    if isinstance(raw, str) and raw.strip():
        try:
            grid = json.loads(raw)
        except json.JSONDecodeError:
            grid = None
    d["schedule"] = schedule_mod.normalize(grid) if grid is not None else None
    d["schedule_enabled"] = bool(d.get("schedule_enabled"))
    d["standby"] = schedule_mod.worker_standby(d)
    return d


def get_worker(worker_id: str) -> Optional[dict[str, Any]]:
    with _lock:
        row = _conn.execute("SELECT * FROM workers WHERE id=?", (worker_id,)).fetchone()
    return _worker_dict(row) if row else None


def list_workers() -> list[dict[str, Any]]:
    _expire_stale_workers()
    with _lock:
        rows = _conn.execute(
            """SELECT w.*, j.name AS current_job_name
               FROM workers w
               LEFT JOIN tasks t ON t.id = w.current_task_id
               LEFT JOIN jobs j ON j.id = t.job_id
               ORDER BY w.name""").fetchall()
    return [_worker_dict(r) for r in rows]


def heartbeat_worker(worker_id: str, status: str, current_task_id: Optional[str],
                     tz_offset: Optional[int] = None) -> None:
    with _lock:
        if tz_offset is None:
            _conn.execute(
                "UPDATE workers SET last_seen=?, status=?, current_task_id=? WHERE id=?",
                (time.time(), status, current_task_id, worker_id),
            )
        else:
            # Refresh the worker's UTC offset on every beat so a DST change is
            # picked up without waiting for a re-registration.
            _conn.execute(
                "UPDATE workers SET last_seen=?, status=?, current_task_id=?, tz_offset=? WHERE id=?",
                (time.time(), status, current_task_id, tz_offset, worker_id),
            )
        _conn.commit()


def update_worker(worker_id: str, fields: dict[str, Any]) -> None:
    sets, vals = [], []
    if "name" in fields:
        sets.append("name=?")
        vals.append(fields["name"])
    if "capabilities" in fields:
        sets.append("capabilities=?")
        vals.append(json.dumps([c.lower() for c in fields["capabilities"]]))
    if "pool" in fields:
        sets.append("pool=?")
        vals.append(fields["pool"])
    if "schedule_enabled" in fields:
        sets.append("schedule_enabled=?")
        vals.append(1 if fields["schedule_enabled"] else 0)
    if "schedule" in fields:
        # Stored as a compact JSON list of 7 hour-bitmasks (see muffin/schedule).
        grid = schedule_mod.normalize(fields["schedule"])
        sets.append("schedule=?")
        vals.append(json.dumps(grid))
    if not sets:
        return
    vals.append(worker_id)
    with _lock:
        _conn.execute(f"UPDATE workers SET {', '.join(sets)} WHERE id=?", vals)
        _conn.commit()


def delete_worker(worker_id: str) -> None:
    """Remove a worker; requeue any task it was holding."""
    with _lock:
        row = _conn.execute(
            "SELECT current_task_id FROM workers WHERE id=?", (worker_id,)
        ).fetchone()
        if row and row["current_task_id"]:
            _conn.execute(
                """UPDATE tasks SET status='queued', worker_id=NULL, progress=0
                   WHERE id=? AND status IN ('assigned','running')""",
                (row["current_task_id"],),
            )
        _conn.execute("DELETE FROM workers WHERE id=?", (worker_id,))
        _conn.commit()


def set_worker_last(worker_id: str, job_name: str, task_id: str) -> None:
    """Remember the most recent job/task a worker finished (for the UIs)."""
    with _lock:
        _conn.execute(
            "UPDATE workers SET last_job_name=?, last_task_id=? WHERE id=?",
            (job_name, task_id, worker_id),
        )
        _conn.commit()


def set_worker_enabled(worker_id: str, enabled: bool) -> None:
    with _lock:
        _conn.execute(
            "UPDATE workers SET enabled=? WHERE id=?", (1 if enabled else 0, worker_id)
        )
        _conn.commit()


def _expire_stale_workers() -> None:
    """Mark workers offline if they've stopped sending heartbeats, and release
    any task they were holding so it can be rescheduled."""
    cutoff = time.time() - config.WORKER_TIMEOUT
    with _lock:
        stale = _conn.execute(
            "SELECT id, current_task_id FROM workers WHERE last_seen < ? AND status != 'offline'",
            (cutoff,),
        ).fetchall()
        for w in stale:
            _conn.execute(
                "UPDATE workers SET status='offline', current_task_id=NULL WHERE id=?",
                (w["id"],),
            )
            if w["current_task_id"]:
                _conn.execute(
                    """UPDATE tasks SET status='queued', worker_id=NULL, progress=0
                       WHERE id=? AND status IN ('assigned','running')""",
                    (w["current_task_id"],),
                )
        if stale:
            _conn.commit()


# --------------------------------------------------------------- pools --------
def list_pools() -> list[str]:
    with _lock:
        rows = _conn.execute("SELECT name FROM pools ORDER BY name").fetchall()
    return [r["name"] for r in rows]


def create_pool(name: str) -> None:
    with _lock:
        _conn.execute(
            "INSERT OR IGNORE INTO pools (name, created_at) VALUES (?,?)",
            (name, time.time()),
        )
        _conn.commit()


def delete_pool(name: str) -> None:
    """Delete a pool and remove it from every worker's membership list."""
    with _lock:
        for row in _conn.execute("SELECT id, pools FROM workers").fetchall():
            pools = json.loads(row["pools"] or "[]")
            if name in pools:
                pools.remove(name)
                _conn.execute("UPDATE workers SET pools=? WHERE id=?",
                              (json.dumps(pools), row["id"]))
        _conn.execute("DELETE FROM pools WHERE name=?", (name,))
        _conn.commit()


def set_pool_members(name: str, worker_ids: list[str]) -> None:
    """Make exactly `worker_ids` the members of pool `name`. Workers can belong
    to several pools — only this pool's membership is touched."""
    with _lock:
        _conn.execute(
            "INSERT OR IGNORE INTO pools (name, created_at) VALUES (?,?)",
            (name, time.time()),
        )
        for row in _conn.execute("SELECT id, pools FROM workers").fetchall():
            pools = json.loads(row["pools"] or "[]")
            should = row["id"] in worker_ids
            if should and name not in pools:
                pools.append(name)
            elif not should and name in pools:
                pools.remove(name)
            else:
                continue
            _conn.execute("UPDATE workers SET pools=? WHERE id=?",
                          (json.dumps(sorted(pools)), row["id"]))
        _conn.commit()
