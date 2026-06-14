"""Muffin Submitter for Blender.

A self-contained Blender add-on (like the Flamenco / Deadline submitters): it
adds a "Muffin Submitter" panel to the Output properties so you can send the
current .blend to your Muffin manager without leaving Blender.

Install:
  Edit ▸ Preferences ▸ Add-ons ▸ Install…  → pick this file → enable
  "Render: Muffin Submitter", then set the Manager URL in its preferences.
  The panel appears under Properties ▸ Output ▸ Muffin Submitter.

It talks to the manager's REST API using only Python's standard library, so no
extra packages need to be installed into Blender.
"""

import json
import urllib.error
import urllib.request

import bpy
from bpy.app.handlers import persistent

bl_info = {
    "name": "Muffin Submitter",
    "author": "Muffin",
    "version": (0, 0, 1),
    "blender": (3, 0, 0),
    "location": "Properties ▸ Output ▸ Muffin Submitter",
    "description": "Submit the current scene to a Muffin render farm",
    "category": "Render",
}

# Named priority levels (keep in sync with muffin/priority.py + the Maya add-on).
# EnumProperty stores the identifier; we map it to the manager's numeric priority.
_PRIORITY_ITEMS = [
    ("LOW", "Low", "Lowest priority"),
    ("BELOW", "Below Normal", "Below normal priority"),
    ("NORMAL", "Normal", "Normal priority"),
    ("HIGH", "High", "High priority"),
    ("URGENT", "Urgent", "Highest priority — runs first"),
]
_PRIORITY_VALUES = {"LOW": 10, "BELOW": 30, "NORMAL": 50, "HIGH": 70, "URGENT": 90}


def _prefs():
    """The add-on's preferences (where the Manager URL is configured)."""
    return bpy.context.preferences.addons[__name__].preferences


def _submitter() -> str:
    import getpass
    import socket
    try:
        return f"{getpass.getuser()}@{socket.gethostname()}"
    except Exception:
        return socket.gethostname()


def _submit_payload(scene) -> dict:
    """Build the JSON job payload from the current scene."""
    blend_path = bpy.data.filepath
    output = bpy.path.abspath(scene.render.filepath)
    return {
        "name": scene.muffin_job_name or bpy.path.display_name_from_filepath(blend_path) or "blender_job",
        "dcc": "blender",
        "renderer": scene.render.engine,           # e.g. CYCLES / BLENDER_EEVEE_NEXT
        "scene_path": blend_path,
        "output_path": output,
        "frame_start": scene.frame_start,
        "frame_end": scene.frame_end,
        "frame_step": scene.frame_step,
        "chunk_size": max(1, scene.muffin_chunk_size),
        "priority": _PRIORITY_VALUES.get(scene.muffin_priority, 50),
        "extra": {},
        "submitter": _submitter(),
    }


def _layer_expr(layer_name: str) -> str:
    """Python expression run on the worker: render only `layer_name`.
    use_single_layer must be cleared, otherwise Blender ignores the
    per-layer flags and renders only the file's active layer."""
    return ("import bpy; sc = bpy.context.scene; "
            "sc.render.use_single_layer = False; "
            "[setattr(vl, 'use', vl.name == %r) for vl in sc.view_layers]"
            % layer_name)


def _sub_output(output_path: str, sub_name: str) -> str:
    """Put each layer's/scene's frames in their own subfolder so they don't clash."""
    if not output_path:
        return output_path
    import os
    d, base = os.path.split(output_path)
    return os.path.join(d, sub_name, base)


def _scene_payloads(base: dict, scene_names: list) -> list:
    """One job per scene; more than one scene shares a batch id so the Monitor
    shows them as ONE job whose entries are the scenes. Each scene keeps its
    own frame range, engine and output; '-S <name>' selects it."""
    import uuid
    batch = f"{base['name']}::{uuid.uuid4().hex[:8]}" if len(scene_names) > 1 else ""
    payloads = []
    for name in scene_names:
        sc = bpy.data.scenes[name]
        p = dict(base)
        p["name"] = f"{base['name']} - {sc.name}"
        p["batch"] = batch
        out = bpy.path.abspath(sc.render.filepath) or base["output_path"]
        out = _sub_output(out, sc.name)
        p["output_path"] = out
        # Engine is applied via "-E" AFTER the -S scene switch (the args
        # below); the renderer field itself is display info for the Monitor.
        p["renderer"] = sc.render.engine
        p["frame_start"] = sc.frame_start
        p["frame_end"] = sc.frame_end
        p["frame_step"] = sc.frame_step
        p["extra"] = {"args": ["-S", sc.name, "-o", out, "-E", sc.render.engine]}
        payloads.append(p)
    return payloads


