"""Muffin Submitter for Maya.

Sections:
  Job Description  — job name (auto from scene)
  Job Options      — pool (live from the manager), priority
  Render Options   — frames/task, project path, current layer vs all layers

Everything else is auto-detected from the scene / Render Settings; the manager
URL comes from the machine's Muffin settings (same file the Worker/Monitor use).

Install (any one of these):
  * Drag this file into the Maya viewport (Maya 2022+), or
  * Put it in Documents/maya/scripts and make a shelf button with:
        import muffin_maya_submit; muffin_maya_submit.show()
"""

import getpass
import json
import os
import socket
import urllib.error
import urllib.request
import uuid

import maya.cmds as cmds
import maya.mel as mel

# Bump when the submitter changes — shown in the title bar so you can tell at a
# glance whether Maya has the latest version loaded (it caches imported modules).
_VERSION = "0.0.1"
_WINDOW = "muffinSubmitWindow"

# Named priority levels (keep in sync with muffin/priority.py + the Blender add-on).
_PRIORITY_LEVELS = [("Low", 10), ("Below Normal", 30), ("Normal", 50),
                    ("High", 70), ("Urgent", 90)]
_PRIORITY_BY_LABEL = dict(_PRIORITY_LEVELS)


# ----------------------------------------------------------- auto-detect ------
def _manager_url() -> str:
    url = os.environ.get("MUFFIN_MANAGER_URL", "").strip()
    if url:
        return url
    data_dir = os.environ.get(
        "MUFFIN_DATA_DIR", os.path.join(os.path.expanduser("~"), ".muffin"))
    try:
        with open(os.path.join(data_dir, "settings.json"), encoding="utf-8") as fh:
            url = (json.load(fh).get("manager_url") or "").strip()
            if url:
                return url
    except (OSError, ValueError):
        pass
    return "http://muffin"


def _pools() -> list:
    """Pools straight from the manager, so the dropdown matches the farm."""
    try:
        with urllib.request.urlopen(
                _manager_url().rstrip("/") + "/api/pools", timeout=4) as resp:
            return json.loads(resp.read().decode("utf-8")) or []
    except Exception:
        return []


def _render_layers() -> list:
    layers = []
    for rl in cmds.ls(type="renderLayer") or []:
        try:
            if cmds.referenceQuery(rl, isNodeReferenced=True):
                continue
            if not cmds.getAttr(rl + ".renderable"):
                continue
        except Exception:
            continue
        layers.append(rl)
    return layers or ["defaultRenderLayer"]


def _layer_label(layer: str) -> str:
    if layer == "defaultRenderLayer":
        return "masterLayer"
    return layer[3:] if layer.startswith("rs_") else layer


def _scene_info() -> dict:
    scene = cmds.file(q=True, sceneName=True) or ""
    name = os.path.splitext(os.path.basename(scene))[0] if scene else "maya_job"
    animation = bool(cmds.getAttr("defaultRenderGlobals.animation"))
    if animation:
        start = int(cmds.getAttr("defaultRenderGlobals.startFrame"))
        end = int(cmds.getAttr("defaultRenderGlobals.endFrame"))
        step = max(1, int(cmds.getAttr("defaultRenderGlobals.byFrameStep")))
    else:
        start = end = int(cmds.currentTime(q=True))
        step = 1
    renderer = cmds.getAttr("defaultRenderGlobals.currentRenderer")
    project = ""
    images = ""
    try:
        project = cmds.workspace(q=True, rootDirectory=True) or ""
        img_rule = cmds.workspace(fileRuleEntry="images") or "images"
        images = os.path.join(project, img_rule)
    except Exception:
        pass
    return {"scene": scene, "name": name, "animation": animation,
            "start": start, "end": end, "step": step, "renderer": renderer,
            "project": project, "images": images, "layers": _render_layers()}


def _globals_frames() -> tuple:
    """(start, end, step, animation) from the currently active render settings."""
    anim = bool(cmds.getAttr("defaultRenderGlobals.animation"))
    if anim:
        s = int(cmds.getAttr("defaultRenderGlobals.startFrame"))
        e = int(cmds.getAttr("defaultRenderGlobals.endFrame"))
        st = max(1, int(cmds.getAttr("defaultRenderGlobals.byFrameStep")))
    else:
        s = e = int(cmds.currentTime(q=True))
        st = 1
    if e < s:
        s, e = e, s
    return s, e, st, anim


