# Running the Muffin Manager on a Synology NAS

The manager is a small FastAPI + SQLite service — no GUI, no DCCs — so it's a
perfect always-on service for a NAS. Workers, Monitors and submitters on the
artist machines just point at the NAS's address.

```
   Synology NAS                     Artist / render PCs
  ┌───────────────────┐            ┌──────────────────────┐
  │  muffin-manager   │◀──────────▶│ Muffin (worker GUI)  │
  │  (Docker, :8080)  │            │ Muffin's Monitor     │
  │  /data → volume   │            │ Blender/Maya submit  │
  └───────────────────┘            └──────────────────────┘
```

## Requirements
- A Synology model that supports **Container Manager** (most x86 "+" models;
  on older DSM it's called "Docker").
- The repo copied onto the NAS, e.g. `/volume1/docker/muffin`.

## Setup (Container Manager)
1. **File Station**: copy the whole `muffin` repo to `/volume1/docker/muffin`.
2. **Container Manager ▸ Project ▸ Create**:
   - Project name: `muffin`
   - Path: `/volume1/docker/muffin/deploy`
   - Source: *Use existing docker-compose.yml*
3. Build & start. First build takes a minute (downloads python:3.12-slim).
4. Test from any PC: open `http://<nas-ip>:8080/api/jobs` in a browser —
   you should see `[]`.

The farm database lives in `/volume1/docker/muffin/deploy/data/` on the NAS —
it survives container rebuilds and DSM updates. Back it up like any folder.

## Point the farm at the NAS
On every artist/worker machine, set the Manager URL to `http://<nas-ip>:8080`:
- **Muffin Manager / Worker app** → Settings ▸ Manager URL
- **Muffin's Monitor** → Edit ▸ Super Muffin Mode ▸ Manager URL…
- Blender/Maya submitters pick it up automatically from those settings.

Give the NAS a **static IP** (or DNS name) so the URL never changes.

## Notes & tips
- **Render outputs do NOT go to the manager.** Workers write frames to
  whatever output path the job says — use a network share every worker can
  reach (the NAS itself is ideal: submit with output paths on a NAS share).
- **Updating Muffin**: copy the new repo over, then Container Manager ▸
  Project ▸ Build again. The `data/` volume keeps all jobs/workers.
- **Port**: change the left side of `8080:8080` in the compose file if 8080
  is taken on the NAS.
- The "Open Render Output" action in the Monitor opens paths on *your* machine,
  so it keeps working with a NAS-hosted manager as long as the job's output path
  is reachable from your PC (e.g. a `\\nas\…` share or mapped drive).
- **Worker schedules need no NAS timezone setup.** Each worker reports its own
  UTC offset, so a "render 18:00–09:00" schedule follows the *worker's* local
  clock even though this container may run in UTC.

## Without Docker (not recommended)
DSM ships Python 3; you can run the manager directly via Task Scheduler
(`python3 -m muffin.manager` with `MUFFIN_DATA_DIR` set), but you'll have to
manage dependencies (`pip install fastapi uvicorn`) and restarts yourself —
the container does all of that for you.
