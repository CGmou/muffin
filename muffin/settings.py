"""Persistent node settings (JSON on disk). Shared by the PySide6 control app and
the worker, so DCC paths configured in the GUI are honoured by renders even when
the worker is started from the CLI.

Resolution order for a DCC executable is: environment override
(MUFFIN_<DCC>_EXE) > this settings file > bare name on PATH."""

import copy
import json
from pathlib import Path
from typing import Any

from . import config

SETTINGS_PATH: Path = config.DATA_DIR / "settings.json"

DEFAULTS: dict[str, Any] = {
    "manager_url": config.MANAGER_URL,
    "worker_name": "",          # blank = use hostname
    "capabilities": [],         # blank list = accept every DCC (the common case)
    "dcc_paths": {},            # {"blender": "C:/.../blender.exe", "kick": "..."}
    # Extra folders the kick (.ass) renderer needs on this worker — Maya's bin
    # and XGen plug-in dir go on PATH, procedurals is kick's -l search path.
    "kick": {"maya_bin": "", "xgen": "", "procedurals": ""},
    # GUI conveniences (read by the desktop apps; ignored by headless workers).
    "worker_autostart": True,   # Worker app starts rendering as soon as it opens
    "manager_autostart": True,  # Node app starts the manager as soon as it opens
    "worker_notify": True,      # Worker toasts when its render finishes / fails
    "monitor_notify": True,     # Monitor toasts on job done / failed (master switch)
    "monitor_notify_done": True,
    "monitor_notify_failed": True,
}


def load() -> dict[str, Any]:
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    # Deep-copy so the mutable defaults (dcc_paths {}, capabilities []) are never
    # shared by reference — callers mutate the result in place (e.g. setting a
    # dcc path), which would otherwise pollute the module-level DEFAULTS.
    return {**copy.deepcopy(DEFAULTS), **data}


def save(data: dict[str, Any]) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    merged = {**DEFAULTS, **data}
    SETTINGS_PATH.write_text(json.dumps(merged, indent=2), encoding="utf-8")


def dcc_path(name: str) -> str:
    """Configured executable path for a DCC, or '' if not set."""
    return load().get("dcc_paths", {}).get(name, "")
