"""Runs a single task's render command as a subprocess, streaming stdout back to
the manager as progress + log updates. Returns the final status.

The renderer's output is drained on a dedicated thread so it NEVER blocks on the
network: a verbose renderer (Arnold/Maya) fills the OS pipe buffer in a fraction
of a second, and if the reader paused to POST to the manager the renderer would
block on its stdout write and effectively stop computing. Posting to the manager
happens on the main thread, decoupled from reading."""

import collections
import subprocess
import threading
import time
from typing import Callable

from .. import dccs

# Cap how much log text we ship per flush — the manager only keeps the last
# ~20k chars per task, and a runaway renderer can spew megabytes.
_MAX_CHUNK = 16000


class TaskRunner:
    def __init__(self, assignment: dict, report_progress: Callable[[float, str], None]):
        self.assignment = assignment
        self.report_progress = report_progress
        self._proc: subprocess.Popen | None = None
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        # Bounded so a verbose renderer + a slow manager can't grow memory:
        # we only ever ship the most recent output anyway.
        self._pending = collections.deque(maxlen=2000)  # unsent log lines
        self._tail = collections.deque(maxlen=400)      # last lines for the final result
        self._progress = 0.0
        self._plugin = None

    def cancel(self) -> None:
        self._cancel.set()
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()

    # -- reader thread: drain the pipe as fast as possible, no network here --
    def _reader(self) -> None:
        try:
            for line in self._proc.stdout:
                if self._cancel.is_set():
                    break
                prog = self._plugin.parse_progress(line.rstrip("\n"), self.assignment)
                with self._lock:
                    self._pending.append(line)
                    self._tail.append(line)
                    if prog is not None and prog > self._progress:
                        self._progress = prog
        except Exception:
            pass  # pipe closed / decode hiccup — the wait() below handles exit

    def _flush(self) -> None:
        with self._lock:
            chunk = "".join(self._pending)
            self._pending.clear()
            prog = self._progress
        if len(chunk) > _MAX_CHUNK:
            chunk = chunk[-_MAX_CHUNK:]
        # Always send progress; only include a log chunk when there is one.
        self.report_progress(prog, chunk)

    def run(self) -> tuple[str, int, str]:
        """Returns (status, exit_code, tail_log). status is 'done' or 'failed'."""
        self._plugin = dccs.get(self.assignment["dcc"])
        if self._plugin is None:
            return "failed", -1, f"[muffin] no DCC plugin for '{self.assignment['dcc']}'\n"

        try:
            cmd = self._plugin.build_command(self.assignment)
        except Exception as exc:  # bad scene/params
            return "failed", -1, f"[muffin] failed to build command: {exc}\n"

        self.report_progress(0.0, f"[muffin] running: {' '.join(map(str, cmd))}\n")

        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                # DCCs emit bytes that aren't valid in the OS default codec
                # (e.g. cp1252 on Windows). Force UTF-8 and never crash on a
                # stray byte.
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except FileNotFoundError:
            return "failed", -1, f"[muffin] executable not found: {cmd[0]}\n"
        except Exception as exc:
            return "failed", -1, f"[muffin] failed to launch: {exc}\n"

        reader = threading.Thread(target=self._reader, daemon=True)
        reader.start()

        # Post accumulated output to the manager roughly once a second. If a
        # post is slow (e.g. NAS over the network) the reader keeps draining the
        # pipe regardless, so the renderer never stalls.
        while reader.is_alive():
            reader.join(timeout=1.0)
            self._flush()
        self._flush()  # final drain

        self._proc.wait()
        code = self._proc.returncode
        with self._lock:
            tail = "".join(self._tail)

        if self._cancel.is_set():
            return "failed", code, tail + "\n[muffin] canceled\n"
        if code == 0:
            return "done", code, tail + "\n[muffin] exit 0\n"
        return "failed", code, tail + f"\n[muffin] exit {code}\n"
