"""Shared settings dialog — manager URL, DCC executable paths, and the extra
folders the kick (.ass) renderer needs. Opened from a menu in the Manager and
Worker apps. Writes to the shared settings.json that the worker reads."""

from functools import partial

from PySide6.QtWidgets import (
    QDialog, QFileDialog, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QVBoxLayout,
)

from .. import settings

# (label, settings-key, default exe name) — DCC executables, picked as files.
DCC_FIELDS = [
    ("Blender", "blender", "blender.exe"),
    ("Maya (Render)", "maya", "Render.exe"),
    ("Houdini (husk)", "houdini", "husk.exe"),
    ("Houdini (hython)", "hython", "hython.exe"),
    ("Kick (Arnold .ass)", "kick", "kick.exe"),
]

# (label, kick-settings-key, hint) — folders the kick renderer needs, picked as
# directories. Stored under settings["kick"].
KICK_DIRS = [
    ("Maya bin", "maya_bin", "Arnold libs — added to PATH, e.g. …/Maya2024/bin"),
    ("XGen", "xgen", "Maya XGen plug-in folder — added to PATH, e.g. …/plug-ins/xgen"),
    ("Procedurals", "procedurals", "kick's -l procedural search path"),
]


class NodeSettingsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(640, 480)
        self.dcc_edits: dict[str, QLineEdit] = {}
        self.kick_edits: dict[str, QLineEdit] = {}
        self._build()
        self._load()

    def _build(self) -> None:
        lay = QVBoxLayout(self)

        conn = QGroupBox("Connection")
        cl = QVBoxLayout(conn)
        self.manager_url = QLineEdit()
        cl.addWidget(QLabel("Manager URL"))
        cl.addWidget(self.manager_url)
        lay.addWidget(conn)

        paths = QGroupBox("DCC executable paths")
        pl = QVBoxLayout(paths)
        for label, key, default in DCC_FIELDS:
            edit = QLineEdit()
            edit.setPlaceholderText(f"path to {default} (blank = look on PATH)")
            self.dcc_edits[key] = edit
            pl.addLayout(self._path_row(label, edit, partial(self._browse_file, key)))
        lay.addWidget(paths)

        kick = QGroupBox("Arnold .ass / kick rendering (folders on this worker)")
        kl = QVBoxLayout(kick)
        for label, key, hint in KICK_DIRS:
            edit = QLineEdit()
            edit.setPlaceholderText(hint)
            self.kick_edits[key] = edit
            kl.addLayout(self._path_row(label, edit, partial(self._browse_dir, key)))
        lay.addWidget(kick)

        bar = QHBoxLayout()
        auto = QPushButton("Auto-detect")
        auto.setToolTip("Scan the standard install folders and fill the paths in")
        auto.clicked.connect(self._autodetect)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.setObjectName("primary")
        save.clicked.connect(self._save)
        bar.addWidget(auto)
        bar.addStretch()
        bar.addWidget(cancel)
        bar.addWidget(save)
        lay.addLayout(bar)

    def _path_row(self, label: str, edit: QLineEdit, on_browse) -> QHBoxLayout:
        row = QHBoxLayout()
        lbl = QLabel(label)
        lbl.setFixedWidth(130)
        browse = QPushButton("Browse…")
        browse.clicked.connect(on_browse)
        row.addWidget(lbl)
        row.addWidget(edit, 1)
        row.addWidget(browse)
        return row

    def _browse_file(self, key: str) -> None:
        path, _ = QFileDialog.getOpenFileName(self, f"Locate {key} executable")
        if path:
            self.dcc_edits[key].setText(path)

    def _browse_dir(self, key: str) -> None:
        path = QFileDialog.getExistingDirectory(self, f"Locate the {key} folder")
        if path:
            self.kick_edits[key].setText(path)

    # ----------------------------------------------------------- auto-detect --
    def _autodetect(self) -> None:
        """Fill every path from the standard install folders in one click; ask
        which version to use when several are installed."""
        from PySide6.QtWidgets import QMessageBox
        from . import autodetect
        found = autodetect.detect_all()
        filled = []

        bl = self._choose_version("Blender", found["blender"])
        if bl and bl.get("blender"):
            self.dcc_edits["blender"].setText(bl["blender"])
            filled.append("Blender")

        ho = self._choose_version("Houdini", found["houdini"])
        if ho:
            for key in ("houdini", "hython"):
                if ho.get(key):
                    self.dcc_edits[key].setText(ho[key])
            if ho.get("houdini") or ho.get("hython"):
                filled.append("Houdini")

        my = self._choose_version("Maya", found["maya"])
        if my:
            if my.get("maya"):
                self.dcc_edits["maya"].setText(my["maya"])
            if my.get("kick"):
                self.dcc_edits["kick"].setText(my["kick"])
            for key in ("maya_bin", "xgen", "procedurals"):
                if my.get(key):
                    self.kick_edits[key].setText(my[key])
            filled.append("Maya + Arnold/kick")

        if filled:
            QMessageBox.information(
                self, "Auto-detect",
                "Filled in: " + ", ".join(filled) + ".\n\nReview the paths "
                "(especially the kick plugins/procedurals folders) and click Save.")
        else:
            QMessageBox.information(
                self, "Auto-detect",
                "No DCC installs were found in the standard locations.\n"
                "Use the Browse… buttons to set them manually.")

    def _choose_version(self, family: str, candidates: list):
        """Return the chosen version's paths dict (asking when several), or None."""
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0][1]
        from PySide6.QtWidgets import QInputDialog
        versions = [v for v, _ in candidates]
        ver, ok = QInputDialog.getItem(
            self, f"{family} version",
            f"Several {family} versions found — pick one:", versions, 0, False)
        if not ok:
            return None
        return dict(candidates).get(ver)

    def _load(self) -> None:
        s = settings.load()
        self.manager_url.setText(s.get("manager_url", ""))
        for key, edit in self.dcc_edits.items():
            edit.setText(s.get("dcc_paths", {}).get(key, ""))
        kick = s.get("kick", {}) or {}
        for key, edit in self.kick_edits.items():
            edit.setText(kick.get(key, ""))

    def _save(self) -> None:
        s = settings.load()  # preserve any keys we no longer expose (name, caps)
        s["manager_url"] = self.manager_url.text().strip() or "http://127.0.0.1:8080"
        s["dcc_paths"] = {k: e.text().strip() for k, e in self.dcc_edits.items() if e.text().strip()}
        s["kick"] = {k: e.text().strip() for k, e in self.kick_edits.items() if e.text().strip()}
        settings.save(s)
        self.accept()
