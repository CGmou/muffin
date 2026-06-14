"""The worker agent. Registers with the manager, heartbeats, asks for work, runs
it, and reports results. One task at a time.

Run with:  python -m muffin.worker
"""

import platform
import socket
import threading
import time

import requests

from .. import config
from .runner import TaskRunner


class Agent:
    def __init__(self) -> None:
        import os

        from .. import settings
        s = settings.load()

        # Manager URL / name / capabilities: explicit env wins, then the GUI
        # settings file, then sensible defaults.
        self.base = (os.environ.get("MUFFIN_MANAGER_URL")
                     or s.get("manager_url") or config.MANAGER_URL).rstrip("/")
        self.name = (os.environ.get("MUFFIN_WORKER_NAME")
                     or s.get("worker_name") or socket.gethostname())
        self.host = socket.gethostname()
        self.worker_id: str | None = None
        self._current_task_id: str | None = None
        self._runner = None           # the TaskRunner currently executing
        self._stop_after = False      # finish current task, then exit

        # Capabilities = which DCCs this node will accept. The common case is a
        # homogeneous farm (every PC has every DCC), so the default is an EMPTY
        # list, which means "accept any job". Only set capabilities to restrict
        # a node to specific DCCs (e.g. a GPU-only or licence-limited box).
        if "MUFFIN_CAPABILITIES" in os.environ:
            self.capabilities = config.WORKER_CAPABILITIES
        else:
            self.capabilities = [c.lower() for c in s.get("capabilities", [])]

    # ------------------------------------------------------------------ net --
    def _post(self, path: str, json: dict | None = None) -> dict | None:
        try:
            r = requests.post(f"{self.base}{path}", json=json or {}, timeout=15)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as exc:
            print(f"[worker] POST {path} failed: {exc}")
            return None

    @staticmethod
    def _machine_specs() -> dict:
        """CPU / GPU / RAM summary reported at registration (shown in the Monitor)."""
        import os
        import shutil
        import subprocess

        from ..common.sysinfo import cpu_name
        cpu = cpu_name()
        try:
            import psutil
            phys = psutil.cpu_count(logical=False)
            logical = psutil.cpu_count()
            ram = f"{psutil.virtual_memory().total / (1024 ** 3):.0f} GB"
        except ImportError:
            phys, logical, ram = None, os.cpu_count(), ""
        if phys and logical:
            cpu += f"  ({phys}C/{logical}T)"
        elif logical:
            cpu += f"  ({logical}T)"

        gpu = ""
        if shutil.which("nvidia-smi"):
            try:
                flags = 0x08000000 if platform.system() == "Windows" else 0
                out = subprocess.run(
                    ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                    capture_output=True, text=True, timeout=2, creationflags=flags)
                if out.returncode == 0:
                    gpu = ", ".join(l.strip() for l in out.stdout.splitlines() if l.strip())
            except Exception:
                pass
        return {"cpu": cpu, "gpu": gpu, "ram": ram}

    def register(self) -> bool:
        resp = self._post(
            "/api/workers/register",
            {"name": self.name, "host": self.host, "capabilities": self.capabilities,
             **self._machine_specs()},
        )
        if resp and resp.get("id"):
            self.worker_id = resp["id"]
            print(f"[worker] registered as '{self.name}' id={self.worker_id} "
                  f"caps={self.capabilities}")
            return True
        return False

    def heartbeat(self, status: str = "idle", task_id: str | None = None) -> None:
        self._post(
            f"/api/workers/{self.worker_id}/heartbeat",
            {"status": status, "current_task_id": task_id},
        )

    def request_task(self) -> dict | None:
        resp = self._post(f"/api/workers/{self.worker_id}/request-task")
        return resp.get("task") if resp else None

    # ----------------------------------------------------------------- work --
    def run_task(self, assignment: dict) -> None:
        task_id = assignment["task_id"]
        self._current_task_id = task_id
        f_start, f_end = assignment["frame_start"], assignment["frame_end"]
        job_name = assignment.get("job_name", "")
        print(f"[worker] picked up task {task_id} "
              f"(job '{job_name}', {assignment['dcc']} frames {f_start}-{f_end})")
        self._post(f"/api/tasks/{task_id}/start")

        last_pct = -1

        def report(progress: float, log: str) -> None:
            nonlocal last_pct
            pct = int(progress * 100)
            if pct != last_pct:
                last_pct = pct
                # Structured line the Muffin (worker) GUI parses for its status panel.
                n_frames = max(1, f_end - f_start + 1)
                cur = f_start + min(n_frames - 1, int(progress * n_frames))
                print(f"[worker] task {task_id} frame {cur}/{f_end} progress {pct}%")
            self._post(f"/api/tasks/{task_id}/progress",
                       {"progress": progress, "log": log})

        runner = TaskRunner(assignment, report)
        self._runner = runner
        status, code, tail = runner.run()
        self._runner = None
        self._current_task_id = None
        self._post(f"/api/tasks/{task_id}/result",
                   {"status": status, "exit_code": code, "log": tail})
        print(f"[worker] task {task_id} -> {status} (exit {code})")

    def _handle_cmd(self, cmd: str) -> None:
        if cmd == "stop_task":
            runner = self._runner
            if runner:
                print("[worker] stop_task: canceling current task")
                runner.cancel()
            else:
                print("[worker] stop_task: nothing rendering")
        elif cmd == "stop_after":
            self._stop_after = True
            print("[worker] will stop after the current render")
        elif cmd == "shutdown":
            # Immediate stop: kill the current render (it gets requeued by the
            # manager) and exit the worker loop.
            print("[worker] shutdown requested")
            self._stop_after = True
            runner = self._runner
            if runner:
                runner.cancel()

    def _cmd_file_loop(self) -> None:
        """Control channel for the Muffin GUI: it drops a command into
        DATA_DIR/worker_cmd. (Windows pipe inheritance makes stdin unreliable
        while a render subprocess is running, so a file is used instead.)"""
        path = config.DATA_DIR / "worker_cmd"
        while True:
            time.sleep(0.5)
            try:
                if path.exists():
                    cmd = path.read_text(encoding="utf-8").strip()
                    path.unlink(missing_ok=True)
                    if cmd:
                        self._handle_cmd(cmd)
            except Exception:
                pass

    def _heartbeat_loop(self) -> None:
        """Background heartbeat so long renders don't get the worker marked
        offline (the main loop is blocked inside run_task while rendering)."""
        while True:
            time.sleep(config.HEARTBEAT_INTERVAL)
            try:
                tid = self._current_task_id
                if self.worker_id:
                    self.heartbeat("busy" if tid else "idle", tid)
            except Exception:
                pass

    # ----------------------------------------------------------------- loop --
    def run_forever(self) -> None:
        print(f"[worker] connecting to manager at {self.base} "
              f"({platform.system()} {platform.release()})")
        while not self.register():
            print("[worker] could not reach manager, retrying in 5s...")
            time.sleep(5)

        # Clear any stale command left over from a previous session.
        try:
            (config.DATA_DIR / "worker_cmd").unlink(missing_ok=True)
        except OSError:
            pass
        # NOTE: no stdin-based control — a blocking console read in a thread
        # freezes the whole process on Windows. The command file is the channel.
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        threading.Thread(target=self._cmd_file_loop, daemon=True).start()

        while True:
            try:
                if self._stop_after:
                    print("[worker] stopped (after-render request)")
                    break
                task = self.request_task()
                if task:
                    self.run_task(task)
                    self.heartbeat("idle", None)
                else:
                    self.heartbeat("idle", None)
                    # Sleep in small steps so a shutdown request is noticed
                    # promptly even while idle.
                    waited = 0.0
                    while waited < config.HEARTBEAT_INTERVAL and not self._stop_after:
                        time.sleep(0.5)
                        waited += 0.5
            except KeyboardInterrupt:
                print("\n[worker] shutting down")
                break
            except Exception as exc:
                print(f"[worker] loop error: {exc}; retrying")
                # If the manager forgot us (restart), re-register.
                self.register()
                time.sleep(config.HEARTBEAT_INTERVAL)
