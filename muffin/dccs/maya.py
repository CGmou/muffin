"""Maya plugin using the standalone `Render` command-line renderer.

    Render -r arnold -s START -e END -b STEP -rd <outdir> scene.mb

NOTE: Studio setups vary a lot (project dirs, render layers, OCIO, plugins). The
command below is a sensible default; pass overrides via the job's `extra.args`
list, e.g. extra = {"args": ["-proj", "X:/show/shot"]}."""

import re
from typing import Any, Optional

from .base import DCC

# Arnold/VRay print progress as a percentage at various points.
_PCT = re.compile(r"(\d{1,3})%")
_FRAME = re.compile(r"[Ff]rame[:\s]+(\d+)")


class MayaDCC(DCC):
    name = "maya"
    renderers = ["arnold", "vray", "redshift", "sw", "hw2"]
    executable = "Render"

    def build_command(self, task: dict[str, Any]) -> list[str]:
        extra = task.get("extra", {})
        cmd = [self.exe()]
        if task.get("renderer"):
            cmd += ["-r", task["renderer"]]
        # "-s/-e" force Maya into animation mode, which appends frame numbers
        # to the output ("name.ext.0001"). Single-frame jobs whose Render
        # Settings use "name.ext" naming must skip them. A combined multi-layer
        # render also skips them so every layer uses its own configured range.
        if not extra.get("single_frame") and not extra.get("no_frame_flags"):
            cmd += [
                "-s", str(task["frame_start"]),
                "-e", str(task["frame_end"]),
                "-b", str(task.get("frame_step", 1)),
            ]
        # The Maya submitter sets no_output_flag so the render follows the
        # scene's own Render Settings (image file prefix tokens like
        # <Scene>/<RenderLayer>) — output_path is then display info only.
        if task.get("output_path") and not extra.get("no_output_flag"):
            cmd += ["-rd", task["output_path"]]
        # Thread count (0 = all cores). The flag is renderer-specific.
        threads = extra.get("render_threads")
        if threads is not None:
            renderer = (task.get("renderer") or "").lower()
            if renderer == "arnold":
                cmd += ["-ai:threads", str(threads)]
            elif renderer == "vray":
                cmd += ["-threads", str(threads)]
            # other renderers: use their own defaults (no safe universal flag)
        cmd += list(extra.get("args", []))
        cmd += [task["scene_path"]]
        return cmd

    def parse_progress(self, line: str, task: dict[str, Any]) -> Optional[float]:
        fm = _FRAME.search(line)
        if fm:
            # Frame N is starting — count completed frames only.
            return self._frame_progress(int(fm.group(1)) - 1,
                                        task["frame_start"], task["frame_end"])
        pm = _PCT.search(line)
        if pm:
            return min(1.0, int(pm.group(1)) / 100.0)
        return None
