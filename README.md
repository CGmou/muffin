# 🧁 Muffin — a small, friendly render farm

Muffin is a lightweight, **Python-based** render farm you can set up in minutes. A central
**manager** hands render jobs to any number of **workers**, and three small desktop apps
(**Node**, **Worker**, **Monitor**) start the farm and watch it. Artists submit straight
from **Blender** or **Maya** — including an Arnold **.ass + kick** path that renders on the
farm without holding a Maya licence.

> Built for small studios, classrooms and home setups — a few machines on a LAN, or a
> manager parked on a NAS with workers wherever you have spare cores.

```
      Muffin Node ─┐                                        ┌─ Muffin Worker  (blender)
   (runs manager,  │      ┌──────────────┐   REST/HTTP      ├─ Muffin Worker  (maya)
    settings)      ├─────▶│   MANAGER     │◀────────────────┤  ...
      Muffin       │      │  FastAPI +    │                 └─ Muffin Worker  (...)
      Monitor  ────┘      │   SQLite      │◀── Blender / Maya add-on / CLI (submit jobs)
   (jobs+workers)         └──────────────┘
```

**Python 3.10+ · Manager: FastAPI + SQLite · Apps: PySide6 · Windows-first (also runs on macOS/Linux).**

## What's in the box
- **Manager** — one FastAPI process over a single SQLite file. No database server, no
  message broker, no cloud account.
- **Three desktop apps** — *Node* (runs the manager + settings), *Worker* (renders, with
  live machine stats), *Monitor* (live jobs / tasks / workers with progress bars and
  desktop notifications when a render finishes or fails).
- **In-app submitters** — a Blender add-on and a Maya shelf button send the open scene to
  the farm. (Houdini renders today via the CLI/API; an in-app Houdini submitter is in
  development.)
