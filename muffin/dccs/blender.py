"""Blender plugin. Renders a frame range in background mode.

    blender -b scene.blend -E CYCLES -o //out_#### -s START -e END -j STEP -a

Blender prints lines like:  Fra:12 Mem:... | Time:... | Rendering 5 / 64
We track the "Fra:N" token to estimate progress across the chunk."""

import re
from typing import Any, Optional

from .base import DCC

# Blender prints "Fra:12" (older) or "Fra: 12" (5.x) — allow optional space.
_FRA = re.compile(r"\bFra:\s*(\d+)")


class BlenderDCC(DCC):
    name = "blender"
    renderers = ["CYCLES", "BLENDER_EEVEE", "BLENDER_EEVEE_NEXT", "BLENDER_WORKBENCH"]
    executable = "blender"

    def build_command(self, task: dict[str, Any]) -> list[str]:
        cmd = [self.exe(), "-b", task["scene_path"]]
        if task.get("output_path"):
            cmd += ["-o", task["output_path"]]
        if task.get("renderer"):
            cmd += ["-E", task["renderer"]]
        # Extra raw args (e.g. ["-F", "PNG"]) pass straight through.
        cmd += list(task.get("extra", {}).get("args", []))
        cmd += [
            "-s", str(task["frame_start"]),
            "-e", str(task["frame_end"]),
            "-j", str(task.get("frame_step", 1)),
            "-a",
        ]
        return cmd

    def parse_progress(self, line: str, task: dict[str, Any]) -> Optional[float]:
        m = _FRA.search(line)
        if m:
            # "Fra: N" means frame N is being rendered — count only the frames
            # already finished, so the bar shows done work (like the Monitor).
            return self._frame_progress(int(m.group(1)) - 1,
                                        task["frame_start"], task["frame_end"])
        return None