def _layer_frames(layer_name) -> tuple:
    """Frame range for a specific render layer, honouring per-layer overrides.
    Switches to the layer to read its settings, then restores the original."""
    if not layer_name or layer_name == "defaultRenderLayer":
        return _globals_frames()
    try:
        current = cmds.editRenderLayerGlobals(q=True, currentRenderLayer=True)
        if current != layer_name:
            cmds.editRenderLayerGlobals(currentRenderLayer=layer_name)
            try:
                return _globals_frames()
            finally:
                cmds.editRenderLayerGlobals(currentRenderLayer=current)
        return _globals_frames()
    except Exception:
        return _globals_frames()


def _submitter() -> str:
    try:
        return f"{getpass.getuser()}@{socket.gethostname()}"
    except Exception:
        return socket.gethostname()


_RENDERER_MAP = {
    "arnold": "arnold",
    "mayaSoftware": "sw",
}

# Frame padding in exported .ass names (scene.0001.ass). The kick plugin uses the
# same '####' width, so keep the two in sync.
_ASS_PAD = 4


def _post_payloads(payloads, url) -> None:
    """POST each job payload to the manager and report the result."""
    submitted = []
    for payload in payloads:
        req = urllib.request.Request(
            url.rstrip("/") + "/api/jobs",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                submitted.append(json.loads(resp.read().decode("utf-8")))
        except urllib.error.URLError:
            cmds.confirmDialog(title="Muffin", icon="critical", button=["OK"],
                               message="Submit failed — could not reach the Muffin Manager!\n"
                                       "Is it running at %s?" % url)
            return
        except Exception as exc:
            cmds.confirmDialog(title="Muffin", icon="critical", button=["OK"],
                               message="Submit failed: %s" % exc)
            return
    msg = ("Submitted %d job(s)" % len(submitted)) if len(submitted) > 1 else "Submitted!"
    cmds.confirmDialog(title="Muffin", icon="information", button=["OK"],
                       message=msg + "\nCheck Muffin's Monitor for progress.")


def _export_ass(name, ass_dir, start, end, step):
    """Export the scene to one .ass per frame, then return the '####' path
    pattern the kick job substitutes per frame (or None on failure)."""
    try:
        os.makedirs(ass_dir, exist_ok=True)
    except OSError as exc:
        cmds.confirmDialog(title="Muffin", icon="critical", button=["OK"],
                           message="Could not create the .ass folder:\n%s" % exc)
        return None
    base = os.path.join(ass_dir, name + ".ass")
    cmds.waitCursor(state=True)
    try:
        # arnoldExportAss writes <name>.<frame>.ass per frame for the range.
        # (Flag names are MtoA's; if your version differs, the error below says so.)
        cmds.arnoldExportAss(f=base, selected=False,
                             startFrame=start, endFrame=end, frameStep=step)
    except Exception as exc:
        cmds.confirmDialog(title="Muffin", icon="critical", button=["OK"],
                           message="Could not export .ass files.\nIs the Arnold "
                                   "(mtoa) plugin loaded and the renderer set to "
                                   "Arnold?\n\n%s" % exc)
        return None
    finally:
        cmds.waitCursor(state=False)
    import glob
    import re
    files = sorted(glob.glob(os.path.join(ass_dir, glob.escape(name) + ".*.ass")))
    if not files:
        cmds.confirmDialog(title="Muffin", icon="warning", button=["OK"],
                           message="No .ass files were written to:\n%s" % ass_dir)
        return None
    # Derive the real frame padding from what MtoA actually wrote (e.g.
    # scene.0001.ass -> 4) instead of assuming a fixed width. Keep the native
    # path — do NOT swap backslashes, or UNC shares (\\nas\…) break.
    m = re.search(re.escape(name) + r"\.(\d+)\.ass$", os.path.basename(files[0]))
    pad = len(m.group(1)) if m else _ASS_PAD
    return os.path.join(ass_dir, "%s.%s.ass" % (name, "#" * pad))


def _build_kick_payloads(info, scene, name, pool, priority, layers_mode):
    """KickAss mode: export the scene to .ass here, then submit 'kick' jobs that
    render each .ass with kick.exe on the farm (one .ass per task). With multiple
    render layers + 'All render layers', each layer is exported and submitted as
    its OWN job (named '<job> - <layer>', grouped in a batch) so the Monitor shows
    the layer — exactly like the Maya render path."""
    if (info["renderer"] or "").lower() != "arnold":
        cmds.confirmDialog(title="Muffin", icon="critical", button=["OK"],
                           message="KickAss needs the Arnold renderer.\nSet Render "
                                   "Settings ▸ Render Using ▸ Arnold and try again.")
        return None

    layers = info["layers"]
    if layers_mode == "All render layers" and len(layers) > 1:
        targets = list(layers)
    elif len(layers) > 1:
        targets = [cmds.editRenderLayerGlobals(q=True, currentRenderLayer=True)]
    else:
        targets = [None]                       # single / master layer
    batch = "%s::%s" % (name, uuid.uuid4().hex[:8]) if len(targets) > 1 else ""
    original = cmds.editRenderLayerGlobals(q=True, currentRenderLayer=True)
    scene_dir = os.path.dirname(scene)
    payloads = []
    try:
        for layer in targets:
            label = _layer_label(layer) if layer else ""
            if layer and layer != original:
                cmds.editRenderLayerGlobals(currentRenderLayer=layer)  # export THIS layer
            s, e, st, _anim = _layer_frames(layer)   # honour per-layer frame overrides
            # Folder by the unique raw layer node name (not the display label, so
            # 'character' and 'rs_character' can't share a folder and overwrite).
            ass_dir = os.path.join(scene_dir, "ass", name, layer or "main")
            pattern = _export_ass(name, ass_dir, s, e, st)
            if not pattern:
                return None
            payloads.append({
                "name": "%s - %s" % (name, label) if label else name,
                "dcc": "kick", "renderer": "arnold",
                "scene_path": pattern, "output_path": info["images"],
                "frame_start": s, "frame_end": e, "frame_step": st,
                "chunk_size": 1,                # one .ass file per task
                "priority": priority, "extra": {"ass_pattern": pattern},
                "submitter": _submitter(), "batch": batch, "pool": pool,
                "suspended": False,
            })
    finally:
        try:
            if cmds.editRenderLayerGlobals(q=True, currentRenderLayer=True) != original:
                cmds.editRenderLayerGlobals(currentRenderLayer=original)
        except Exception:
            pass
    return payloads

# ----------------------------------------------------------------- submit -----
def _submit(*_args) -> None:
    scene = cmds.file(q=True, sceneName=True) or ""
    if not scene:
        cmds.confirmDialog(title="Muffin", icon="critical", button=["OK"],
                           message="Save the scene first — workers need a file path to open.")
        return
    # Auto-save so the workers open the current state — no prompt.
    if cmds.file(q=True, modified=True):
        try:
            cmds.file(save=True)
        except Exception as exc:
            cmds.confirmDialog(title="Muffin", icon="critical", button=["OK"],
                               message="Could not save the scene: %s" % exc)
            return

    info = _scene_info()
    url = _manager_url()
    name = cmds.textFieldGrp("muffinName", q=True, text=True).strip() or info["name"]
    pool = cmds.optionMenuGrp("muffinPool", q=True, value=True)
    pool = "" if pool == "none" else pool
    priority = _PRIORITY_BY_LABEL.get(
        cmds.optionMenuGrp("muffinPrio", q=True, value=True), 50)
    chunk = max(1, cmds.intField("muffinChunk", q=True, value=True))
    threads = cmds.intField("muffinThreads", q=True, value=True)
    project = cmds.textFieldGrp("muffinProj", q=True, text=True).strip()
    layers_mode = cmds.optionMenuGrp("muffinLayers", q=True, value=True)
    render_with = cmds.optionMenuGrp("muffinRenderWith", q=True, value=True)

    # Arnold .ass + kick: export the scene to .ass here, then let the farm render
    # those files with kick.exe (no Maya licence needed on the workers).
    if render_with.startswith("Arnold"):
        payloads = _build_kick_payloads(info, scene, name, pool, priority, layers_mode)
        if payloads:
            _post_payloads(payloads, url)
        return

    out = info["images"]
    common_args = []
    if project:
        common_args += ["-proj", project]

    def _payload_for(layer_name):
        """One payload, using THIS layer's own frame range / animation
        setting (layers can override the globals)."""
        s, e, st, anim = _layer_frames(layer_name)
        single = (not anim) and s == e
        # Output follows the scene's Render Settings exactly (the image file
        # prefix tokens build the folders) — no -rd override.
        extra = {"args": list(common_args), "no_output_flag": True,
                 "render_threads": threads}
        if single:
            extra["single_frame"] = True
        p = {
            "name": name, "dcc": "maya",
            "renderer": _RENDERER_MAP.get(info["renderer"], info["renderer"]),
            "scene_path": scene, "output_path": out,
            "frame_start": s, "frame_end": e, "frame_step": st,
            "chunk_size": chunk, "priority": priority, "extra": extra,
            "submitter": _submitter(), "batch": "", "pool": pool,
            "suspended": False,
        }
        if layer_name:
            p["name"] = "%s - %s" % (name, _layer_label(layer_name))
            p["extra"] = dict(extra, args=common_args + ["-rl", layer_name])
        return p

    layers = info["layers"]
    if layers_mode == "All render layers" and len(layers) > 1:
        # One job PER render layer, grouped under a shared batch id. The Monitor
        # shows the batch as one expandable submission whose children are the
        # layers, so each layer reports its own progress and "done" — you can see
        # which layers a machine has finished without waiting for the whole
        # scene. Each job renders just its layer with "-rl <layer>" (the only
        # multi-layer form Maya's Render command actually supports), and uses
        # that layer's own frame range. (A single "all layers" job can't show
        # per-layer progress — it's one opaque render process.)
        batch = "%s::%s" % (name, uuid.uuid4().hex[:8])
        payloads = []
        for rl in layers:
            p = _payload_for(rl)
            p["batch"] = batch
            payloads.append(p)
    elif len(layers) > 1:
        # Several layers exist but the user picked just the active one.
        current = cmds.editRenderLayerGlobals(q=True, currentRenderLayer=True)
        payloads = [_payload_for(current)]
    else:
        payloads = [_payload_for(None)]

    _post_payloads(payloads, url)


def _open_render_settings(*_args) -> None:
    mel.eval("unifiedRenderGlobalsWindow")


# ------------------------------------------------------------------- ui -------
def show() -> None:
    """Open (or re-open) the submitter window."""
    if cmds.window(_WINDOW, exists=True):
        cmds.deleteUI(_WINDOW)
    # Maya caches each window's last size in its prefs, which often reopens this
    # one too big or too small. Clearing the pref lets resizeToFitChildren size
    # it fresh to its current contents every time.
    if cmds.windowPref(_WINDOW, exists=True):
        cmds.windowPref(_WINDOW, remove=True)
    info = _scene_info()
    pools = _pools()
    multi_layers = len(info["layers"]) > 1

    cmds.window(_WINDOW, title="Muffin Submitter  v%s" % _VERSION, sizeable=True,
                resizeToFitChildren=True, widthHeight=(372, 120))
    cmds.columnLayout(adjustableColumn=True, rowSpacing=3,
                      columnOffset=("both", 6))
    cmds.separator(style="none", height=3)
    cmds.textFieldGrp("muffinName", label="Job Name", text=info["name"],
                      columnWidth2=(95, 245))
    cmds.optionMenuGrp("muffinPool", label="Pool", columnWidth2=(95, 160))
    cmds.menuItem(label="none")
    for p in pools:
        cmds.menuItem(label=p)
    cmds.optionMenuGrp("muffinPrio", label="Priority", columnWidth2=(95, 160))
    for _lbl, _val in _PRIORITY_LEVELS:
        cmds.menuItem(label=_lbl)
    cmds.optionMenuGrp("muffinPrio", e=True, value="Normal")
    # Frames Per Task / CPU Threads share one row.
    cmds.rowLayout(numberOfColumns=4,
                   columnWidth4=(90, 55, 80, 55),
                   columnAlign=[(1, "right"), (3, "right")],
                   columnAttach=[(1, "both", 2), (2, "both", 2),
                                 (3, "both", 2), (4, "both", 2)])
    cmds.text(label="Frames / Task")
    cmds.intField("muffinChunk", value=5, minValue=1, width=55)
    cmds.text(label="CPU Threads",
              annotation="0 = all cores; negative = all but N (Arnold / V-Ray)")
    cmds.intField("muffinThreads", value=0, width=55)
    cmds.setParent("..")
    cmds.textFieldGrp("muffinProj", label="Project Path", text=info["project"],
                      columnWidth2=(95, 245))
    cmds.optionMenuGrp("muffinRenderWith", label="Render with",
                       columnWidth2=(95, 200),
                       annotation="Maya: workers run Render.exe.\n"
                                  "Arnold .ass + kick: export .ass here (the scene must "
                                  "be on a share the workers can read), then the farm "
                                  "renders them with kick — no Maya licence on workers.")
    cmds.menuItem(label="Maya (Render.exe)")
    cmds.menuItem(label="Arnold .ass + kick")
    cmds.optionMenuGrp("muffinLayers", label="Render layers", columnWidth2=(95, 160))
    cmds.menuItem(label="Current render layer")
    cmds.menuItem(label="All render layers")
    if multi_layers:
        cmds.optionMenuGrp("muffinLayers", e=True, value="All render layers")
    else:
        cmds.optionMenuGrp("muffinLayers", e=True, enable=False)
    cmds.separator(height=6)
    cmds.rowLayout(numberOfColumns=2, columnWidth2=(170, 170),
                   columnAttach2=("both", "both"), columnOffset2=(2, 2))
    cmds.button(label="Render Settings", height=30, command=_open_render_settings)
    cmds.button(label="Muffin!!!!!!!!!", height=30, command=_submit,
                backgroundColor=(0.91, 0.63, 0.23))
    cmds.setParent("..")
    cmds.separator(style="none", height=4)
    cmds.showWindow(_WINDOW)


def reload_show() -> None:
    """Force-reload this module from disk, then open — handy because Maya caches
    imported modules, so a plain show() can run an old version."""
    import importlib
    import sys
    mod = sys.modules.get(__name__)
    if mod is not None:
        importlib.reload(mod).show()
    else:
        show()


def _this_path() -> str:
    try:
        return os.path.abspath(__file__)
    except NameError:
        return ""


def _shelf_command(path: str) -> str:
    """Shelf-button code: load THIS file fresh from disk every click (beats
    Maya's module cache) and open the submitter."""
    return ("import importlib.util\n"
            "_p = r\"%s\"\n"
            "_s = importlib.util.spec_from_file_location('muffin_maya_submit', _p)\n"
            "_m = importlib.util.module_from_spec(_s); _s.loader.exec_module(_m); _m.show()"
            % path)


def install(path: str = "") -> None:
    """Add a persistent 'Muffin' button to the current shelf — click it in any
    future Maya session, no more dragging the file in."""
    path = path or _this_path()
    if not path or not os.path.isfile(path):
        cmds.confirmDialog(title="Muffin", icon="critical", button=["OK"],
                           message="Couldn't locate the submitter file to install.\n"
                                   "Run install() with the full path to "
                                   "muffin_maya_submit.py.")
        return
    shelf_top = mel.eval("$tmp = $gShelfTopLevel")
    shelf = cmds.tabLayout(shelf_top, q=True, selectTab=True)
    # Replace any existing Muffin button so re-installing never duplicates.
    for b in (cmds.shelfLayout(shelf, q=True, childArray=True) or []):
        try:
            if cmds.shelfButton(b, q=True, label=True) == "Muffin":
                cmds.deleteUI(b)
        except Exception:
            pass
    cmds.shelfButton(parent=shelf, label="Muffin",
                     annotation="Open the Muffin render-farm submitter",
                     imageOverlayLabel="Muffin", image1="render.png",
                     sourceType="python", command=_shelf_command(path))
    try:
        mel.eval("saveAllShelves $gShelfTopLevel")  # persist immediately
    except Exception:
        pass
    cmds.confirmDialog(
        title="Muffin", icon="information", button=["OK"],
        message="Installed a 'Muffin' button on your current shelf.\n"
                "Click it any time to open the submitter — no need to drag the "
                "file in again.")


def onMayaDroppedPythonFile(*_args) -> None:
    """Drag-and-drop: install a persistent shelf button, then open the window."""
    path = _this_path()
    if path:
        try:
            install(path)
        except Exception:
            pass
    show()


if __name__ == "__main__":
    show()
