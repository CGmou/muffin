"""Base class for DCC plugins.

A DCC plugin knows three things:
  1. how to build the command line that renders a chunk of frames,
  2. how to read a line of that process's stdout and turn it into progress,
  3. (optional) whether it is actually installed on this machine.

Each plugin advertises the renderers it supports. The worker imports the plugin
matching a task's `dcc` field and calls build_command().
"""

import os
import shutil
from typing import Any, Optional


class DCC:
    name: str = "base"
    # Renderers this DCC can drive. First entry is the default.
    renderers: list[str] = []
    # Executable looked up on PATH to decide if the DCC is installed.
    executable: str = ""

    def build_command(self, task: dict[str, Any]) -> list[str]:
        """Return argv for rendering frames [frame_start..frame_end] of `task`.

        `task` carries: scene_path, output_path, renderer, frame_start,
        frame_end, frame_step, extra (renderer-specific dict)."""
        raise NotImplementedError

    def parse_progress(self, line: str, task: dict[str, Any]) -> Optional[float]:
        """Given one line of stdout, return a 0..1 progress estimate, or None."""
        return None

    def build_env(self, task: dict[str, Any]) -> Optional[dict]:
        """Environment for the render subprocess, or None to inherit the
        worker's unchanged. Most DCCs inherit; kick prepends Maya bin/plugins
        to PATH so it can find Arnold's libraries."""
        return None

    def exe(self) -> str:
        """The executable to launch. Resolution order: env override
        MUFFIN_<NAME>_EXE  >  the GUI settings file  >  the bare name on PATH.
        Lets each worker point at a full install path when the DCC isn't on PATH."""
        if not self.executable:
            return ""
        env = os.environ.get(f"MUFFIN_{self.name.upper()}_EXE")
        if env:
            return env
        from .. import settings  # local import avoids an import cycle
        configured = settings.dcc_path(self.name)
        return configured or self.executable

    def is_installed(self) -> bool:
        if not self.executable:
            return True
        exe = self.exe()
        # An absolute/explicit path counts as installed if it exists on disk;
        # a bare name is resolved against PATH.
        if os.path.isabs(exe) or os.sep in exe or (os.altsep and os.altsep in exe):
            return os.path.isfile(exe)
        return shutil.which(exe) is not None

    # -- helpers shared by subclasses --
    @staticmethod
    def _frame_progress(line_frame: int, start: int, end: int) -> float:
        span = max(1, end - start + 1)
        return min(1.0, max(0.0, (line_frame - start + 1) / span))
