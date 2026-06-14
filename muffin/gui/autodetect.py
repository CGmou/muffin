"""Best-effort auto-detection of DCC executables (and the kick render folders)
from standard install locations, so the Settings dialog can fill every path in
one click instead of browsing for each.

Windows-first (that's where the farm runs). Each detector returns a list of
(version, paths) newest-version-first, so the caller can let the user choose when
several versions are installed. Everything is best-effort — found paths are
filled in for the user to review, never assumed correct.
"""

import glob
import os
import re

_PF = os.environ.get("ProgramFiles", r"C:\Program Files")


def _ver_key(text: str) -> tuple:
    return tuple(int(n) for n in re.findall(r"\d+", text)) or (0,)


def _newest_first(pairs: list) -> list:
    return sorted(pairs, key=lambda p: _ver_key(p[0]), reverse=True)


def _first_file(*candidates: str) -> str:
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return ""


def _first_dir(*candidates: str) -> str:
    for c in candidates:
        if c and os.path.isdir(c):
            return c
    return ""


def detect_blender() -> list:
    out = []
    for exe in glob.glob(os.path.join(_PF, "Blender Foundation", "Blender *", "blender.exe")):
        ver = os.path.basename(os.path.dirname(exe)).replace("Blender ", "")
        out.append((ver, {"blender": exe}))
    return _newest_first(out)


def detect_houdini() -> list:
    out = []
    for root in glob.glob(os.path.join(_PF, "Side Effects Software", "Houdini *")):
        ver = os.path.basename(root).replace("Houdini ", "")
        paths = {}
        husk = os.path.join(root, "bin", "husk.exe")
        hython = os.path.join(root, "bin", "hython.exe")
        if os.path.isfile(husk):
            paths["houdini"] = husk
        if os.path.isfile(hython):
            paths["hython"] = hython
        if paths:
            out.append((ver, paths))
    return _newest_first(out)


def detect_maya() -> list:
    """Maya Render.exe + the matching Arnold (kick / bin / plugins / procedurals)."""
    out = []
    for mroot in glob.glob(os.path.join(_PF, "Autodesk", "Maya*")):
        m = re.search(r"Maya(\d{4})", os.path.basename(mroot))
        render = os.path.join(mroot, "bin", "Render.exe")
        if not m or not os.path.isfile(render):
            continue
        year = m.group(1)
        arnold = os.path.join(_PF, "Autodesk", "Arnold", "maya%s" % year)
        out.append((year, {
            "maya": render,
            "maya_bin": os.path.join(mroot, "bin"),
            "kick": _first_file(os.path.join(arnold, "bin", "kick.exe")),
            "xgen": _first_dir(os.path.join(mroot, "plug-ins", "xgen")),
            "procedurals": _first_dir(os.path.join(arnold, "procedurals")),
        }))
    return _newest_first(out)


def detect_all() -> dict:
    return {
        "blender": detect_blender(),
        "maya": detect_maya(),
        "houdini": detect_houdini(),
    }
