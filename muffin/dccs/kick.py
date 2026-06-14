"""Arnold standalone (kick) renderer: renders pre-exported .ass scenes with
kick.exe — no Maya licence needed on the worker. The Maya submitter exports the
scene to one .ass per frame and submits a 'kick' job; each task renders a single
.ass file.

The command mirrors the usual studio .bat:
    set PATH=<maya bin>;<plugins>;%PATH%
    kick.exe -i scene.0001.ass -l <procedurals> -nokeypress

The Maya-bin, plugins, procedurals and kick.exe paths are configured per worker
in Node ▸ Settings — they live where the render runs, not where it's submitted.
"""

import os
import re
from typing import Any, Optional

from .base import DCC

_PCT = re.compile(r"(\d{1,3})%")


def _kick_paths() -> dict:
    from .. import settings  # local import avoids an import cycle
    return settings.load().get("kick", {}) or {}


class KickDCC(DCC):
    name = "kick"
    renderers = ["arnold"]
    executable = "kick"  # kick.exe — Arnold's standalone renderer

    def build_command(self, task: dict[str, Any]) -> list[str]:
        ass = self._ass_for_frame(task)
        # Arnold's canonical headless batch flags: -dp disable progressive,
        # -dw no render-view window, -nokeypress never wait for a key.
        cmd = [self.exe(), "-i", ass, "-dp", "-dw", "-nokeypress"]
        procedurals = _kick_paths().get("procedurals", "")
        if procedurals:
            cmd += ["-l", procedurals]
        cmd += list(task.get("extra", {}).get("args", []))
        return cmd

    def build_env(self, task: dict[str, Any]) -> Optional[dict]:
        """Prepend the Maya-bin + XGen dirs to PATH so kick finds Arnold's core
        libraries and the XGen plug-in (the .bat's `set PATH=…` step)."""
        paths = _kick_paths()
        dirs = [d for d in (paths.get("maya_bin", ""), paths.get("xgen", "")) if d]
        if not dirs:
            return None  # nothing configured — inherit the worker's PATH as-is
        env = dict(os.environ)
        env["PATH"] = os.pathsep.join(dirs + [env.get("PATH", "")])
        return env

    @staticmethod
    def _ass_for_frame(task: dict[str, Any]) -> str:
        """Resolve this task's .ass file from the '####' frame pattern the Maya
        submitter recorded (e.g. .../scene.####.ass -> .../scene.0007.ass)."""
        pattern = task.get("extra", {}).get("ass_pattern") or task.get("scene_path", "")
        frame = task.get("frame_start", 0)
        m = re.search(r"#+", pattern)
        if m:
            return pattern[:m.start()] + str(frame).zfill(len(m.group(0))) + pattern[m.end():]
        return pattern

    def parse_progress(self, line: str, task: dict[str, Any]) -> Optional[float]:
        pm = _PCT.search(line)
        if pm:
            return min(1.0, int(pm.group(1)) / 100.0)
        return None
