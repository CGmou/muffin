# 🧁 Muffin — a small, friendly render farm

Muffin is a lightweight, cross-DCC render farm you can stand up in minutes. A central
**manager** hands render work to any number of **workers**, three small **desktop apps**
(Node, Worker, Monitor) drive and watch it, and in-DCC submitters let artists push the
current scene straight to the farm. It renders with **Blender, Maya, Houdini and Nuke**
using their own renderers — and ships with a built-in **mock** renderer so you can prove
the whole pipeline end-to-end with nothing installed.

> Built for small studios, classrooms and home setups: a few machines on a LAN, or a
> manager parked on a NAS with workers wherever you have spare cores.

```
      Muffin Node ─┐                                        ┌─ Muffin Worker  (blender)
   (runs manager,  │      ┌──────────────┐   REST/HTTP      ├─ Muffin Worker  (maya)
    settings)      ├─────▶│   MANAGER     │◀────────────────┤  ...
      Muffin       │      │  FastAPI +    │                 └─ Muffin Worker  (...)
      Monitor  ────┘      │   SQLite      │◀── Blender / Maya add-on / CLI (submit jobs)
   (jobs+workers)         └──────────────┘
```

**Python 3.10+** · Manager: **FastAPI + SQLite** · Apps: **PySide6** · Windows-first, runs on macOS/Linux too.

---