- **Farm features** — frame chunking, named priorities (Low → Urgent), pools, automatic
  retries, dead-worker detection (a crashed worker's task is requeued), and per-worker
  **render schedules** so a machine only renders out of office hours (see below).

## Install
```powershell
pip install -r requirements.txt          # core (manager + worker + CLI)
pip install -r requirements-gui.txt      # the desktop apps (PySide6 + psutil)
```
On Windows, double-click the batch files (they use `pythonw`, so no console window):
`run_gui.bat` (Node), `run_worker.bat` (Worker), `run_monitor.bat` (Monitor).

## Quick start
1. **Start the manager** — open **Muffin Node** (`run_gui.bat`); it auto-starts the manager.
   (Headless alternative: `python -m muffin.manager`.)
2. **Set DCC paths once** — Node ▸ **Settings** ▸ **Auto-detect** scans the standard install
   folders and fills in Blender / Maya / Houdini (and Arnold `kick.exe` + its folders),
   asking which version when several are installed. Saved to `~/.muffin/settings.json`,
   which workers read automatically. (Browse for anything it can't find.)
3. **Start a worker** — open **Muffin Worker** (`run_worker.bat`); it auto-starts rendering.
4. **Submit a job** — from the Blender/Maya submitter, or the CLI:
   ```powershell
   python -m muffin.client submit --name shotA --dcc blender --renderer CYCLES `
       --scene //server/proj/shotA.blend --output //server/proj/out/sh_#### `
       --start 1 --end 100 --chunk 5
   ```
5. **Watch it** in **Muffin Monitor** (`run_monitor.bat`).

> Verified end-to-end against **Blender 5.1** on Windows 11.

## Submitting from your DCC
- **Blender** — install `integrations/blender/muffin_blender_submit.py` (Edit ▸ Preferences
  ▸ Add-ons ▸ Install…), enable it, set the **Manager URL**, then submit from
  **Properties ▸ Output ▸ Muffin Farm**.
- **Maya** — load `integrations/maya/muffin_maya_submit.py` (or use the installer below) and
  click the **Muffin** shelf button. With multiple render layers, each layer is submitted
  as its own job so you can watch them finish independently. Pick **Render with ▸ Arnold
  .ass + kick** to export the scene to `.ass` and render it on the farm with `kick.exe`
  (no Maya licence on the workers — see [Arnold .ass / kick](#arnold-ass--kick-rendering)).
- **CLI** — `python -m muffin.client submit … | jobs | workers | pause|resume|cancel|retry <id>`.

**One-step install on a new machine:** `python integrations/install_addons.py` (or
double-click `install_addons.bat`) installs the Blender add-on and the Maya shelf button in
one go.

## Manager on a NAS / server
The manager runs great as a Docker container on a Synology or any always-on box — see
[deploy/README.md](deploy/README.md). Then every worker, Monitor and submitter just points
at `http://<nas-ip>:8080`. A worker on another machine reads its settings from Node, or from
env vars:
```powershell
$env:MUFFIN_MANAGER_URL = "http://192.168.1.10:8080"
$env:MUFFIN_BLENDER_EXE = "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"
python -m muffin.worker
```

## Worker schedules (render only out of office hours)
An artist's PC can double as a render node **after hours** without ever stealing the
machine during the day. Each worker can carry a weekly schedule of *render-allowed*
hours; outside them the worker is on **standby** — it isn't given new frames, and any
render already running is stopped and requeued so the PC is free the moment work starts.

**Set the whole farm at once — Muffin's Monitor ▸ Schedule ▸ Worker Schedules…:**
1. **Tick the workers** to schedule (all are ticked by default; **All** / **None** buttons help).
2. Set the week once, to the **minute**, with the editor:
   - tick **Enable schedule**, then pick a preset — **Nights & weekends** (renders
     18:00–09:00 on weekdays + all weekend), **Render 24/7**, or **Off (never)**; or
   - **Quick set** — type a window like *18:30 → 09:15*, tick the days, **Apply**; or
   - set any day to **Off / All day / Window** (a window that ends earlier than it
     starts is shown as **(next day)**, e.g. 18:00 → 09:00).
   A plain-English line ("Mon–Fri 18:00–09:00 next day · Sat–Sun all day") previews it.
3. **Save** — the schedule is applied to every ticked worker.

**Artists can set their own machine** without opening the Monitor: in the **Muffin
(Worker) app ▸ Settings ▸ Render schedule…**. It edits the same schedule on the manager,
found by this machine's name, so the Monitor stays in sync.

A parked node shows as **scheduled off** in the Workers list. A frame stopped for the
schedule is **requeued without a failure** — a night-shift machine simply picks it up
later.

> **Timezones just work.** Each worker reports its own UTC offset, so "09:00" means
> 9am *where that machine sits* — even when the manager runs on a NAS in another
> timezone. No clock config needed.

## Configuration
Everything is overridable via environment variables (most are also editable in the GUIs):

| var | default | meaning |
|-----|---------|---------|
| `MUFFIN_HOST` / `MUFFIN_PORT` | `0.0.0.0` / `8080` | manager bind address |
| `MUFFIN_DATA_DIR` | `~/.muffin` | folder for the db + `settings.json` |
| `MUFFIN_MANAGER_URL` | `http://127.0.0.1:8080` | where a worker finds the manager |
| `MUFFIN_CAPABILITIES` | (accept all) | comma list of DCCs a worker accepts |
| `MUFFIN_<DCC>_EXE` | (on PATH) | full path to a DCC exe, e.g. `MUFFIN_BLENDER_EXE` |
| `MUFFIN_WORKER_NAME` | hostname | worker display name |

See [`muffin/config.py`](muffin/config.py) for the rest (heartbeat, timeouts, retries).

## DCC support
Each DCC is a small plugin in [`muffin/dccs/`](muffin/dccs/) that turns a task into a
command line. Pipelines differ, so every plugin accepts an `extra` JSON blob on the job
(`extra.args` is appended verbatim) — tune them to taste.

| DCC | renders with | in-app submitter |
|-----|-------------|------------------|
| Blender | `blender` | ✅ add-on |
| Maya | `Render` | ✅ shelf tool |
| Arnold (kick) | `kick` (`.ass`) | ✅ via the Maya submitter |
| Houdini | `husk` / `hython` | 🚧 in development (renders via CLI/API) |

### Arnold .ass / kick rendering
The Maya submitter's **Render with ▸ Arnold .ass + kick** mode exports the scene to one
`.ass` per frame (next to the scene, in an `ass/<job>/` folder — so **the scene must be on a
share the workers can read**) and submits a `kick` job; each frame is rendered by Arnold's
standalone `kick.exe`, so the workers don't need a Maya licence. With *All render layers*,
each layer is exported and submitted as its own job (named per layer). On each worker, point
Node ▸ **Settings** at its **kick.exe**, **Maya bin**, **XGen** and **procedurals** folders
(Auto-detect fills these in); Muffin runs the equivalent of:
```bat
set PATH=<Maya bin>;<XGen>;%PATH%
kick.exe -i scene.0001.ass -dp -dw -nokeypress -l "<procedurals>"
```

## Troubleshooting
- **Monitor says "offline"** — start the manager in Node, check the **Manager URL**, and
  make sure port `8080` is reachable between machines.
- **Worker never renders** — check its capabilities / any **pool** restriction on the job,
  and that the DCC executable path is set (Node ▸ Settings).
- **No GPU stats in the Worker** — install `psutil`, and for GPU make sure NVIDIA's
  `nvidia-smi` is on `PATH`.

## Contributing
Issues and PRs welcome! Muffin is intentionally small and readable — each piece (manager,
worker, a DCC plugin, an app) is a short, self-contained file. A great first contribution is
a new DCC plugin in [`muffin/dccs/`](muffin/dccs/) or an in-app submitter in
[`integrations/`](integrations/).

## License
[GNU General Public License v2.0 or later](LICENSE) — the same license as Blender. You're
free to use, study, share and modify Muffin; if you distribute it or a derivative, that work
must also be GPL and ship its source. Copyright © 2026 Muffin contributors.
