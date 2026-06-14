#!/usr/bin/env python3
"""Install Muffin's DCC submitters on this PC so a new machine is one step away.

  python integrations/install_addons.py            # both Blender + Maya
  python integrations/install_addons.py blender     # just one
  python integrations/install_addons.py maya
  (or double-click install_addons.bat on Windows)

Blender — installs and enables the add-on through Blender's own CLI, so it lands
  in the right user add-ons folder for whatever Blender we can find and survives
  restarts. We locate blender.exe via MUFFIN_BLENDER_EXE, then the Muffin
  settings.json (dcc_paths.blender), then PATH.
Maya — copies the submitter into your user scripts folder and writes a "Muffin"
  shelf button for every Maya version found, so a Muffin tab appears on restart.

Standard library only — safe to run on a fresh machine before anything else is
set up. Nothing here renders; it only copies files / drives the DCC's installer.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BLENDER_ADDON = HERE / "blender" / "muffin_blender_submit.py"
MAYA_SUBMITTER = HERE / "maya" / "muffin_maya_submit.py"
HOME = Path.home()
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _settings_dcc(name: str) -> str:
    """A DCC path from Muffin's settings.json, or '' — same file the apps use."""
    data_dir = os.environ.get("MUFFIN_DATA_DIR", str(HOME / ".muffin"))
    try:
        data = json.loads((Path(data_dir) / "settings.json").read_text("utf-8"))
        return (data.get("dcc_paths") or {}).get(name, "") or ""
    except (OSError, ValueError):
        return ""


# ----------------------------------------------------------------- Blender ----
def _resolve_blender() -> str:
    """blender.exe via env override → settings.json → PATH (Muffin's own order)."""
    for cand in (os.environ.get("MUFFIN_BLENDER_EXE", ""),
                 _settings_dcc("blender"), "blender"):
        if not cand:
            continue
        if os.path.dirname(cand):
            if Path(cand).exists():
                return cand
        else:
            found = shutil.which(cand)
            if found:
                return found
    return ""


def install_blender() -> bool:
    print("Blender:")
    if not BLENDER_ADDON.exists():
        print(f"  x add-on missing at {BLENDER_ADDON}")
        return False
    exe = _resolve_blender()
    if not exe:
        print("  x Blender not found. Set MUFFIN_BLENDER_EXE, or set the Blender")
        print("    path in Muffin > Settings, then re-run.")
        return False
    print(f"  using {exe}")
    # Drive Blender's own installer headlessly: install the file, enable the
    # module, and persist user preferences so it stays enabled next launch.
    # repr() embeds the path as a proper Python literal — robust to spaces,
    # backslashes and apostrophes (a bare r'...' would break on an apostrophe).
    expr = (
        "import bpy, sys;"
        f"bpy.ops.preferences.addon_install(filepath={repr(str(BLENDER_ADDON))}, overwrite=True);"
        "bpy.ops.preferences.addon_enable(module='muffin_blender_submit');"
        "bpy.ops.wm.save_userpref();"
        # addon_enable doesn't raise on a bad/uninstalled module, so make the
        # exit code tell the truth: only success if it's actually registered.
        "sys.exit(0 if 'muffin_blender_submit' in bpy.context.preferences.addons else 1)"
    )
    try:
        # NB: no --factory-startup. We must load the user's real preferences so
        # save_userpref() preserves their other add-ons instead of resetting
        # everything to factory defaults plus ours.
        # errors="replace": Blender's console output can contain bytes that
        # aren't valid in the locale code page (e.g. cp1252) — without this the
        # subprocess reader thread crashes with UnicodeDecodeError even though
        # the install itself succeeded.
        r = subprocess.run(
            [exe, "--background", "--python-expr", expr],
            capture_output=True, text=True, errors="replace",
            timeout=180, creationflags=_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"  x could not run Blender: {exc}")
        return False
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode == 0 and "Traceback" not in out:
        print("  ok add-on installed and enabled "
              "(Properties > Output > Muffin Submitter).")
        return True
    print("  ! Blender exited unhappily - install may not have completed:")
    for ln in out.strip().splitlines()[-4:]:
        # Sanitise to ASCII so echoing Blender's output can't itself raise a
        # UnicodeEncodeError on a non-UTF-8 / redirected console.
        print("    ", ln.encode("ascii", "replace").decode("ascii"))
    return False


# -------------------------------------------------------------------- Maya ----
def _maya_user_dir() -> Path:
    if sys.platform == "win32":
        return HOME / "Documents" / "maya"
    if sys.platform == "darwin":
        return HOME / "Library" / "Preferences" / "Autodesk" / "maya"
    return HOME / "maya"


_SHELF_TEMPLATE = '''\
global proc shelf_Muffin () {{
    shelfButton
        -enableCommandRepeat 1
        -enable 1
        -width 35
        -height 35
        -manage 1
        -visible 1
        -label "Muffin"
        -annotation "Submit the current scene to the Muffin render farm"
        -imageOverlayLabel "Muffin"
        -image "render.png"
        -image1 "render.png"
        -style "iconOnly"
        -sourceType "python"
        -command "{command}"
    ;
}}
'''


def install_maya() -> bool:
    print("Maya:")
    if not MAYA_SUBMITTER.exists():
        print(f"  x submitter missing at {MAYA_SUBMITTER}")
        return False
    base = _maya_user_dir()
    scripts = base / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    dest = scripts / MAYA_SUBMITTER.name
    shutil.copy2(MAYA_SUBMITTER, dest)
    print(f"  ok submitter copied -> {dest}")

    # Reload from disk on every click so updates land without a Maya restart.
    command = ("import muffin_maya_submit, importlib; "
               "importlib.reload(muffin_maya_submit); muffin_maya_submit.show()")
    versions = sorted(p.name for p in base.glob("[0-9][0-9][0-9][0-9]")
                      if p.is_dir())
    made = 0
    for ver in versions:
        shelves = base / ver / "prefs" / "shelves"
        shelves.mkdir(parents=True, exist_ok=True)
        (shelves / "shelf_Muffin.mel").write_text(
            _SHELF_TEMPLATE.format(command=command), encoding="utf-8")
        print(f"  ok Muffin shelf written for Maya {ver}")
        made += 1
    if not made:
        print(f"  ! no Maya version folders under {base} - the script is")
        print("    importable; make a shelf button running:")
        print(f"      {command}")
        print("    (or launch Maya once to create its folders, then re-run.)")
        return False  # the copy worked, but the easy shelf button didn't
    print("  (restart Maya to see the Muffin shelf tab)")
    return True


def main() -> int:
    targets = [a.lower() for a in sys.argv[1:]] or ["all"]
    if "all" in targets:
        targets = ["blender", "maya"]
    ok = True
    if "blender" in targets:
        ok = install_blender() and ok
    if "maya" in targets:
        ok = install_maya() and ok
    print()
    print("Done." if ok else "Finished with warnings - see above.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