def _post_jobs(op, payloads: list):
    """Shared submit path: validates, posts every payload, pops the result
    dialog. Returns the operator status set."""
    if not bpy.data.filepath:
        op.report({"ERROR"}, "Save the .blend file first (workers need a path to open).")
        return {"CANCELLED"}
    manager_url = _prefs().manager_url.strip()
    if not manager_url:
        op.report({"ERROR"}, "Set the Manager URL in the add-on preferences first.")
        return {"CANCELLED"}
    url = manager_url.rstrip("/") + "/api/jobs"

    submitted = []
    for payload in payloads:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                submitted.append(json.loads(resp.read().decode("utf-8")))
        except urllib.error.URLError as exc:
            op.report({"ERROR"}, f"Could not reach manager at {manager_url}: {exc}")
            bpy.ops.muffin.error(
                "INVOKE_DEFAULT", message=f"Is the Manager running at {manager_url}?")
            return {"CANCELLED"}
        except Exception as exc:
            op.report({"ERROR"}, f"Submit failed: {exc}")
            bpy.ops.muffin.error("INVOKE_DEFAULT", message=str(exc)[:80])
            return {"CANCELLED"}

    names = ", ".join(j.get("name", "?") for j in submitted)
    op.report({"INFO"}, f"Submitted {len(submitted)} job(s): {names}")
    bpy.ops.muffin.submit_done(
        "INVOKE_DEFAULT",
        message=f"Submitted {len(submitted)} job(s)!" if len(submitted) > 1 else "Submitted!")
    return {"FINISHED"}


class MUFFIN_OT_submit(bpy.types.Operator):
    bl_idname = "muffin.submit"
    bl_label = "Muffin!!!!!!!!!"
    bl_description = "Send this scene to the Muffin render farm"

    def _build_payloads(self, context) -> list:
        scene = context.scene
        base = _submit_payload(scene)

        if scene.muffin_layers == "SCENES" and len(bpy.data.scenes) > 1:
            return _scene_payloads(base, [sc.name for sc in bpy.data.scenes])

        if len(scene.view_layers) > 1:
            # Current layer only — restrict the render to it on the worker.
            vl = context.view_layer
            p = dict(base)
            p["name"] = f"{base['name']} - {vl.name}"
            p["extra"] = {"args": ["--python-expr", _layer_expr(vl.name)]}
            return [p]

        return [base]

    def execute(self, context):
        if context.scene.muffin_layers == "CHOOSE":
            # Hand over to the scene-picker dialog.
            bpy.ops.muffin.submit_scenes("INVOKE_DEFAULT")
            return {"FINISHED"}
        return _post_jobs(self, self._build_payloads(context))


