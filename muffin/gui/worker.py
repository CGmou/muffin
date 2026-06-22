"""Muffin — a standalone GUI to run a render worker on this machine.

Two tabs:
  * Task    — what the worker is doing now (job, task, frame, progress) plus
              what it rendered last. Status big at the top, log behind a button.
  * Machine — Deadline-style node info: CPU/GPU/RAM/disk specs and live usage.

A Job Control menu can stop the current task, or stop / restart the worker —
or restart / shut down the whole PC — after the current render finishes.

Run with:  python -m muffin.gui.worker
"""

import os
import platform
import re
import shutil
import socket
import subprocess
import sys

import requests
from PySide6.QtCore import QProcess, Qt, QTimer
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import (
    QApplication, QDialog, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QTabWidget, QVBoxLayout, QWidget,
)

from .. import schedule, settings
from .notify import Notifier
from .schedule_widget import ScheduleEditor
from .settings_dialog import NodeSettingsDialog
from .style import QSS, app_icon, apply_app_icon, bring_to_front


def _worker_name() -> str:
    """This machine's worker name — the same resolution the agent uses, so the
    Worker app can find its own record on the manager."""
    return (os.environ.get("MUFFIN_WORKER_NAME")
            or settings.load().get("worker_name")
            or socket.gethostname())


def _worker_title() -> str:
    return f"Muffin - {_worker_name()}"

try:
    import psutil
except ImportError:  # machine tab degrades gracefully
    psutil = None

# "[worker] task abc123 frame 14/20 progress 45%"
_PROGRESS = re.compile(r"task (\S+) frame (\d+)/(\d+) progress (\d+)%")
# "[worker] picked up task abc123 (job 'shotA', blender frames 11-20)"
_PICKED = re.compile(r"picked up task (\S+) \(job '(.*?)', (\S+) frames (\d+)-(\d+)\)")

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _gb(n_bytes: float) -> str:
    return f"{n_bytes / (1024 ** 3):.1f} GB"


