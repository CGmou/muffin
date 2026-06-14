"""Native desktop notifications (system-tray toasts) for the Muffin apps.

A thin wrapper over QSystemTrayIcon so the Monitor and Worker can pop a real
OS notification when a render finishes or fails — no extra dependencies, since
PySide6 already ships the tray icon and Windows turns ``showMessage`` into a
proper toast.

Keep the Notifier alive for the window's lifetime: the tray icon must stay
referenced or the toast never appears. One ``enabled`` flag mutes every toast,
so a single menu checkbox can turn notifications on/off.
"""

from PySide6.QtWidgets import QSystemTrayIcon

from .style import app_icon


class Notifier:
    """Pops toast notifications via a tray icon (a no-op where unavailable)."""

    def __init__(self, app_name: str = "Muffin", window=None) -> None:
        self.enabled = True
        self._window = window
        self._tray: QSystemTrayIcon | None = None
        # No system tray (headless box, some Linux desktops) → notify() is a
        # silent no-op rather than a crash.
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._tray = QSystemTrayIcon(app_icon(), window)
            self._tray.setToolTip(app_name)
            self._tray.activated.connect(self._on_activated)
            self._tray.show()

    def _on_activated(self, reason) -> None:
        # Left-click the tray icon → bring the app window back to the front.
        if reason == QSystemTrayIcon.Trigger and self._window is not None:
            w = self._window.window()
            w.showNormal()
            w.raise_()
            w.activateWindow()

    def notify(self, title: str, message: str, level: str = "info") -> None:
        """Show a toast. ``level`` picks the icon: info / warning / critical."""
        if not self.enabled or self._tray is None:
            return
        icon = {
            "warning": QSystemTrayIcon.Warning,
            "critical": QSystemTrayIcon.Critical,
        }.get(level, QSystemTrayIcon.Information)
        self._tray.showMessage(title, message, icon, 6000)

    def hide(self) -> None:
        """Remove the tray icon (call from the window's closeEvent)."""
        if self._tray is not None:
            # Drop the click handler first: the window is tearing down, so a
            # late tray click must not reach a half-destroyed window.
            try:
                self._tray.activated.disconnect(self._on_activated)
            except (RuntimeError, TypeError):
                pass
            self._tray.hide()