## Contents
- [Why Muffin](#why-muffin)
- [The pieces](#the-pieces)
- [Install](#install)
- [Quick start](#quick-start)
- [The desktop apps](#the-desktop-apps)
- [Submitting from your DCC](#submitting-from-your-dcc)
- [One-step add-on install](#one-step-add-on-install)
- [Running the manager on a NAS / server](#running-the-manager-on-a-nas--server)
- [Configuration](#configuration-environment-variables)
- [How DCC commands are built](#how-dcc-commands-are-built)
- [CLI](#cli)
- [Project layout](#project-layout)
- [Troubleshooting](#troubleshooting)
- [Roadmap](#roadmap) · [Contributing](#contributing) · [License](#license)

---

## Why Muffin
- **Tiny to run.** The manager is one FastAPI process over a single SQLite file. No
  database server, no message broker, no cloud account.
- **Cross-DCC.** Blender, Maya, Houdini and Nuke plugins out of the box; each is a short
  file in [`muffin/dccs/`](muffin/dccs/) you can tune for your pipeline.
- **Real farm features.** Frame **chunking**, **priorities**, **pools**, automatic
  **retries**, dead-worker detection (a crashed worker's task is requeued), and live
  per-task progress.
- **Friendly apps.** Three small dark-themed desktop apps — start the manager, run a
  worker, watch jobs with progress bars — plus desktop **notifications** when a render
  finishes or fails.
- **Submit from where you work.** A Blender add-on and a Maya shelf button push the open
  scene to the farm, or script it with the CLI.

## The pieces
- **Manager** — a FastAPI + SQLite service ([`muffin/manager/`](muffin/manager/)). The
  single source of truth; everything talks to it over a small REST API.
- **Muffin Node** (`python -m muffin.gui`) — run the manager on this machine, reach
  Settings (manager URL, worker name, DCC executable paths) and launch the other apps.
- **Muffin Worker** (`python -m muffin.gui.worker`) — run a render worker and watch its
  status/log + live machine stats. (Or headless: `python -m muffin.worker`.)
- **Muffin Monitor** (`python -m muffin.gui.monitor`) — live jobs + tasks + workers view
  with progress bars, search, filters and toast notifications.
- **In-DCC submitters** — a **Blender add-on** and a **Maya shelf tool** submit the current
  scene straight to the farm ([`integrations/`](integrations/)).
- **CLI client** (`python -m muffin.client`) for scripted submission.

## Install
```powershell
pip install -r requirements.txt          # core (manager + worker + CLI)
pip install -r requirements-gui.txt      # the desktop apps (PySide6 + psutil)
```
Python 3.10+.

On Windows, double-click the batch files (they use `pythonw`, so no console window):
`run_gui.bat` (Node), `run_worker.bat` (Worker), `run_monitor.bat` (Monitor).

## Quick start
1. **Start the manager.** Open **Muffin Node** (`run_gui.bat`). The manager **auto-starts**
   when the Node opens (stop/start it manually any time, or turn auto-start off in
   *Settings*). Headless alternative: `python -m muffin.manager`.
2. **Configure DCC paths once.** In Node ▸ **Settings**, point Blender (etc.) at its
   executable with the file picker. Saved to `~/.muffin/settings.json`, which workers read
   automatically.
3. **Start a worker.** Open **Muffin Worker** (`run_worker.bat`). It **auto-starts**
   rendering on launch (toggle in *Settings*); the big status text shows idle / rendering.
4. **Submit a job** — from the Blender/Maya submitter, or the CLI:
   ```powershell
   # fake job to prove the pipeline with nothing installed:
   python -m muffin.client submit --name smoke --dcc mock --scene none `
       --output ./mockout --start 1 --end 20 --chunk 4

   # a real Blender job:
   python -m muffin.client submit --name shotA --dcc blender --renderer CYCLES `
       --scene //server/proj/shotA.blend --output //server/proj/out/sh_#### `
       --start 1 --end 100 --chunk 5 --priority 60
   ```
5. **Watch it** in **Muffin Monitor** (`run_monitor.bat`).

> Verified end-to-end against **Blender 5.1** on Windows 11: a 6-frame Cycles job chunked
> into 3 tasks rendered to valid PNGs with live per-task progress.

## The desktop apps

### Muffin Node (`python -m muffin.gui`)
The control panel for a farm machine. Start/stop the manager (auto-starts by default),
open the shared **Settings** dialog (manager URL + DCC executable paths), and launch the
Monitor and Worker.

### Muffin Worker (`python -m muffin.gui.worker`)
Runs a render worker on this machine.
- **Task tab** — what it's rendering now (job, task, frame, live progress) and what it
  rendered last. The log is one click away.
- **Machine Information tab** — CPU/GPU/RAM/disk specs and live usage bars (needs
  `psutil`; GPU stats via `nvidia-smi`).
- **Auto-start** on launch (toggle in *Settings*), and a **Job Control** menu to stop the
  current task, or stop/restart the worker — or reboot/shut down the PC — *after* the
  current render finishes.
- **Notifications** — a desktop toast when a render finishes or fails on this machine.

### Muffin Monitor (`python -m muffin.gui.monitor`)
The live farm view: jobs (a tree — multi-layer/scene submissions group into one expandable
row), the selected job's frame-chunk **tasks**, and all **workers** with their specs.
- **Right-click a job** to edit / open the render folder / preview in DJV / start / pause /
  resume / requeue / cancel / delete (multi-select friendly).
- **Double-click a job** for its full per-task log; **double-click a task** for just that
  task's log.
- **Search + status filters** on jobs and workers.
- **Notifications** menu — toast when any job finishes or fails (master switch +
  per-event toggles).
- **Layout** menu — **Compact Job List** with a pop-up **Compact Columns** picker (choose
  exactly which columns the compact view shows), **Auto-fit columns to window** (on by
  default; turn it off and columns keep their widths with a horizontal scrollbar, so
  dragging one column *pushes* the rest instead of squeezing them — and a column is never
  shrunk narrower than its title), plus save / load / reset of your layout.
- **Super Muffin Mode** (Edit menu) unlocks worker editing, **Pool Management**, and the
  manager-URL override.

## Submitting from your DCC

### Blender
1. Install [`integrations/blender/muffin_blender_submit.py`](integrations/blender/) (Edit ▸
   Preferences ▸ Add-ons ▸ Install…), enable it, and set the **Manager URL** in the add-on
   preferences.
2. Save your `.blend`, then in **Properties ▸ Output ▸ Muffin Farm** set the job name,
   frames-per-task and priority, and click **Submit to Muffin**.

See [integrations/blender/README.md](integrations/blender/README.md).

### Maya
1. Load [`integrations/maya/muffin_maya_submit.py`](integrations/maya/) (or use the
   [one-step installer](#one-step-add-on-install), which adds a **Muffin** shelf button).
2. Click the shelf button to open the submitter. Set the job name, pool, priority,
   frames-per-task, CPU threads and project path, then **Muffin!!!!!!!!!**.
3. **Render layers:** choose *Current render layer* or *All render layers*. With multiple
   renderable layers, **each layer is submitted as its own job** under one batch — the
   Monitor shows them as a single expandable submission and **you see each layer finish
   independently** (each renders with `-rl <layer>` and its own frame range).

### CLI
```powershell
python -m muffin.client submit ...        # see --help
python -m muffin.client jobs | workers | dccs
python -m muffin.client pause|resume|cancel|retry <job_id>
```

## One-step add-on install
Setting up a new artist machine? Install both submitters in one go:
```powershell
python integrations/install_addons.py          # both Blender + Maya
python integrations/install_addons.py blender   # just one
python integrations/install_addons.py maya
```
…or just double-click **`install_addons.bat`** on Windows.

- **Blender** — installs *and enables* the add-on through Blender's own headless CLI, so it
  lands in the correct user add-ons folder and survives restarts. (Blender is located via
  `MUFFIN_BLENDER_EXE` → Muffin settings → `PATH`.)
- **Maya** — copies the submitter into your user scripts folder and writes a **Muffin**
  shelf button for every Maya version found, so a Muffin shelf appears on restart.

## Running the manager on a NAS / server
The manager doesn't need a PC — it runs great as a Docker container on a Synology
(Container Manager) or any always-on box. See [deploy/README.md](deploy/README.md) for the
step-by-step; then every worker, Monitor and submitter just points at `http://<nas-ip>:8080`.

### Workers on other machines
Point a worker at the manager and run it. DCC paths/URL come from that machine's Node
settings, or from env vars:
```powershell
$env:MUFFIN_MANAGER_URL = "http://192.168.1.10:8080"
$env:MUFFIN_BLENDER_EXE = "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"
python -m muffin.worker
```
> DCC executable resolution order: `MUFFIN_<DCC>_EXE` env var → the settings file → the
> bare name on `PATH`.

### Worker "Accepts" (capabilities) and pools
Most farms are homogeneous — every machine has every DCC — so by default a worker
**accepts any job** (its "Accepts" shows `any`). Set capabilities only to *restrict* a node
to certain DCCs (e.g. a GPU-only or licence-limited box): in Node settings, in the Monitor
(Super Muffin Mode ▸ Edit worker), or with `MUFFIN_CAPABILITIES=blender,nuke`. **Pools**
(Monitor ▸ Pool Management) further restrict which workers a given job may run on.

## Configuration (environment variables)
| var | default | meaning |
|-----|---------|---------|
| `MUFFIN_HOST` / `MUFFIN_PORT` | `0.0.0.0` / `8080` | manager bind address |
| `MUFFIN_DB_PATH` | `~/.muffin/muffin.db` | SQLite database |
| `MUFFIN_DATA_DIR` | `~/.muffin` | folder for the db + `settings.json` |
| `MUFFIN_MANAGER_URL` | `http://127.0.0.1:8080` | where a worker finds the manager |
| `MUFFIN_CAPABILITIES` | (accept all) | comma list of DCCs a worker accepts |
| `MUFFIN_<DCC>_EXE` | (on PATH) | full path to a DCC exe, e.g. `MUFFIN_BLENDER_EXE` |
| `MUFFIN_WORKER_NAME` | hostname | worker display name |
| `MUFFIN_HEARTBEAT` | `5` | worker poll interval (s) |
| `MUFFIN_WORKER_TIMEOUT` | `30` | mark worker offline after (s) |
| `MUFFIN_MAX_ATTEMPTS` | `3` | task retries before failing |

Most of these are also editable in the GUIs (Node settings / Monitor).

## How DCC commands are built
Each DCC plugin lives in [`muffin/dccs/`](muffin/dccs/) and turns a task into a command line:

| DCC | executable | default command shape |
|-----|-----------|------------------------|
| blender | `blender` | `blender -b scene -E RENDERER -o out -s S -e E -j STEP -a` |
| maya | `Render` | `Render -r RENDERER -s S -e E -b STEP -rd out scene` |
| houdini | `husk` | `husk -f S -n COUNT -R RENDERER -o out scene.usd` (or `hython` ROP mode) |
| nuke | `nuke` | `nuke -x -F S-ExSTEP -X WriteNode script.nk` |
| mock | — | simulated render, writes a text file per frame |

Studio pipelines differ, so every plugin accepts an `extra` JSON blob on the job.
`extra.args` is appended verbatim, and some plugins read specific keys (e.g. Nuke's
`write_node`, Houdini's `mode`/`rop`). Tune the plugins in [`muffin/dccs/`](muffin/dccs/).

## CLI
```
python -m muffin.client submit ...        # see --help
python -m muffin.client jobs | workers | dccs
python -m muffin.client pause|resume|cancel|retry <job_id>
```

## Project layout
```
muffin/
  config.py             # env-driven settings
  settings.py           # persistent node settings (DCC paths + GUI prefs) shared by GUIs + worker
  common/schemas.py     # shared pydantic models
  manager/
    app.py              # FastAPI REST API
    db.py               # SQLite data layer
    scheduler.py        # task assignment + job reconciliation
  worker/
    agent.py            # register / heartbeat / request / report loop
    runner.py           # subprocess execution + progress streaming
  dccs/                 # one plugin per DCC (+ registry)
  gui/
    app.py              # Muffin Node    (python -m muffin.gui)
    worker.py           # Muffin Worker  (python -m muffin.gui.worker)
    monitor.py          # Muffin Monitor (python -m muffin.gui.monitor)
    settings_dialog.py  # shared Node settings dialog
    notify.py           # desktop toast notifications
    style.py            # shared dark stylesheet
  client/               # CLI submitter
integrations/
  blender/              # Blender add-on submitter
  maya/                 # Maya shelf submitter
  install_addons.py     # one-step installer for both submitters
deploy/                 # Docker / NAS deployment
run_gui.bat / run_worker.bat / run_monitor.bat / install_addons.bat
```

## Troubleshooting
- **Monitor says "offline".** The manager isn't reachable. Start it in Muffin Node, check
  the **Manager URL**, and confirm port `8080` is open between machines.
- **Worker registers but never renders.** Check its **Accepts/capabilities** and any
  **pool** restriction on the job, and that the DCC executable path is set (Node ▸ Settings).
- **Maya: "render layer 'all' is not part of the render setup".** Update the Maya submitter
  to the latest (`integrations/maya/muffin_maya_submit.py`, v0.3+) — it submits one job per
  layer instead of the unsupported `-rl all`.
- **Pools don't stick / "Manager out of date".** Restart the manager (or rebuild the NAS
  container) so it has the latest schema.
- **No GPU stats in the Worker.** Install `psutil`, and for GPU make sure NVIDIA's
  `nvidia-smi` is on `PATH`.

## Roadmap
- Auth / users / per-project permissions
- Output-frame thumbnails in the Monitor
- Worker resource stats (CPU/GPU/RAM) and tag-based scheduling
- Houdini / Nuke in-DCC submitters

## Contributing
Issues and PRs are welcome! Muffin is intentionally small and readable — each piece
(manager, worker, a DCC plugin, an app) is a short, self-contained file. Good first
contributions: a new DCC plugin in [`muffin/dccs/`](muffin/dccs/), an in-DCC submitter in
[`integrations/`](integrations/), or Monitor/Worker polish. Please keep changes focused and
match the surrounding style.

## License
_No license file is included yet._ If you're publishing this for the community, add a
`LICENSE` — [MIT](https://choosealicense.com/licenses/mit/) is a common, permissive choice
for projects like this. Until then, all rights are reserved by the author.
