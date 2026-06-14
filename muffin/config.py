"""Central configuration. Everything is overridable via environment variables so a
worker on another machine just needs MUFFIN_MANAGER_URL set."""

import os
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


# --- Manager (server) ---
MANAGER_HOST = _env("MUFFIN_HOST", "0.0.0.0")
MANAGER_PORT = int(_env("MUFFIN_PORT", "8080"))

# Where the SQLite database lives.
DATA_DIR = Path(_env("MUFFIN_DATA_DIR", str(Path.home() / ".muffin")))
DB_PATH = Path(_env("MUFFIN_DB_PATH", str(DATA_DIR / "muffin.db")))

# A worker is considered offline if we haven't heard from it in this many seconds.
WORKER_TIMEOUT = float(_env("MUFFIN_WORKER_TIMEOUT", "30"))

# Default number of frames bundled into a single task (chunk).
DEFAULT_CHUNK_SIZE = int(_env("MUFFIN_CHUNK_SIZE", "1"))

# How many times a failed task is retried before the job is failed.
MAX_TASK_ATTEMPTS = int(_env("MUFFIN_MAX_ATTEMPTS", "3"))


# --- Worker (agent) ---
# URL the worker uses to reach the manager.
MANAGER_URL = _env("MUFFIN_MANAGER_URL", f"http://127.0.0.1:{MANAGER_PORT}")

# How often the worker pings the manager / asks for work (seconds).
HEARTBEAT_INTERVAL = float(_env("MUFFIN_HEARTBEAT", "5"))

# Comma-separated list of DCCs this worker can run, e.g. "blender,maya".
# Empty means "accept any DCC".
WORKER_CAPABILITIES = [
    c.strip().lower()
    for c in _env("MUFFIN_CAPABILITIES", "").split(",")
    if c.strip()
]

# Human-friendly worker name. Defaults to the hostname.
WORKER_NAME = _env("MUFFIN_WORKER_NAME", "")
