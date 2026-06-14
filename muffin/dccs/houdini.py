"""Houdini plugin.

Two common paths:
  * Karma/USD via `husk`:   husk -R BRAY_HdKarma -f START -n COUNT -o out.exr scene.usd
  * Mantra/ROP via `hython` driving a render script.

This default uses husk (the modern USD render delegate). For ROP-based renders,
pass extra = {"mode": "hython", "rop": "/out/mantra1"} and we fall back to a
hython one-liner that cooks the ROP for the frame range."""

import os
import re
from typing import Any, Optional

from .base import DCC

_PCT = re.compile(r"(\d{1,3})%")
_HUSK_FRAME = re.compile(r"[Ff]rame\s+(\d+)")


class HoudiniDCC(DCC):
    name = "houdini"
    renderers = ["karma", "mantra", "redshift", "arnold"]
    executable = "husk"

    def build_command(self, task: dict[str, Any]) -> list[str]:
        extra = task.get("extra", {})
        count = task["frame_end"] - task["frame_start"] + 1

        if extra.get("mode") == "hython":
            rop = extra.get("rop", "/out/mantra1")
            script = (
                "import hou,sys; hou.hipFile.load(sys.argv[1]); "
                "r=hou.node(sys.argv[2]); "
                "r.render(frame_range=(%d,%d,%d))"
                % (task["frame_start"], task["frame_end"], task.get("frame_step", 1))
            )
            from .. import settings
            hython = (os.environ.get("MUFFIN_HYTHON_EXE")
                      or settings.dcc_path("hython") or "hython")
            return [hython, "-c", script, task["scene_path"], rop]

        # Default: husk on a USD file.
        cmd = [self.exe(), "-f", str(task["frame_start"]), "-n", str(count)]
        if task.get("frame_step", 1) != 1:
            cmd += ["-i", str(task["frame_step"])]
        if task.get("renderer"):
            cmd += ["-R", task["renderer"]]
        if task.get("output_path"):
            cmd += ["-o", task["output_path"]]
        cmd += list(extra.get("args", []))
        cmd += [task["scene_path"]]
        return cmd

    def parse_progress(self, line: str, task: dict[str, Any]) -> Optional[float]:
        fm = _HUSK_FRAME.search(line)
        if fm:
            # Frame N is starting — count completed frames only.
            return self._frame_progress(int(fm.group(1)) - 1,
                                        task["frame_start"], task["frame_end"])
        pm = _PCT.search(line)
        if pm:
            return min(1.0, int(pm.group(1)) / 100.0)
        return None
