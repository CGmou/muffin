# Muffin Submitter — Maya

Submits the current Maya scene to your Muffin render farm. Everything is
auto-detected — you only type **Frames / task** and **Priority**.

## Install — one click (recommended for a new PC)
From the repo root, double-click **`install_addons.bat`** (or run
`python integrations/install_addons.py maya`). It copies the submitter into your
user `maya/scripts` folder and writes a **Muffin** shelf button for every Maya
version found — restart Maya and click it. The button reloads the script fresh
on every click, so re-running the installer is all an update takes.

## Install — recommended shelf button (always loads the latest)
Maya caches imported modules, so `import muffin_maya_submit; show()` can keep
running an **old** copy even after you update the file. Use this shelf button
instead — it loads the file **fresh from disk every click**, so you always get
the current version (point the path at this repo file, your single source of
truth — don't keep a second copy in `scripts/`):

```python
import importlib.util
_p = r"C:\Users\weeda\Documents\GitHub\muffin\integrations\maya\muffin_maya_submit.py"
_s = importlib.util.spec_from_file_location("muffin_maya_submit", _p)
_m = importlib.util.module_from_spec(_s); _s.loader.exec_module(_m); _m.show()
```

The window title shows the version (e.g. **"Muffin Submitter  v2.2"**) — if it
doesn't match the `_VERSION` in the file, Maya loaded a stale copy.

### Other install options
- **Drag & drop** the `.py` into the viewport (opens fresh each time too).
- Plain `import muffin_maya_submit; muffin_maya_submit.show()` works but is
  prone to the caching issue above — use `muffin_maya_submit.reload_show()` to
  force a reload.

## What's automatic
| Thing | Where it comes from |
|-------|---------------------|
| Manager URL | Muffin's `settings.json` on this machine (same one the Worker/Monitor use), or `MUFFIN_MANAGER_URL` |
| Job name | the scene file name (editable) |
| Frame range / step | Render Settings |
| Renderer | Render Settings (Arnold → `arnold`, V-Ray → `vray`, Redshift → `redshift`, Maya SW/HW2 → `sw`/`hw2`) |
| Render layers | every **renderable** layer submits as its own job (`Render -rl`), grouped as one job in the Monitor — each layer's frames go to their own subfolder |
| Output dir | the project's images directory (editable) |

## Buttons
- **Render Settings…** — opens Maya's Render Settings window so you can adjust
  range/renderer/layers without leaving the submitter.
- **⟳ Refresh** — re-reads the scene after you change settings.
- **Muffin!!!!!!!!!** — submits (offers to save unsaved changes first).

## Notes
- The scene path should be on a network share every worker can reach.
- Workers need Maya's `Render.exe` configured (Settings ▸ Maya path in the
  Muffin Manager/Worker apps).
- Studio-specific flags (project dir, AOVs, OCIO) can be added via the job's
  `extra.args` — see `muffin/dccs/maya.py`.
