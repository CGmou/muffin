"""Nuke plugin — renders a comp in terminal mode.

    nuke -ix -F START-ENDxSTEP -X <WriteNode> script.nk

`-x` executes, `-i` uses an interactive license slot if needed. Target a
specific Write node via extra = {"write_node": "Write1"}."""

import re
from typing import Any, Optional

from .base import DCC

_FRAME = re.compile(r"[Ff]rame (\d+)")


class NukeDCC(DCC):
    name = "nuke"
    renderers = ["scanline", "raytrace"]  # Nuke's internal 3D; comps usually need none
    executable = "nuke"

    def build_command(self, task: dict[str, Any]) -> list[str]:
        extra = task.get("extra", {})
        step = task.get("frame_step", 1)
        frange = f"{task['frame_start']}-{task['frame_end']}"
        if step != 1:
            frange += f"x{step}"
        cmd = [self.exe(), "-x", "-F", frange]
        if extra.get("write_node"):
            cmd += ["-X", extra["write_node"]]
        cmd += list(extra.get("args", []))
        cmd += [task["scene_path"]]
        return cmd

    def parse_progress(self, line: str, task: dict[str, Any]) -> Optional[float]:
        m = _FRAME.search(line)
        if m:
            # Frame N is starting — count completed frames only.
            return self._frame_progress(int(m.group(1)) - 1,
                                        task["frame_start"], task["frame_end"])
        return None
