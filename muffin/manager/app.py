"""The manager: a REST API the GUIs (Node / Worker / Monitor) and the workers
talk to. State lives in SQLite.

Run with:  python -m muffin.manager
"""

from fastapi import FastAPI, HTTPException

from ..common import schemas
from ..dccs import catalog
from . import db, scheduler

app = FastAPI(title="Muffin Render Farm", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    db.init()


# --------------------------------------------------------------- jobs ---------
@app.get("/api/jobs")
def get_jobs():
    return db.list_jobs()


@app.post("/api/jobs")
def submit_job(job: schemas.JobSubmit):
    return db.create_job(job.model_dump())


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    job.update(db.task_stats(job_id))
    job["tasks"] = db.list_tasks(job_id)
    return job


@app.put("/api/jobs/{job_id}")
def edit_job(job_id: str, edit: schemas.JobEdit):
    """Edit a job. Name and priority can change any time. Frame range / chunk
    size can only change while the job hasn't started rendering (it rebuilds the
    task list)."""
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")

    fields = {k: v for k, v in edit.model_dump().items() if v is not None}
    db.update_job(job_id, fields)

    frame_keys = {"frame_start", "frame_end", "frame_step", "chunk_size"}
    if frame_keys & fields.keys():
        stats = db.task_stats(job_id)
        if stats["task_done"] or stats["task_running"]:
            raise HTTPException(
                409, "cannot change frames/chunk after the job has started; "
                     "name and priority were still updated")
        db.regenerate_tasks(job_id)
    return db.get_job(job_id)


@app.post("/api/jobs/{job_id}/pause")
def pause_job(job_id: str):
    _require_job(job_id)
    db.set_job_status(job_id, "paused")
    return {"ok": True}


@app.post("/api/jobs/{job_id}/resume")
def resume_job(job_id: str):
    _require_job(job_id)
    db.set_job_status(job_id, "queued")
    return {"ok": True}


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    _require_job(job_id)
    db.set_job_status(job_id, "canceled")
    return {"ok": True}


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: str):
    _require_job(job_id)
    db.requeue_job(job_id)
    return {"ok": True}


@app.post("/api/jobs/{job_id}/start")
def start_job(job_id: str):
    """Release a requeued job to the scheduler. Only requeued jobs can start."""
    job = _require_job(job_id)
    if job["status"] != "requeued":
        raise HTTPException(409, "only requeued jobs can be started")
    db.set_job_status(job_id, "queued")
    return {"ok": True}


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    db.delete_job(job_id)
    return {"ok": True}


@app.post("/api/jobs/{job_id}/reveal")
def reveal_output(job_id: str):
    """Open the job's output folder in the OS file browser on the manager host."""
    import os
    import subprocess
    import sys

    job = _require_job(job_id)
    raw = job.get("output_path") or ""
    folder = ""
    if raw:
        folder = raw if os.path.isdir(raw) else os.path.dirname(raw)
    if not folder or not os.path.isdir(folder):
        # No usable output path (or it doesn't exist on this host) — fall back
        # to the scene file's folder so "Open render folder" still helps.
        scene_dir = os.path.dirname(job.get("scene_path") or "")
        if scene_dir and os.path.isdir(scene_dir):
            folder = scene_dir
    if not folder or not os.path.isdir(folder):
        raise HTTPException(400, "no output or scene folder found on the manager machine")
    try:
        if sys.platform == "win32":
            os.startfile(folder)  # noqa: S606 — local convenience
        elif sys.platform == "darwin":
            subprocess.Popen(["open", folder])
        else:
            subprocess.Popen(["xdg-open", folder])
    except Exception as exc:
        raise HTTPException(500, f"could not open folder: {exc}")
    return {"ok": True, "folder": folder}


def _require_job(job_id: str) -> dict:
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return job


# ------------------------------------------------------------- workers --------
@app.get("/api/workers")
def get_workers():
    return db.list_workers()


@app.post("/api/workers/register")
def register_worker(reg: schemas.WorkerRegister):
    return db.upsert_worker(reg.model_dump())


@app.post("/api/workers/{worker_id}/heartbeat")
def worker_heartbeat(worker_id: str, hb: schemas.WorkerHeartbeat):
    if not db.get_worker(worker_id):
        raise HTTPException(404, "unknown worker; re-register")
    db.heartbeat_worker(worker_id, hb.status, hb.current_task_id)
    return {"ok": True}


@app.post("/api/workers/{worker_id}/request-task")
def request_task(worker_id: str):
    worker = db.get_worker(worker_id)
    if not worker:
        raise HTTPException(404, "unknown worker; re-register")
    if not worker.get("enabled", 1):
        return {"task": None}
    assignment = scheduler.assign_task(worker)
    if assignment:
        db.heartbeat_worker(worker_id, "busy", assignment["task_id"])
    return {"task": assignment}


@app.put("/api/workers/{worker_id}")
def edit_worker(worker_id: str, edit: schemas.WorkerEdit):
    if not db.get_worker(worker_id):
        raise HTTPException(404, "worker not found")
    fields = {k: v for k, v in edit.model_dump().items() if v is not None}
    db.update_worker(worker_id, fields)
    return db.get_worker(worker_id)


@app.delete("/api/workers/{worker_id}")
def delete_worker(worker_id: str):
    """Remove a worker. Any task it was holding is requeued."""
    db.delete_worker(worker_id)
    return {"ok": True}


@app.post("/api/workers/{worker_id}/enable")
def enable_worker(worker_id: str):
    db.set_worker_enabled(worker_id, True)
    return {"ok": True}


@app.post("/api/workers/{worker_id}/disable")
def disable_worker(worker_id: str):
    db.set_worker_enabled(worker_id, False)
    return {"ok": True}


# --------------------------------------------------------------- tasks --------
@app.post("/api/tasks/{task_id}/start")
def task_start(task_id: str):
    scheduler.mark_task_running(task_id)
    return {"ok": True}


@app.post("/api/tasks/{task_id}/progress")
def task_progress(task_id: str, p: schemas.TaskProgress):
    db.update_task_progress(task_id, p.progress, p.log)
    return {"ok": True}


@app.post("/api/tasks/{task_id}/result")
def task_result(task_id: str, r: schemas.TaskResult):
    scheduler.complete_task(task_id, r.status, r.log)
    return {"ok": True}


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str):
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(404, "task not found")
    return task


# --------------------------------------------------------------- pools --------
@app.get("/api/pools")
def get_pools():
    return db.list_pools()


@app.post("/api/pools")
def create_pool(p: schemas.PoolCreate):
    if not p.name.strip():
        raise HTTPException(400, "pool name required")
    db.create_pool(p.name.strip())
    return {"ok": True}


@app.put("/api/pools/{name}")
def set_pool(name: str, members: schemas.PoolMembers):
    db.set_pool_members(name, members.workers)
    return {"ok": True}


@app.delete("/api/pools/{name}")
def delete_pool(name: str):
    db.delete_pool(name)
    return {"ok": True}


# ------------------------------------------------------------- catalog --------
@app.get("/api/dccs")
def get_dccs():
    return catalog()