class MuffinSceneItem(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty()
    selected: bpy.props.BoolProperty(default=True)


class MUFFIN_OT_submit_scenes(bpy.types.Operator):
    """Pick exactly which scenes of this file to render."""
    bl_idname = "muffin.submit_scenes"
    bl_label = "Muffin — choose scenes"
    bl_description = "Pick which scenes to submit to the farm"

    scenes: bpy.props.CollectionProperty(type=MuffinSceneItem)

    def invoke(self, context, event):
        self.scenes.clear()
        for sc in bpy.data.scenes:
            it = self.scenes.add()
            it.name = sc.name
            it.selected = True
        return context.window_manager.invoke_props_dialog(
            self, width=300, confirm_text="Muffin!!!!!!!!!")

    def draw(self, context):
        col = self.layout.column()
        col.label(text="Select the scenes to render:", icon="SCENE_DATA")
        for it in self.scenes:
            sc = bpy.data.scenes.get(it.name)
            frames = f"  ({sc.frame_start}-{sc.frame_end})" if sc else ""
            col.prop(it, "selected", text=f"{it.name}{frames}")

    def execute(self, context):
        chosen = [it.name for it in self.scenes if it.selected]
        if not chosen:
            self.report({"ERROR"}, "No scenes selected.")
            return {"CANCELLED"}
        base = _submit_payload(context.scene)
        return _post_jobs(self, _scene_payloads(base, chosen))


class MUFFIN_OT_error(bpy.types.Operator):
    """Warning dialog when a submit fails (e.g. the manager is offline)."""
    bl_idname = "muffin.error"
    bl_label = "Muffin"
    bl_options = {"INTERNAL"}

    message: bpy.props.StringProperty(default="")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(
            self, width=360, confirm_text="OK")

    def draw(self, context):
        col = self.layout.column()
        col.scale_y = 1.3
        col.label(text="Submit failed — could not reach the Muffin Manager!",
                  icon="ERROR")
        if self.message:
            col.label(text=self.message)

    def execute(self, context):
        return {"FINISHED"}


class MUFFIN_OT_submit_done(bpy.types.Operator):
    """Confirmation dialog after a successful submit. Centered dialog so OK
    actually closes it (Blender's at-cursor popups can't be closed by a
    button; its centered dialogs always pair OK with Cancel — both close)."""
    bl_idname = "muffin.submit_done"
    bl_label = "Muffin"
    bl_options = {"INTERNAL"}

    message: bpy.props.StringProperty(default="Submitted!")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(
            self, width=240, confirm_text="OK")

    def draw(self, context):
        col = self.layout.column()
        col.scale_y = 1.4
        col.label(text=self.message, icon="CHECKMARK")

    def execute(self, context):
        return {"FINISHED"}


class MUFFIN_PT_panel(bpy.types.Panel):
    bl_label = "Muffin Submitter"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "output"

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        layout.prop(scene, "muffin_job_name")
        row = layout.row()
        row.prop(scene, "muffin_chunk_size")
        row.prop(scene, "muffin_priority")
        layout.prop(scene, "muffin_layers")
        layout.operator("muffin.submit", icon="RENDER_ANIMATION")


class MuffinAddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    manager_url: bpy.props.StringProperty(
        name="Manager URL",
        description="Base URL of your Muffin manager",
        default="http://127.0.0.1:8080",
    )

    def draw(self, context):
        self.layout.prop(self, "manager_url")


# --- auto-fill the job name with the .blend's file name ---------------------
@persistent
def _autofill_job_name(*args) -> None:
    """Fill the job name from the working file name, but never overwrite a name
    the user has already typed."""
    if not hasattr(bpy.data, "filepath"):  # restricted context (add-on loading)
        return
    name = bpy.path.display_name_from_filepath(bpy.data.filepath)
    if not name:
        return
    for sc in bpy.data.scenes:
        if not sc.muffin_job_name:
            sc.muffin_job_name = name


# --- scene properties -------------------------------------------------------
def _register_props():
    bpy.types.Scene.muffin_job_name = bpy.props.StringProperty(
        name="Job name", default="",
        description="Defaults to the .blend file name; edit to rename")
    bpy.types.Scene.muffin_chunk_size = bpy.props.IntProperty(
        name="Frames / task", default=5, min=1, soft_max=50)
    bpy.types.Scene.muffin_priority = bpy.props.EnumProperty(
        name="Priority", items=_PRIORITY_ITEMS, default="NORMAL",
        description="How urgently the farm should run this job")
    bpy.types.Scene.muffin_layers = bpy.props.EnumProperty(
        name="Submit",
        items=[
            ("CURRENT", "Current scene", "Render only the active scene/view layer"),
            ("SCENES", "All scenes",
             "Submit every scene in this file as one job (scenes become tasks)"),
            ("CHOOSE", "Choose scenes…",
             "Pick exactly which scenes to submit (a dialog opens on submit)"),
        ],
        default="CURRENT")


def _unregister_props():
    for p in ("muffin_job_name", "muffin_chunk_size", "muffin_priority", "muffin_layers"):
        if hasattr(bpy.types.Scene, p):
            delattr(bpy.types.Scene, p)


_CLASSES = (MuffinAddonPreferences, MuffinSceneItem, MUFFIN_OT_submit,
            MUFFIN_OT_submit_scenes, MUFFIN_OT_error,
            MUFFIN_OT_submit_done, MUFFIN_PT_panel)


def _autofill_deferred():
    """Timer callback for the initial fill. At register() time bpy.data is
    restricted (no .filepath), so the first fill must be deferred."""
    _autofill_job_name()
    return None  # run once


def register():
    _register_props()
    for c in _CLASSES:
        bpy.utils.register_class(c)
    bpy.app.handlers.load_post.append(_autofill_job_name)
    bpy.app.handlers.save_post.append(_autofill_job_name)
    bpy.app.timers.register(_autofill_deferred, first_interval=0.2)


def unregister():
    for h in (bpy.app.handlers.load_post, bpy.app.handlers.save_post):
        if _autofill_job_name in h:
            h.remove(_autofill_job_name)
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)
    _unregister_props()


if __name__ == "__main__":
    register()
