"""Shared dark stylesheet + window icon for the Muffin desktop apps."""

import ctypes
import sys
from pathlib import Path

from PySide6.QtGui import QIcon

# <repo>/icon/icon_muffin.ico  (this file is <repo>/muffin/gui/style.py)
ICON_PATH = Path(__file__).resolve().parents[2] / "icon" / "icon_muffin.ico"


def app_icon() -> QIcon:
    return QIcon(str(ICON_PATH))


def bring_to_front(title: str) -> bool:
    """Focus an existing top-level window by exact title (Windows only).
    Returns True if a window was found and raised."""
    if sys.platform != "win32":
        return False
    u = ctypes.windll.user32
    hwnd = u.FindWindowW(None, title)
    if not hwnd:
        return False
    u.ShowWindow(hwnd, 9)  # SW_RESTORE — un-minimize if needed
    u.SetForegroundWindow(hwnd)
    return True


def apply_app_icon(app) -> None:
    """Set the icon for every window in this app (title bar + taskbar)."""
    app.setWindowIcon(app_icon())
    if sys.platform == "win32":
        # Make Windows use our icon in the taskbar instead of python's.
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("muffin.app")
        except Exception:
            pass


QSS = """
QWidget { background: #1d2027; color: #d8dce3; font-size: 13px; }
QLineEdit, QPlainTextEdit { background: #14161b; border: 1px solid #2e333d; border-radius: 4px; padding: 5px; }
QLineEdit:focus { border-color: #e8a13a; }
QPushButton { background: #23272f; border: 1px solid #2e333d; border-radius: 5px; padding: 6px 14px; }
QPushButton:hover { border-color: #e8a13a; color: #f2c46b; }
QPushButton#primary { background: #e8a13a; color: #1a1206; font-weight: bold; border: none; }
QPushButton#primary:hover { background: #f2c46b; }
QPushButton#danger:hover { border-color: #e0594f; color: #e0594f; }

/* Tool buttons (menu-bar Refresh, the jobs status filter) get the same obvious
   hover as the Start/Stop buttons so they don't look flat/dead. */
QToolButton { background: #23272f; border: 1px solid #2e333d; border-radius: 5px; padding: 4px 10px; }
QToolButton:hover { background: #2e333d; border-color: #e8a13a; color: #f2c46b; }
QToolButton:pressed { background: #14161b; }
QGroupBox { border: 1px solid #2e333d; border-radius: 6px; margin-top: 10px; padding-top: 10px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; color: #8a91a0; }
QTabBar::tab { background: #1d2027; padding: 8px 18px; color: #8a91a0; }
QTabBar::tab:selected { color: #e8a13a; border-bottom: 2px solid #e8a13a; }
QTabWidget::pane { border: 1px solid #2e333d; }
QLabel#hdr { color: #8a91a0; font-size: 12px; }

QTableWidget { background: #14161b; gridline-color: #2e333d; border: 1px solid #2e333d; selection-background-color: #33373f; }
QTableWidget::item { padding: 3px 6px; }
QTableWidget::item:selected { background: #3a3f4a; color: #ffffff; font-weight: bold; }
QHeaderView::section { background: #23272f; color: #8a91a0; padding: 6px; border: none; border-right: 1px solid #2e333d; border-bottom: 1px solid #2e333d; }
QTableCornerButton::section { background: #23272f; border: none; }

QProgressBar { background: #23272f; border: none; border-radius: 3px; text-align: center; color: #d8dce3; height: 16px; }
QProgressBar::chunk { background: #e8a13a; border-radius: 3px; }

QMenu { background: #23272f; border: 1px solid #2e333d; }
QMenu::item { padding: 6px 18px; }
QMenu::item:selected { background: #e8a13a; color: #1a1206; }
"""
