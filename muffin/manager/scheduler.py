"""Scheduling logic: pick the next task for a worker, and reconcile job status as
tasks complete or fail. Kept deliberately simple and synchronous — all writes go
through the locked db layer."""

import time
from typing import Any, Optional

from .. import config
from . import db


def assign_task(worker: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Find the highest-priority queued task this worker is able to run and
    atomically assign it. Returns the task dict (with job fields merged) or None."""
    caps = [c.lower() for c in worker.get("capabilities", [])]
    with db._lock:
        # Candidate tasks: queued, whose parent job is active and not paused.
        rows = db._conn.execute(
            """SELECT t.*, j.name job_name, j.dcc dcc, j.renderer renderer,
                      j.scene_path scene_path, j.pool pool,
                      j.output_path output_path, j.frame_step frame_step,
                      j.extra extra, j.priority priority, j.created_at job_created
               FROM tasks t JOIN jobs j ON j.id = t.job_id
               WHERE t.status='queued' AND j.status IN ('queued','running')
               ORDER BY j.priority DESC, j.created_at ASC, t.frame_start ASC""",
        ).fetchall()

        chosen = None
        for r in rows:
            dcc = r["dcc"].lower()
            # Empty capability list == accept any DCC.
            if caps and dcc not in caps:
                continue
            # A pooled job only renders on workers that are in that pool
            # (workers may belong to several pools).
            if r["pool"] and r["pool"] not in (worker.get("pools") or []):
                continue
            chosen = r
            break

        if chosen is None:
            return None

        task_id = chosen["id"]
        db._conn.execute(
            "UPDATE tasks SET status='assigned', worker_id=?, started_at=? WHERE id=?",
            (worker["id"], time.time(), task_id),
        )
        db._conn.execute(
            "UPDATE jobs SET status='running', updated_at=? WHERE id=? AND status='queued'",
            (time.time(), chosen["job_id"]),
        )
        db._conn.commit()

    import json
    return {
        "task_id": task_id,
        "job_id": chosen["job_id"],
        "job_name": chosen["job_name"],
        "dcc": chosen["dcc"],
        "renderer": chosen["renderer"],
        "scene_path": chosen["scene_path"],
        "output_path": chosen["output_path"],
        "frame_start": chosen["frame_start"],
        "frame_end": chosen["frame_end"],
        "frame_step": chosen["frame_step"],
        "extra": json.loads(chosen["extra"]) if chosen["extra"] else {},
    }


def mark_task_running(task_id: str) -> None:
    with db._lock:
        db._conn.execute(
            "UPDATE tasks SET status='running' WHERE id=? AND status='assigned'",
            (task_id,),
        )
        db._conn.commit()


def complete_task(task_id: str, status: str, log_chunk: str = "") -> None:
    """Record a task result and recompute the parent job's status."""
    task = db.get_task(task_id)
    if not task:
        return
    job_id = task["job_id"]

    # Remember what this worker just rendered (shown as "Last Job" in the UIs).
    if task.get("worker_id"):
        job = db.get_job(job_id)
        db.set_worker_last(task["worker_id"], job["name"] if job else "", task_id)

    if status == "failed":
        with db._lock:
            attempts = task["attempts"] + 1
            db._conn.execute("UPDATE tasks SET attempts=? WHERE id=?", (attempts, task_id))
            db._conn.commit()
        if attempts < config.MAX_TASK_ATTEMPTS:
            # Retry: drop it back into the queue for another worker.
            with db._lock:
                db._conn.execute(
                    """UPDATE tasks SET status='queued', worker_id=NULL, progress=0,
                           log=substr(log || ?, -20000) WHERE id=?""",
                    (f"\n[muffin] task failed, retrying ({attempts}/{config.MAX_TASK_ATTEMPTS})\n", task_id),
                )
                db._conn.commit()
            return
        db.finish_task(task_id, "failed", log_chunk)
    else:
        db.finish_task(task_id, "done", log_chunk)

    _reconcile_job(job_id)


def _reconcile_job(job_id: str) -> None:
    stats = db.task_stats(job_id)
    total = stats["task_total"]
    done = stats["task_done"]
    failed = stats["task_failed"]
    if total == 0:
        return
    if failed > 0 and (done + failed) >= total:
        db.set_job_status(job_id, "failed")
    elif done >= total:
        db.set_job_status(job_id, "done")
    # otherwise leave as 'running'
