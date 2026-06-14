"""A fake DCC that renders nothing but behaves like a real one: it sleeps,
prints per-frame progress, and writes a small placeholder file per frame. This
lets you exercise the entire farm end-to-end without any DCC installed."""

import sys
from typing import Any, Optional

from .base import DCC

# Runs in a subprocess via `python -c`. Prints "MUFFIN_FRAME <n>" per frame so
# parse_progress can track it, sleeps to simulate work, and writes an output file.
_SCRIPT = r"""
import os, sys, time
start, end, step, outdir = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
delay = float(sys.argv[5])
if outdir:
    os.makedirs(outdir, exist_ok=True)
for f in range(start, end + 1, step):
    print("MUFFIN_FRAME %d" % f, flush=True)
    time.sleep(delay)
    if outdir:
        with open(os.path.join(outdir, "frame_%04d.txt" % f), "w") as fh:
            fh.write("rendered frame %d\n" % f)
    print("MUFFIN_DONE %d" % f, flush=True)
print("MUFFIN_COMPLETE", flush=True)
"""


class MockDCC(DCC):
    name = "mock"
    renderers = ["mock"]
    executable = ""  # always "installed"

    def build_command(self, task: dict[str, Any]) -> list[str]:
        delay = str(task.get("extra", {}).get("frame_delay", 1.0))
        return [
            sys.executable, "-c", _SCRIPT,
            str(task["frame_start"]), str(task["frame_end"]),
            str(task.get("frame_step", 1)), task.get("output_path", ""), delay,
        ]

    def parse_progress(self, line: str, task: dict[str, Any]) -> Optional[float]:
        if line.startswith("MUFFIN_DONE "):
            try:
                frame = int(line.split()[1])
            except (IndexError, ValueError):
                return None
            return self._frame_progress(frame, task["frame_start"], task["frame_end"])
        if line.strip() == "MUFFIN_COMPLETE":
            return 1.0
        return None
