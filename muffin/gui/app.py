"""Muffin Node — the control app for a farm machine: run the manager here, and
reach the settings and the other Muffin apps (Monitor, Worker).

Run with:  python -m muffin.gui   (needs:  pip install -r requirements-gui.txt)

  * Settings (manager URL, worker name, DCC paths) live in the Settings menu.
  * The Monitor and Worker are separate apps — launch them from the buttons here
    or directly with `python -m muffin.gui.monitor` / `python -m muffin.gui.worker`.
"""

import sys

from PySide6.QtCore import QProcess, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QFont
from PySide6.QtWidgets import (
    QApplication, QGroupBox, QHBoxLayout, QLabel, QMainWindow, QPlainTextEdit,
    QPushButton, QVBoxLayout, QWidget,
)

from .. import settings
from .settings_dialog import NodeSettingsDialog
from .style import QSS, app_icon, apply_app_icon


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Muffin Manager")
        self.setWindowIcon(app_icon())
        self.resize(720, 560)
        self.manager_proc: QProcess | None = None
        self._build_menu()
        self._build()
        # Start the manager as soon as the Node opens (Settings ▸ Start manager
        # automatically). The user can still Stop it from the button.
        if settings.load().get("manager_autostart", True):
            QTimer.singleShot(0, self._toggle_manager)

    def _build_menu(self) -> None:
        m = self.menuBar().addMenu("Settings")
        act = QAction("Settings…", self)
        act.triggered.connect(lambda: NodeSettingsDialog(self).exec())
        m.addAction(act)
        m.addSeparator()
        self.act_autostart = QAction("Start manager automatically on launch", self)
        self.act_autostart.setCheckable(True)
        self.act_autostart.setChecked(settings.load().get("manager_autostart", True))
        self.act_autostart.toggled.connect(self._toggle_autostart)
        m.addAction(self.act_autostart)

    def _toggle_autostart(self, on: bool) -> None:
        s = settings.load()
        s["manager_autostart"] = on
        settings.save(s)

    def _build(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        lay = QVBoxLayout(central)

        # --- Manager control ---
        box = QGroupBox("Manager (this machine)")
        bl = QVBoxLayout(box)
        bar = QHBoxLayout()
        self.manager_btn = QPushButton("Start manager")
        self.manager_btn.setObjectName("primary")
        self.manager_btn.clicked.connect(self._toggle_manager)
        self.manager_state = QLabel("● stopped")
        self.manager_state.setStyleSheet("color:#8a91a0")
        bar.addWidget(self.manager_btn)
        bar.addWidget(self.manager_state, 1)
        bl.addLayout(bar)
        lay.addWidget(box)

        # --- Launchers for the other apps ---
        apps = QGroupBox("Apps")
        al = QHBoxLayout(apps)
        mon = QPushButton("Open Monitor")
        mon.clicked.connect(lambda: self._spawn_app("muffin.gui.monitor"))
        wrk = QPushButton("Open Worker")
        wrk.clicked.connect(lambda: self._spawn_app("muffin.gui.worker"))
        al.addWidget(mon)
        al.addWidget(wrk)
        al.addStretch()
        lay.addWidget(apps)

        lay.addWidget(QLabel("Manager log"))
        self.manager_log = QPlainTextEdit()
        self.manager_log.setReadOnly(True)
        self.manager_log.setFont(QFont("Consolas", 9))
        self.manager_log.setMaximumBlockCount(5000)
        lay.addWidget(self.manager_log, 1)

    # ------------------------------------------------------------- manager --
    def _toggle_manager(self) -> None:
        if self.manager_proc and self.manager_proc.state() != QProcess.NotRunning:
            self.manager_proc.terminate()
            return
        self.manager_log.clear()
        self.manager_proc = QProcess(self)
        self.manager_proc.setProcessChannelMode(QProcess.MergedChannels)
        self.manager_proc.readyReadStandardOutput.connect(
            lambda: self.manager_log.appendPlainText(
                bytes(self.manager_proc.readAllStandardOutput()).decode("utf-8", "replace").rstrip()))
        self.manager_proc.stateChanged.connect(self._on_manager_state)
        self.manager_proc.start(sys.executable, ["-u", "-m", "muffin.manager"])
        self.manager_btn.setText("Stop manager")
        self.manager_btn.setObjectName("danger")
        self.style().polish(self.manager_btn)
        self.manager_state.setText("● running")
        self.manager_state.setStyleSheet("color:#4caf72")

    def _on_manager_state(self, state) -> None:
        if state == QProcess.NotRunning:
            self.manager_btn.setText("Start manager")
            self.manager_btn.setObjectName("primary")
            self.style().polish(self.manager_btn)
            self.manager_state.setText("● stopped")
            self.manager_state.setStyleSheet("color:#8a91a0")

    # --------------------------------------------------------------- utils --
    def _spawn_app(self, module: str) -> None:
        """Launch a sibling GUI (Monitor/Worker) as its own detached process."""
        QProcess.startDetached(sys.executable, ["-m", module])

    def closeEvent(self, event) -> None:
        if self.manager_proc and self.manager_proc.state() != QProcess.NotRunning:
            self.manager_proc.terminate()
            self.manager_proc.waitForFinished(2000)
        event.accept()


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyleSheet(QSS)
    apply_app_icon(app)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