def _nvidia_smi(query: str) -> list[str]:
    """One line per GPU, or [] when nvidia-smi isn't available."""
    if not shutil.which("nvidia-smi"):
        return []
    try:
        out = subprocess.run(
            ["nvidia-smi", f"--query-gpu={query}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2, creationflags=_NO_WINDOW)
        if out.returncode == 0:
            return [l.strip() for l in out.stdout.splitlines() if l.strip()]
    except Exception:
        pass
    return []


class LogDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Muffin — worker log")
        self.resize(760, 480)
        lay = QVBoxLayout(self)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QFont("Consolas", 9))
        self.log.setMaximumBlockCount(5000)
        lay.addWidget(self.log)


class RenderScheduleDialog(QDialog):
    """Let the artist set THIS machine's render schedule from the Worker app —
    no need to open the Monitor. It's the same schedule the manager stores, found
    by this worker's name, so the Monitor and this dialog stay in sync."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Render schedule — this machine")
        self.setWindowIcon(app_icon())
        self.resize(640, 540)
        self.base = settings.load().get("manager_url", "").rstrip("/")
        self.name = _worker_name()
        self._worker_id = None

        lay = QVBoxLayout(self)
        intro = QLabel(
            "Choose when this PC is free to render. Outside these windows Muffin "
            "won't start a render here, and a render already running is stopped so "
            "the machine is yours. Saved on the farm manager — the studio's "
            "Monitor sees the same schedule.")
        intro.setObjectName("hdr")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        self.editor = ScheduleEditor()
        lay.addWidget(self.editor)

        bar = QHBoxLayout()
        self.status = QLabel("")
        self.status.setObjectName("hdr")
        bar.addWidget(self.status, 1)
        self.save_btn = QPushButton("Save")
        self.save_btn.setObjectName("primary")
        self.save_btn.clicked.connect(self._save)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        bar.addWidget(self.save_btn)
        bar.addWidget(close)
        lay.addLayout(bar)

        self._load()

    def _disable(self, msg: str) -> None:
        self.editor.setEnabled(False)
        self.save_btn.setEnabled(False)
        self.status.setText(msg)

    def _load(self) -> None:
        if not self.base:
            self._disable("No manager URL set — open Settings first.")
            return
        try:
            workers = requests.get(f"{self.base}/api/workers", timeout=5).json()
        except Exception as exc:
            self._disable(f"Can't reach the manager: {exc}")
            return
        w = next((x for x in workers if x.get("name") == self.name), None)
        if not w:
            self._disable("This machine isn't registered yet — start the worker "
                          "once, then set its schedule.")
            return
        if "standby" not in w:
            self._disable("The manager is out of date — restart it to enable "
                          "schedules.")
            return
        self._worker_id = w["id"]
        self.editor.set_schedule(bool(w.get("schedule_enabled")),
                                 w.get("schedule") or schedule.nights_and_weekends())

    def _save(self) -> None:
        if not self._worker_id:
            return
        payload = {"schedule_enabled": self.editor.get_enabled(),
                   "schedule": self.editor.get_schedule()}
        try:
            requests.put(f"{self.base}/api/workers/{self._worker_id}",
                         json=payload, timeout=5).raise_for_status()
        except Exception as exc:
            QMessageBox.warning(self, "Error", str(exc))
            return
        self.status.setText("Saved — this machine's render schedule is updated.")


# ----------------------------------------------------------- machine tab ------
class MachineTab(QWidget):
    """PC spec + live CPU/GPU/RAM/disk usage, refreshed every 2s."""

    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)

        spec = QGroupBox("System")
        sg = QGridLayout(spec)
        sg.setColumnStretch(1, 1)
        from ..common.sysinfo import cpu_name
        rows = [
            ("Machine", socket.gethostname()),
            ("OS", platform.platform()),
            ("CPU", cpu_name()),
            ("Cores", f"{psutil.cpu_count(logical=False) or '?'} physical / "
                      f"{psutil.cpu_count() or '?'} logical" if psutil else "—"),
            ("RAM", _gb(psutil.virtual_memory().total) if psutil else "—"),
            ("GPU", ", ".join(_nvidia_smi("name")) or "—"),
        ]
        for i, (k, v) in enumerate(rows):
            key = QLabel(k)
            key.setObjectName("hdr")
            sg.addWidget(key, i, 0)
            val = QLabel(str(v))
            val.setWordWrap(True)
            sg.addWidget(val, i, 1)
        lay.addWidget(spec)

        usage = QGroupBox("Usage")
        ug = QGridLayout(usage)
        ug.setColumnStretch(1, 1)
        self.cpu_bar = self._bar()
        self.ram_bar = self._bar()
        self.gpu_bar = self._bar()
        self.vram_bar = self._bar()
        for i, (label, bar) in enumerate(
                [("CPU", self.cpu_bar), ("RAM", self.ram_bar),
                 ("GPU", self.gpu_bar), ("VRAM", self.vram_bar)]):
            key = QLabel(label)
            key.setObjectName("hdr")
            ug.addWidget(key, i, 0)
            ug.addWidget(bar, i, 1)
        lay.addWidget(usage)

        disks = QGroupBox("Disks")
        self.disk_grid = QGridLayout(disks)
        self.disk_grid.setColumnStretch(1, 1)
        self.disk_bars: dict[str, tuple[QProgressBar, QLabel]] = {}
        if psutil:
            row = 0
            for part in psutil.disk_partitions(all=False):
                try:
                    du = psutil.disk_usage(part.mountpoint)
                except OSError:
                    continue
                key = QLabel(part.device or part.mountpoint)
                key.setObjectName("hdr")
                self.disk_grid.addWidget(key, row, 0)
                bar = self._bar()
                self.disk_grid.addWidget(bar, row, 1)
                info = QLabel("")
                info.setObjectName("hdr")
                self.disk_grid.addWidget(info, row, 2)
                self.disk_bars[part.mountpoint] = (bar, info)
                row += 1
        else:
            self.disk_grid.addWidget(
                QLabel("Install psutil for live stats:  pip install psutil"), 0, 0)
        lay.addWidget(disks)
        lay.addStretch()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._update)
        self.timer.start(2000)
        self._update()

    def _bar(self) -> QProgressBar:
        b = QProgressBar()
        b.setValue(0)
        return b

    def _update(self) -> None:
        if psutil:
            self.cpu_bar.setValue(int(psutil.cpu_percent()))
            self.ram_bar.setValue(int(psutil.virtual_memory().percent))
            for mount, (bar, info) in self.disk_bars.items():
                try:
                    du = psutil.disk_usage(mount)
                    bar.setValue(int(du.percent))
                    info.setText(f"{_gb(du.free)} free of {_gb(du.total)}")
                except OSError:
                    pass
        gpu = _nvidia_smi("utilization.gpu,memory.used,memory.total")
        if gpu:
            try:
                util, used, total = [float(x) for x in gpu[0].split(",")]
                self.gpu_bar.setValue(int(util))
                self.vram_bar.setValue(int(used * 100 / max(1.0, total)))
            except (ValueError, IndexError):
                pass


# -------------------------------------------------------------- main window ---
class WorkerWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(_worker_title())
        self.setWindowIcon(app_icon())
        self.resize(480, 560)
        self.proc: QProcess | None = None
        # What to do once the worker exits after a "stop after render":
        # None | "restart" | "reboot" | "shutdown"
        self.pending_action: str | None = None
        self.log_dialog = LogDialog(self)
        self.notifier = Notifier(_worker_title(), self)
        self.notifier.enabled = settings.load().get("worker_notify", True)
        self._build_menu()
        self._build()
        self._refresh_target()
        self._reset_task_panel()
        # Start rendering as soon as the app opens (Settings ▸ Start worker
        # automatically). The user can still Stop it from the button/menu.
        if settings.load().get("worker_autostart", True):
            QTimer.singleShot(0, self._toggle)

    # ----------------------------------------------------------------- menus --
    def _build_menu(self) -> None:
        m = self.menuBar().addMenu("Settings")
        act = QAction("Settings…", self)
        act.triggered.connect(self._open_settings)
        m.addAction(act)
        act_sched = QAction("Render schedule…", self)
        act_sched.triggered.connect(self._open_schedule)
        m.addAction(act_sched)
        m.addSeparator()
        self.act_autostart = QAction("Start worker automatically on launch", self)
        self.act_autostart.setCheckable(True)
        self.act_autostart.setChecked(settings.load().get("worker_autostart", True))
        self.act_autostart.toggled.connect(self._toggle_autostart)
        m.addAction(self.act_autostart)
        self.act_notify = QAction("Notify when a render finishes or fails", self)
        self.act_notify.setCheckable(True)
        self.act_notify.setChecked(self.notifier.enabled)
        self.act_notify.toggled.connect(self._toggle_notify)
        m.addAction(self.act_notify)

        jc = self.menuBar().addMenu("Job Control")
        self.act_stop_task = QAction("Stop current task", self)
        self.act_stop_task.triggered.connect(lambda: self._send_cmd("stop_task"))
        jc.addAction(self.act_stop_task)
        jc.addSeparator()
        self.act_stop_after = QAction("Stop worker after current render", self)
        self.act_stop_after.triggered.connect(lambda: self._after_render(None))
        jc.addAction(self.act_stop_after)
        self.act_restart_after = QAction("Restart worker after current render", self)
        self.act_restart_after.triggered.connect(lambda: self._after_render("restart"))
        jc.addAction(self.act_restart_after)
        jc.addSeparator()
        self.act_reboot_after = QAction("Restart PC after current render", self)
        self.act_reboot_after.triggered.connect(lambda: self._after_render("reboot"))
        jc.addAction(self.act_reboot_after)
        self.act_shutdown_after = QAction("Shut down PC after current render", self)
        self.act_shutdown_after.triggered.connect(lambda: self._after_render("shutdown"))
        jc.addAction(self.act_shutdown_after)

    def _open_settings(self) -> None:
        if NodeSettingsDialog(self).exec():
            self._refresh_target()

    def _open_schedule(self) -> None:
        RenderScheduleDialog(self).exec()

    def _toggle_autostart(self, on: bool) -> None:
        s = settings.load()
        s["worker_autostart"] = on
        settings.save(s)

    def _toggle_notify(self, on: bool) -> None:
        self.notifier.enabled = on
        s = settings.load()
        s["worker_notify"] = on
        settings.save(s)

    def _send_cmd(self, cmd: str) -> bool:
        if not (self.proc and self.proc.state() != QProcess.NotRunning):
            QMessageBox.information(self, "Muffin", "The worker is not running.")
            return False
        # Command file, not stdin: Windows pipe inheritance makes stdin writes
        # unreliable while the worker has a render subprocess running.
        from .. import config
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        (config.DATA_DIR / "worker_cmd").write_text(cmd, encoding="utf-8")
        return True

    def _after_render(self, action: str | None) -> None:
        label = {
            None: "stop the worker",
            "restart": "restart the worker",
            "reboot": "RESTART THIS PC",
            "shutdown": "SHUT DOWN THIS PC",
        }[action]
        if action in ("reboot", "shutdown"):
            if QMessageBox.question(
                    self, "Muffin",
                    f"This will {label} after the current render finishes.\nContinue?"
            ) != QMessageBox.Yes:
                return
        if self._send_cmd("stop_after"):
            self.pending_action = action
            self._set_state(f"will {label.lower()} after this render", "#d9b04a")

    # -------------------------------------------------------------------- ui --
    def _build(self) -> None:
        tabs = QTabWidget()
        tabs.addTab(self._task_tab(), "Task")
        self.machine_tab = MachineTab()
        tabs.addTab(self.machine_tab, "Machine Information")
        self.setCentralWidget(tabs)

        # Refresh button on the right of the menu bar: re-reads settings and
        # updates the machine stats immediately.
        from PySide6.QtWidgets import QToolButton
        self.refresh_btn = QToolButton(self.menuBar())
        self.refresh_btn.setText("⟳ Refresh")
        self.refresh_btn.clicked.connect(self._refresh_all)
        self.menuBar().setCornerWidget(self.refresh_btn, Qt.TopRightCorner)

    def _refresh_all(self) -> None:
        self._refresh_target()
        self.machine_tab._update()

    def _task_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        self.machine_lbl = QLabel(socket.gethostname())
        self.machine_lbl.setStyleSheet("color:#8a91a0; font-size:13px; font-weight:bold")
        lay.addWidget(self.machine_lbl)

        self.state = QLabel("● stopped")
        self.state.setStyleSheet("color:#8a91a0; font-size:26px; font-weight:bold")
        lay.addWidget(self.state)

        self.target = QLabel("")
        self.target.setObjectName("hdr")
        lay.addWidget(self.target)

        box = QGroupBox("Current task")
        grid = QGridLayout(box)
        grid.setColumnStretch(1, 1)

        def hdr(text: str) -> QLabel:
            l = QLabel(text)
            l.setObjectName("hdr")
            return l

        self.job_lbl = QLabel("—")
        self.task_lbl = QLabel("—")
        self.dcc_lbl = QLabel("—")
        self.frames_lbl = QLabel("—")
        self.frame_lbl = QLabel("—")
        self.last_job_lbl = QLabel("—")
        self.last_task_lbl = QLabel("—")

        rows = [
            ("Job", self.job_lbl),
            ("Task", self.task_lbl),
            ("DCC", self.dcc_lbl),
            ("Frames", self.frames_lbl),
            ("Rendering frame", self.frame_lbl),
        ]
        for i, (label, widget) in enumerate(rows):
            grid.addWidget(hdr(label), i, 0)
            grid.addWidget(widget, i, 1)

        self.progress = QProgressBar()
        self.progress.setValue(0)
        grid.addWidget(self.progress, len(rows), 0, 1, 2)

        grid.addWidget(hdr("Last job"), len(rows) + 1, 0)
        grid.addWidget(self.last_job_lbl, len(rows) + 1, 1)
        grid.addWidget(hdr("Last task"), len(rows) + 2, 0)
        grid.addWidget(self.last_task_lbl, len(rows) + 2, 1)
        lay.addWidget(box)

        self.btn = QPushButton("Start worker")
        self.btn.setObjectName("primary")
        self.btn.clicked.connect(self._toggle)
        lay.addWidget(self.btn)

        lay.addStretch()

        log_btn = QPushButton("Log…")
        log_btn.clicked.connect(self.log_dialog.show)
        lay.addWidget(log_btn)
        return w

    def _refresh_target(self) -> None:
        s = settings.load()
        self.target.setText(f"Manager: {s.get('manager_url')}")

    def _reset_task_panel(self) -> None:
        for lbl in (self.job_lbl, self.task_lbl, self.dcc_lbl,
                    self.frames_lbl, self.frame_lbl):
            lbl.setText("—")
        self.progress.setValue(0)

    def _finish_task(self, result: str, color: str) -> None:
        """Move the current task into the 'last' slots and clear the panel."""
        if self.job_lbl.text() != "—":
            self.last_job_lbl.setText(self.job_lbl.text())
            self.last_task_lbl.setText(f"{self.task_lbl.text()}  ({result})")
            self.last_task_lbl.setStyleSheet(f"color:{color}")
        self._reset_task_panel()

    # ----------------------------------------------------------- control ----
    def _toggle(self) -> None:
        if self.proc and self.proc.state() != QProcess.NotRunning:
            self.pending_action = None
            self._set_state("stopping…", "#d9b04a")
            # Graceful: tell the worker to cancel its render and exit (the
            # task is requeued). QProcess.terminate() can't stop console-less
            # processes on Windows, so a hard kill backs this up.
            self._send_cmd("shutdown")
            QTimer.singleShot(6000, self._force_kill)
            return
        self.log_dialog.log.clear()
        self._reset_task_panel()
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.stateChanged.connect(self._on_state)
        self.proc.start(sys.executable, ["-u", "-m", "muffin.worker"])
        self.btn.setText("Stop worker")
        self.btn.setObjectName("danger")
        self.style().polish(self.btn)
        self._set_state("starting…", "#4a90d9")

    def _force_kill(self) -> None:
        if self.proc and self.proc.state() != QProcess.NotRunning:
            self.proc.kill()

    def _on_state(self, state) -> None:
        if state != QProcess.NotRunning:
            return
        self.btn.setText("Start worker")
        self.btn.setObjectName("primary")
        self.style().polish(self.btn)
        self._set_state("stopped", "#8a91a0")
        self._reset_task_panel()

        action, self.pending_action = self.pending_action, None
        if action == "restart":
            self._toggle()
        elif action in ("reboot", "shutdown"):
            flag = "/r" if action == "reboot" else "/s"
            verb = "restart" if action == "reboot" else "shut down"
            subprocess.Popen(["shutdown", flag, "/t", "30"], creationflags=_NO_WINDOW)
            QMessageBox.information(
                self, "Muffin",
                f"The PC will {verb} in 30 seconds.\n"
                "Run  shutdown /a  in a terminal to abort.")

    def _read(self) -> None:
        text = bytes(self.proc.readAllStandardOutput()).decode("utf-8", "replace")
        self.log_dialog.log.appendPlainText(text.rstrip())

        for line in text.splitlines():
            m = _PICKED.search(line)
            if m:
                task_id, job_name, dcc, a, b = m.groups()
                self.job_lbl.setText(job_name or "—")
                self.task_lbl.setText(task_id)
                self.dcc_lbl.setText(dcc)
                self.frames_lbl.setText(f"{a}–{b}")
                self.frame_lbl.setText(a)
                self.progress.setValue(0)
                self._set_state("rendering", "#4a90d9")
                continue
            m = _PROGRESS.search(line)
            if m:
                _task, cur, end, pct = m.groups()
                self.frame_lbl.setText(f"{cur} / {end}")
                # 100% means done — while rendering, cap at 99% to match
                # Muffin's Monitor.
                self.progress.setValue(min(99, int(pct)))
                continue
            if "registered as" in line:
                self._set_state("idle", "#d8dce3")
            elif "outside render schedule" in line:
                # Manager parked this worker for its schedule (work hours).
                self._set_state("scheduled off — work hours", "#e8a13a")
            elif "schedule window open" in line:
                self._set_state("idle", "#d8dce3")
            elif "-> requeued" in line:
                # Render was paused for the schedule, not failed — requeued.
                self._finish_task("paused", "#d9b04a")
            elif "-> done" in line:
                job, frames = self.job_lbl.text(), self.frames_lbl.text()
                self._finish_task("done", "#4caf72")
                if job != "—":
                    self.notifier.notify(
                        "Render finished",
                        f"'{job}' frames {frames} done on this worker.", "info")
                if not self.pending_action:
                    self._set_state("idle", "#d8dce3")
            elif "-> failed" in line:
                job, frames = self.job_lbl.text(), self.frames_lbl.text()
                self._finish_task("failed", "#e0594f")
                if job != "—":
                    self.notifier.notify(
                        "Render failed",
                        f"'{job}' frames {frames} failed on this worker.", "critical")
                if not self.pending_action:
                    self._set_state("idle", "#d8dce3")
            elif "could not reach manager" in line:
                self._set_state("can't reach manager — retrying", "#e0594f")

    def _set_state(self, text: str, color: str) -> None:
        self.state.setText(f"● {text}")
        self.state.setStyleSheet(f"color:{color}; font-size:26px; font-weight:bold")

    def closeEvent(self, event) -> None:
        self.notifier.hide()
        if self.proc and self.proc.state() != QProcess.NotRunning:
            self.proc.terminate()
            self.proc.waitForFinished(2000)
        event.accept()


def main() -> None:
    from PySide6.QtCore import QLockFile

    from .. import config

    app = QApplication(sys.argv)
    app.setStyleSheet(QSS)
    apply_app_icon(app)

    # One render worker per machine: a second instance would fight the first
    # over the same DCC licences and CPU, so refuse to start.
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(config.DATA_DIR / "muffin_worker_gui.lock"))
    if not lock.tryLock(100):
        # Already running — bring the existing window to the front instead.
        if not bring_to_front(_worker_title()):
            QMessageBox.warning(
                None, "Muffin",
                "Muffin is already running on this machine.\n"
                "Only one worker per machine is allowed.")
        sys.exit(0)

    win = WorkerWindow()
    win._lock = lock  # hold the lock for the app's lifetime
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
