"""Shared settings dialog — manager URL and DCC executable paths. Opened from a
menu in the Manager and Worker apps. Writes to the shared settings.json that the
worker reads."""

from functools import partial

from PySide6.QtWidgets import (
    QDialog, QFileDialog, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QVBoxLayout,
)

from .. import settings

# (label, settings-key, default exe name) — skip the built-in mock DCC.
DCC_FIELDS = [
    ("Blender", "blender", "blender.exe"),
    ("Maya (Render)", "maya", "Render.exe"),
    ("Houdini (husk)", "houdini", "husk.exe"),
    ("Houdini (hython)", "hython", "hython.exe"),
    ("Nuke", "nuke", "nuke.exe"),
    ("DJV (preview)", "djv", "djv.exe"),
]


class NodeSettingsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(620, 380)
        self.dcc_edits: dict[str, QLineEdit] = {}
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
            row = QHBoxLayout()
            edit = QLineEdit()
            edit.setPlaceholderText(f"path to {default} (blank = look on PATH)")
            self.dcc_edits[key] = edit
            browse = QPushButton("Browse…")
            browse.clicked.connect(partial(self._browse, key))
            lbl = QLabel(label)
            lbl.setFixedWidth(130)
            row.addWidget(lbl)
            row.addWidget(edit, 1)
            row.addWidget(browse)
            pl.addLayout(row)
        lay.addWidget(paths)

        bar = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.setObjectName("primary")
        save.clicked.connect(self._save)
        bar.addStretch()
        bar.addWidget(cancel)
        bar.addWidget(save)
        lay.addLayout(bar)

    def _browse(self, key: str) -> None:
        path, _ = QFileDialog.getOpenFileName(self, f"Locate {key} executable")
        if path:
            self.dcc_edits[key].setText(path)

    def _load(self) -> None:
        s = settings.load()
        self.manager_url.setText(s.get("manager_url", ""))
        for key, edit in self.dcc_edits.items():
            edit.setText(s.get("dcc_paths", {}).get(key, ""))

    def _save(self) -> None:
        s = settings.load()  # preserve any keys we no longer expose (name, caps)
        s["manager_url"] = self.manager_url.text().strip() or "http://127.0.0.1:8080"
        s["dcc_paths"] = {k: e.text().strip() for k, e in self.dcc_edits.items() if e.text().strip()}
        settings.save(s)
        self.accept()