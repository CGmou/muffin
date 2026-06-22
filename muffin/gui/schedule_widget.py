"""A reusable render-schedule editor, shared by Muffin's Monitor (set schedules
for the whole farm) and the Worker app (an artist tunes their own machine).

Readability first: each day is one of Off / All day / Render window, and the
From/To time fields only appear when that day actually uses a window. A live
plain-English line ("Mon–Fri 18:00–09:00 next day · Sat–Sun all day") sits under
the editor so the schedule reads at a glance."""

from PySide6.QtCore import QTime, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QStackedLayout, QTimeEdit, QVBoxLayout, QWidget,
)

from .. import schedule

_MODE_LABELS = ["Off", "All day", "Window"]
_MODE_KEYS = ["off", "all", "window"]


def _to_min(t: QTime) -> int:
    return t.hour() * 60 + t.minute()


def _to_qtime(m: int) -> QTime:
    m = max(0, min(schedule.DAY_MINUTES, int(m)))
    return QTime((m // 60) % 24, m % 60)


class _DayRow:
    """One weekday's controls: a mode dropdown plus, only for 'Window', the
    From/To time fields (with a 'next day' hint when the window crosses midnight).
    For Off / All day a short label shows instead of the times."""

    def __init__(self, grid: QGridLayout, r: int, day: int, on_change) -> None:
        self.day = day
        self._on_change = on_change
        self.label = QLabel(schedule.DAY_NAMES[day])
        self.label.setObjectName("hdr")
        self.mode = QComboBox()
        self.mode.addItems(_MODE_LABELS)
        self.from_t = QTimeEdit()
        self.from_t.setDisplayFormat("HH:mm")
        self.to_t = QTimeEdit()
        self.to_t.setDisplayFormat("HH:mm")
        self.hint = QLabel("")
        self.hint.setStyleSheet("color:#8a91a0")
        self.info = QLabel("")

        times = QWidget()
        th = QHBoxLayout(times)
        th.setContentsMargins(0, 0, 0, 0)
        th.addWidget(self.from_t)
        th.addWidget(QLabel("→"))
        th.addWidget(self.to_t)
        th.addWidget(self.hint)
        th.addStretch()

        self.detail = QWidget()
        self.stack = QStackedLayout(self.detail)
        self.stack.setContentsMargins(0, 0, 0, 0)
        self.stack.addWidget(times)       # index 0 — window
        self.stack.addWidget(self.info)   # index 1 — off / all day

        grid.addWidget(self.label, r, 0)
        grid.addWidget(self.mode, r, 1)
        grid.addWidget(self.detail, r, 2)

        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.from_t.timeChanged.connect(self._times_changed)
        self.to_t.timeChanged.connect(self._times_changed)

    def _times_changed(self, *_) -> None:
        self._update_hint()
        self._on_change()

    def _mode_changed(self, *_) -> None:
        key = _MODE_KEYS[self.mode.currentIndex()]
        if key == "window":
            self.stack.setCurrentIndex(0)
            self._update_hint()
        else:
            self.stack.setCurrentIndex(1)
            self.info.setText("renders all day" if key == "all" else "no rendering")
            self.info.setStyleSheet("color:#4caf72" if key == "all" else "color:#8a91a0")
        self._on_change()

    def _update_hint(self) -> None:
        wraps = _to_min(self.to_t.time()) <= _to_min(self.from_t.time())
        self.hint.setText("(next day)" if wraps else "")

    def set(self, day: dict) -> None:
        self.mode.setCurrentIndex(_MODE_KEYS.index(day["mode"]))
        self.from_t.setTime(_to_qtime(day["start"]))
        self.to_t.setTime(_to_qtime(day["end"]))
        self._mode_changed()

    def read(self) -> dict:
        return {"mode": _MODE_KEYS[self.mode.currentIndex()],
                "start": _to_min(self.from_t.time()),
                "end": _to_min(self.to_t.time())}


class ScheduleEditor(QWidget):
    """Self-contained editor for one schedule. Use set_schedule()/get_schedule()
    and get_enabled(); listen to `changed` for live previews."""

    changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        self.enable = QCheckBox("Enable schedule  (off = render around the clock)")
        self.enable.toggled.connect(self._on_toggle)
        root.addWidget(self.enable)

        self.box = QGroupBox("When is this machine free to render?")
        grid = QGridLayout(self.box)
        grid.setColumnStretch(2, 1)
        self.rows = [_DayRow(grid, d, d, self._on_change) for d in range(schedule.DAYS)]
        root.addWidget(self.box)

        # Quick-fill: set one window across several days at once.
        q = QHBoxLayout()
        q.addWidget(QLabel("Quick set:"))
        self.qfrom = QTimeEdit()
        self.qfrom.setDisplayFormat("HH:mm")
        self.qfrom.setTime(QTime(18, 0))
        self.qto = QTimeEdit()
        self.qto.setDisplayFormat("HH:mm")
        self.qto.setTime(QTime(9, 0))
        q.addWidget(self.qfrom)
        q.addWidget(QLabel("→"))
        q.addWidget(self.qto)
        self.qdays = []
        for i, nm in enumerate(schedule.DAY_NAMES):
            cb = QCheckBox(nm)
            cb.setChecked(i in schedule.WEEKDAYS)
            q.addWidget(cb)
            self.qdays.append(cb)
        qb = QPushButton("Apply")
        qb.clicked.connect(self._apply_quick)
        q.addWidget(qb)
        q.addStretch()
        self.qbar = q
        root.addLayout(q)

        # Presets.
        p = QHBoxLayout()
        plbl = QLabel("Presets:")
        plbl.setObjectName("hdr")
        p.addWidget(plbl)
        for text, fn in (("Nights & weekends", schedule.nights_and_weekends),
                         ("Render 24/7", schedule.full),
                         ("Off (never)", schedule.empty)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, f=fn: self._load(f(), enable=True))
            p.addWidget(b)
        p.addStretch()
        self.pbar = p
        root.addLayout(p)

        self.summary = QLabel("")
        self.summary.setObjectName("hdr")
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)

        self._load(schedule.nights_and_weekends(), enable=False)

    # ---- public API ----
    def set_schedule(self, enabled: bool, sched: dict) -> None:
        self._load(sched or schedule.nights_and_weekends(), enable=bool(enabled))

    def get_enabled(self) -> bool:
        return self.enable.isChecked()

    def get_schedule(self) -> dict:
        return {"days": [r.read() for r in self.rows]}

    # ---- internals ----
    def _on_toggle(self, *_) -> None:
        self._refresh_enabled()
        self.changed.emit()

    def _on_change(self) -> None:
        self._update_summary()
        self.changed.emit()

    def _apply_quick(self) -> None:
        days = [i for i, cb in enumerate(self.qdays) if cb.isChecked()]
        if not days:
            QMessageBox.information(self, "Schedule", "Tick at least one day first.")
            return
        for d in days:
            row = self.rows[d]
            row.mode.setCurrentIndex(_MODE_KEYS.index("window"))
            row.from_t.setTime(self.qfrom.time())
            row.to_t.setTime(self.qto.time())
        self._on_change()

    def _load(self, sched: dict, enable=None) -> None:
        s = schedule.normalize(sched)
        for d, row in enumerate(self.rows):
            row.set(s["days"][d])
        if enable is not None:
            self.enable.blockSignals(True)
            self.enable.setChecked(bool(enable))
            self.enable.blockSignals(False)
        self._refresh_enabled()

    def _refresh_enabled(self) -> None:
        on = self.enable.isChecked()
        self.box.setEnabled(on)
        for bar in (self.qbar, self.pbar):
            for i in range(bar.count()):
                w = bar.itemAt(i).widget()
                if w:
                    w.setEnabled(on)
        self._update_summary()

    def _update_summary(self) -> None:
        if not self.enable.isChecked():
            self.summary.setText("Schedule off — renders 24/7.")
        else:
            self.summary.setText("In plain words:   "
                                 + schedule.human_summary(self.get_schedule()))
