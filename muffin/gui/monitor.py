"""Muffin's Monitor — a PySide6 view of the farm (jobs + tasks + workers), live.

  * Jobs list is a tree: a multi-scene submission shows as one parent row,
    e.g. "demo (2 jobs)", expandable into its scene jobs (demo - floor,
    demo - cube). Selecting a scene job shows its frame-chunk tasks below.
  * Right-click for job controls (a parent row controls every scene in it).
  * Workers list shows machine specs; editing is locked behind
    Edit ▸ Super Muffin Mode.

Run with:  python -m muffin.gui.monitor
"""

import threading
import time

import requests
from PySide6.QtCore import QByteArray, QObject, QRect, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QFontMetrics, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
    QPlainTextEdit, QPushButton, QSpinBox, QSplitter, QStyledItemDelegate,
    QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QWidget,
)

from .. import priority, schedule, settings
from .notify import Notifier
from .schedule_widget import ScheduleEditor
from .style import QSS, app_icon, apply_app_icon

# Row text color per job status.
_JOB_ROW_COLORS = {
    "done": "#4caf72",      # green
    "running": "#4a90d9",   # blue
    "failed": "#e0594f",    # red
    "paused": "#ffffff",    # white
    "canceled": "#8a91a0",  # grey (suspended)
    "requeued": "#d9b04a",  # yellow — waiting for Start
    "queued": "#d8dce3",    # default
}

_SORT_ROLE = Qt.UserRole + 9  # numeric sort key for tree columns


def _worker_row_color(w: dict) -> str:
    if w.get("status") == "offline":
        return "#8a91a0"          # grey
    if not w.get("enabled", 1):
        return "#e8a13a"          # orange
    if w.get("standby") and w.get("status") != "busy":
        return "#e8a13a"          # orange — parked by its schedule (work hours)
    if w.get("status") == "busy":
        return "#4caf72"          # green
    return "#ffffff"              # white


def _display_status(status: str) -> str:
    """User-facing wording: artists say 'rendering', not running/busy."""
    return "rendering" if status in ("running", "busy") else status


def _fmt_time(ts) -> str:
    if not ts:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def _fmt_time_secs(ts) -> str:
    if not ts:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _fmt_duration(secs: float) -> str:
    h, rem = divmod(int(secs), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def _job_render_total(j: dict) -> str:
    """Render time: first task start → last task end (live while rendering)."""
    start = j.get("render_start")
    if not start:
        return "—"
    end = j.get("render_end")
    if end:
        return _fmt_duration(end - start)
    if j.get("status") == "running":
        return _fmt_duration(time.time() - start)
    return "—"


def _task_render_time(task: dict) -> str:
    s = task.get("started_at")
    if not s:
        return "—"
    e = task.get("finished_at")
    if e:
        return _fmt_duration(e - s)
    if task.get("status") in ("running", "assigned"):
        return _fmt_duration(time.time() - s)
    return "—"


def _job_pct(job: dict) -> int:
    """Display progress = completed tasks only. 100% is reserved for done."""
    if job.get("status") == "done":
        return 100
    total = job.get("task_total") or 0
    if not total:
        return 0
    return min(99, int(round(job.get("task_done", 0) * 100 / total)))


def _batch_summary(batch: str, members: list[dict]) -> dict:
    """Collapse a batch's member jobs into one parent row. Status order:
    worst/most-active state wins."""
    statuses = {m["status"] for m in members}
    status = "done"
    for st in ("failed", "running", "requeued", "paused", "queued", "canceled"):
        if st in statuses:
            status = st
            break
    workers = sorted({w for m in members
                      for w in (m.get("workers") or "").split(", ") if w})
    starts = [m["render_start"] for m in members if m.get("render_start")]
    ends = [m.get("render_end") for m in members]
    finished = [m.get("finished_at") for m in members]
    renderers = {m.get("renderer") or "" for m in members}
    return {
        "id": f"batch:{batch}",
        "name": batch.rsplit("::", 1)[0],  # strip the trailing ::uuid, keep any :: in the name
        "dcc": members[0]["dcc"],
        "renderer": (members[0].get("renderer") or "")
                    if len(renderers) == 1 else "mixed",
        "status": status,
        "priority": members[0].get("priority", 0),
        "frame_start": min(m["frame_start"] for m in members),
        "frame_end": max(m["frame_end"] for m in members),
        "task_done": sum(m["task_done"] for m in members),
        "task_total": sum(m["task_total"] for m in members),
        "task_failed": sum(m["task_failed"] for m in members),
        "current_tasks": "",
        "submitter": members[0].get("submitter", ""),
        "workers": ", ".join(workers),
        "created_at": min(m["created_at"] for m in members),
        "render_start": min(starts) if starts else None,
        "render_end": max(ends) if all(ends) and ends else None,
        "finished_at": max(finished) if all(finished) and finished else None,
        "output_path": members[0].get("output_path", ""),
        "_members": [m["id"] for m in members],
    }


# --- column auto-fit -----------------------------------------------------------
# Columns are kept fitted to the viewport using fixed per-column *weights*, not
# by iteratively scaling current widths. That matters: scaling-in-place drifts
# (rounding + min-width floors) so shrinking then re-growing the window leaves
# columns squeezed. Distributing the width by stable weights always returns to
# the same proportions, and a manual column drag just updates the weights.
#
# Every column also has a hard minimum = the width of its header label, so a
# title is NEVER squeezed to the point you can't read it. When the title minima
# don't all fit, the table overflows and shows a horizontal scrollbar (push)
# instead of crushing the columns.
_HDR_MIN_PAD = 24  # header text + chrome (cell padding, sort-indicator arrow)


def _section_min(h, i: int) -> int:
    text = h.model().headerData(i, Qt.Horizontal, Qt.DisplayRole)
    return QFontMetrics(h.font()).horizontalAdvance(str(text or "")) + _HDR_MIN_PAD


def _fit_apply(widget) -> None:
    """Distribute the viewport width across the visible columns once (see the
    water-fill below). This is a deliberate, one-shot 'fill the window' — it is
    NOT run on every window resize, so a column you've sized stays put (like a
    spreadsheet); call it again via Layout ▸ Fit columns to window."""
    h = widget._fit_header()
    visible = [i for i in range(h.count()) if not h.isSectionHidden(i)]
    avail = widget.viewport().width()
    if not visible or avail <= 60:
        return
    weights = widget._fit_weights
    if not weights or set(weights) != set(visible):
        # (Re)seed weights from the current widths whenever the visible set
        # changes (e.g. compact mode hides columns).
        weights = {i: max(1, h.sectionSize(i)) for i in visible}
        widget._fit_weights = weights
    mins = {i: _section_min(h, i) for i in visible}
    # If even the header-width minima don't all fit, don't squeeze everything to
    # the title width — leave the columns at their current (comfortable) widths
    # and let the horizontal scrollbar show. Filling only makes sense when the
    # window is genuinely wide enough.
    if sum(mins.values()) > avail:
        return
    # Water-fill: share the width by weight, but pin any column whose weighted
    # share would fall under its header minimum and re-share the rest among the
    # others.
    free = list(visible)
    assigned: dict[int, int] = {}
    remaining = avail
    while free:
        totw = sum(weights[i] for i in free) or 1
        pinned = [i for i in free if remaining * weights[i] / totw < mins[i]]
        if not pinned:
            break
        for i in pinned:
            assigned[i] = mins[i]
            remaining -= mins[i]
            free.remove(i)
    if free:
        totw = sum(weights[i] for i in free) or 1
        acc = 0
        for i in free[:-1]:
            px = max(mins[i], int(round(remaining * weights[i] / totw)))
            assigned[i] = px
            acc += px
        assigned[free[-1]] = max(mins[free[-1]], remaining - acc)
    widget._fitting = True
    h.blockSignals(True)
    for i in visible:
        h.resizeSection(i, assigned[i])
    h.blockSignals(False)
    widget._fitting = False


def _apply_default_widths(view, widths) -> None:
    """Set sensible starting column widths — never narrower than the header label
    so titles read at the default — without tripping the user-resize handler."""
    h = view._fit_header()
    h.blockSignals(True)
    for i, w in enumerate(widths):
        if i < h.count():
            h.resizeSection(i, max(w, _section_min(h, i)))
    h.blockSignals(False)


def _fit_on_user_resize(widget) -> None:
    # A real (user) column drag re-seeds the weights (so a later 'Fit columns to
    # window' keeps the new proportions) and cancels any pending initial fit —
    # once you've sized a column, we never auto-resize the columns again.
    if getattr(widget, "_fitting", False) or getattr(widget, "_fit_restoring", False):
        return
    h = widget._fit_header()
    widget._fit_weights = {i: max(1, h.sectionSize(i))
                           for i in range(h.count()) if not h.isSectionHidden(i)}
    widget._fit_pending = False


class FitTableWidget(QTableWidget):
    """A table whose columns always fill the viewport (see _fit_apply)."""

    def __init__(self, *a, **k) -> None:
        super().__init__(*a, **k)
        self._fit_weights = None
        self._fitting = False
        self._fit_pending = True   # fill the window once, on the first real resize
        self.horizontalHeader().sectionResized.connect(
            lambda *_: _fit_on_user_resize(self))

    def _fit_header(self):
        return self.horizontalHeader()

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        # Fit ONLY the first time the widget gets a real width — after that the
        # columns are manual, so resizing the window never disturbs them.
        if self._fit_pending and self.viewport().width() > 60:
            _fit_apply(self)
            self._fit_pending = False

    def fit_columns(self) -> None:
        _fit_apply(self)


class SortableTreeItem(QTreeWidgetItem):
    """Tree item that sorts numerically when a sort key is set on the column."""

    def __lt__(self, other):
        col = self.treeWidget().sortColumn() if self.treeWidget() else 0
        a = self.data(col, _SORT_ROLE)
        b = other.data(col, _SORT_ROLE)
        if a is not None and b is not None:
            return a < b
        return self.text(col) < other.text(col)


class FitTreeWidget(QTreeWidget):
    """Tree version of FitTableWidget (used for the jobs list)."""

    def __init__(self, *a, **k) -> None:
        super().__init__(*a, **k)
        self._fit_weights = None
        self._fitting = False
        self._fit_pending = True   # fill the window once, on the first real resize
        self.header().sectionResized.connect(lambda *_: _fit_on_user_resize(self))

    def _fit_header(self):
        return self.header()

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        if self._fit_pending and self.viewport().width() > 60:
            _fit_apply(self)
            self._fit_pending = False

    def fit_columns(self) -> None:
        _fit_apply(self)

    def horizontalHeader(self):
        """Compatibility shim so layout save/restore code treats the jobs tree
        like the tables."""
        return self.header()


class ProgressDelegate(QStyledItemDelegate):
    """Paints a progress bar from item data; moves correctly when sorted."""

    def paint(self, painter, option, index):
        pct = index.data(Qt.UserRole) or 0
        color = index.data(Qt.UserRole + 1) or "#e8a13a"
        label = index.data(Qt.UserRole + 2) or f"{pct}%"
        r = option.rect.adjusted(4, 6, -4, -6)
        painter.save()
        painter.setRenderHint(painter.RenderHint.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#23272f"))
        painter.drawRoundedRect(r, 3, 3)
        w = int(r.width() * pct / 100)
        if w > 0:
            painter.setBrush(QColor(color))
            painter.drawRoundedRect(QRect(r.x(), r.y(), w, r.height()), 3, 3)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(option.rect, Qt.AlignCenter, label)
        painter.restore()


class Poller(QObject):
    """Polls jobs/workers (and the selected job's tasks) on a daemon thread."""

    data = Signal(dict)

    def __init__(self, base_getter, detail_getter) -> None:
        super().__init__()
        self._get_base = base_getter
        self._get_detail = detail_getter
        self._stop = threading.Event()
        self._kick = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self._kick.set()

    def refresh_now(self) -> None:
        self._kick.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            base = self._get_base().rstrip("/")
            out = {"ok": False, "jobs": [], "workers": [], "detail": None, "error": None}
            try:
                out["jobs"] = requests.get(f"{base}/api/jobs", timeout=4).json()
                out["workers"] = requests.get(f"{base}/api/workers", timeout=4).json()
                detail_id = self._get_detail()
                if detail_id:
                    r = requests.get(f"{base}/api/jobs/{detail_id}", timeout=4)
                    if r.ok:
                        out["detail"] = r.json()
                out["ok"] = True
            except Exception as exc:
                out["error"] = str(exc)
            self.data.emit(out)
            self._kick.wait(2.0)
            self._kick.clear()


# ------------------------------------------------------------- dialogs --------
class JobEditDialog(QDialog):
    def __init__(self, job: dict, parent=None, group: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Edit job — {job['name']}")
        self.group = group  # editing a whole batch: only priority applies
        self.started = bool(job.get("task_done") or job.get("task_running"))
        form = QFormLayout(self)

        self.name = QLineEdit(job["name"])
        self.priority = QComboBox()
        self.priority.addItems(priority.LABELS)
        self.priority.setCurrentText(priority.label_for(job.get("priority", 50)))
        form.addRow("Name", self.name)
        form.addRow("Priority", self.priority)

        self.f_start = self._spin(job["frame_start"])
        self.f_end = self._spin(job["frame_end"])
        frames = QHBoxLayout()
        frames.addWidget(QLabel("Start"))
        frames.addWidget(self.f_start, 1)
        frames.addSpacing(10)
        frames.addWidget(QLabel("End"))
        frames.addWidget(self.f_end, 1)
        form.addRow("Frames", frames)

        self.chunk = self._spin(job["chunk_size"], lo=1)
        form.addRow("Frames / task", self.chunk)

        if self.group:
            note = QLabel("Editing a multi-scene job — only Priority applies to all scenes.")
            note.setStyleSheet("color:#d9b04a")
            form.addRow(note)
            for w in (self.name, self.f_start, self.f_end, self.chunk):
                w.setEnabled(False)
        elif self.started:
            note = QLabel("Job has started — only Name and Priority can change.")
            note.setStyleSheet("color:#d9b04a")
            form.addRow(note)
            for w in (self.f_start, self.f_end, self.chunk):
                w.setEnabled(False)

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def _spin(self, value: int, lo: int = -100000) -> QSpinBox:
        s = QSpinBox()
        s.setRange(lo, 1000000)
        s.setValue(value)
        return s

    def payload(self) -> dict:
        prio = priority.value_for(self.priority.currentText())
        if self.group:
            return {"priority": prio}
        data = {"name": self.name.text().strip(), "priority": prio}
        if not self.started:
            data.update(frame_start=self.f_start.value(), frame_end=self.f_end.value(),
                        chunk_size=self.chunk.value())
        return data


class JobLogDialog(QDialog):
    """Shows every task's log for a job (opened by double-clicking the job)."""

    def __init__(self, job: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Log — {job['name']}")
        self.resize(820, 560)
        lay = QVBoxLayout(self)
        log = QPlainTextEdit()
        log.setReadOnly(True)
        log.setFont(QFont("Consolas", 9))
        parts = []
        for t in job.get("tasks", []):
            head = (f"════ task {t['id']}  frames {t['frame_start']}-{t['frame_end']}"
                    f"  [{t['status']}] ════")
            parts.append(head)
            parts.append(t.get("log") or "(no log)")
            parts.append("")
        log.setPlainText("\n".join(parts) or "(no tasks)")
        lay.addWidget(log)


class WorkerEditDialog(QDialog):
    """Edit a worker: just enable / disable."""

    def __init__(self, worker: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Edit worker — {worker['name']}")
        form = QFormLayout(self)
        self.enabled = QCheckBox("Worker accepts new tasks")
        self.enabled.setChecked(bool(worker.get("enabled", 1)))
        form.addRow("Enabled", self.enabled)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def is_enabled(self) -> bool:
        return self.enabled.isChecked()


class WorkerSchedulesDialog(QDialog):
    """Farm-wide schedules in one place (Schedule menu). The left list holds every
    worker; tick the ones to apply to, set the weekly windows once with the editor
    on the right, and Save. Outside its windows a worker is parked and a render in
    progress is stopped and requeued."""

    def __init__(self, base: str, parent=None) -> None:
        super().__init__(parent)
        self.base = base.rstrip("/")
        self.setWindowTitle("Worker Schedules")
        self.resize(760, 560)
        self._workers: list[dict] = []
        self._build()
        self._reload()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        intro = QLabel(
            "Tick the workers to schedule, set the weekly render windows once, and "
            "Save. Outside its windows a machine is left for the artist — a render "
            "in progress is stopped and requeued. Times are each worker's own "
            "local time.")
        intro.setObjectName("hdr")
        intro.setWordWrap(True)
        root.addWidget(intro)

        panes = QHBoxLayout()
        left = QVBoxLayout()
        lbl = QLabel("Apply to workers")
        lbl.setObjectName("hdr")
        left.addWidget(lbl)
        self.worker_list = QListWidget()
        left.addWidget(self.worker_list, 1)
        selrow = QHBoxLayout()
        allb = QPushButton("All")
        allb.clicked.connect(lambda: self._check_all(True))
        noneb = QPushButton("None")
        noneb.clicked.connect(lambda: self._check_all(False))
        selrow.addWidget(allb)
        selrow.addWidget(noneb)
        selrow.addStretch()
        left.addLayout(selrow)
        panes.addLayout(left, 1)

        self.editor = ScheduleEditor()
        panes.addWidget(self.editor, 2)
        root.addLayout(panes, 1)

        bar = QHBoxLayout()
        self.status = QLabel("")
        self.status.setObjectName("hdr")
        bar.addWidget(self.status, 1)
        save = QPushButton("Save")
        save.setObjectName("primary")
        save.clicked.connect(self._save)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        bar.addWidget(save)
        bar.addWidget(close)
        root.addLayout(bar)

    def _reload(self) -> None:
        try:
            self._workers = requests.get(f"{self.base}/api/workers", timeout=5).json()
        except Exception as exc:
            QMessageBox.warning(self, "Error", str(exc))
            return
        if self._workers and "standby" not in self._workers[0]:
            QMessageBox.warning(
                self, "Manager out of date",
                "This manager doesn't support worker schedules.\n"
                "Restart the Muffin Manager (or rebuild the NAS container) to "
                "enable it.")
            self.close()
            return
        self.worker_list.clear()
        for w in self._workers:
            it = QListWidgetItem(f"{w['name']}   ({schedule.summary(w)})")
            it.setData(Qt.UserRole, w["id"])
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked)
            self.worker_list.addItem(it)
        # Seed the editor from an existing schedule if any worker has one.
        seed = next((w for w in self._workers if w.get("schedule_enabled")), None)
        if seed:
            self.editor.set_schedule(True, seed.get("schedule"))
        else:
            self.editor.set_schedule(False, schedule.nights_and_weekends())
        if not self._workers:
            self.status.setText("No workers registered yet.")

    def _check_all(self, on: bool) -> None:
        for i in range(self.worker_list.count()):
            self.worker_list.item(i).setCheckState(Qt.Checked if on else Qt.Unchecked)

    def _checked_ids(self) -> list:
        return [self.worker_list.item(i).data(Qt.UserRole)
                for i in range(self.worker_list.count())
                if self.worker_list.item(i).checkState() == Qt.Checked]

    def _save(self) -> None:
        ids = self._checked_ids()
        if not ids:
            QMessageBox.information(self, "Schedule", "Tick at least one worker.")
            return
        payload = {"schedule_enabled": self.editor.get_enabled(),
                   "schedule": self.editor.get_schedule()}
        try:
            for wid in ids:
                requests.put(f"{self.base}/api/workers/{wid}",
                             json=payload, timeout=5).raise_for_status()
        except Exception as exc:
            QMessageBox.warning(self, "Error", str(exc))
            return
        # Reflect locally so the list labels update without another round-trip.
        idset = set(ids)
        for w in self._workers:
            if w["id"] in idset:
                w["schedule_enabled"] = payload["schedule_enabled"]
                w["schedule"] = payload["schedule"]
        for i in range(self.worker_list.count()):
            it = self.worker_list.item(i)
            w = next((x for x in self._workers if x["id"] == it.data(Qt.UserRole)), None)
            if w:
                it.setText(f"{w['name']}   ({schedule.summary(w)})")
        self.status.setText(f"Saved — applied to {len(ids)} worker(s).")


class EditWorkersDialog(QDialog):
    """Manage all workers at once: tick = enabled, plus remove."""

    def __init__(self, base: str, parent=None) -> None:
        super().__init__(parent)
        self.base = base.rstrip("/")
        self.setWindowTitle("Edit workers")
        self.resize(420, 460)
        self._orig: dict[str, bool] = {}
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Checked workers are enabled (accept new tasks)"))
        self.list = QListWidget()
        lay.addWidget(self.list, 1)
        bar = QHBoxLayout()
        rm = QPushButton("Remove selected")
        rm.setObjectName("danger")
        rm.clicked.connect(self._remove)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.setObjectName("primary")
        save.clicked.connect(self._save)
        bar.addWidget(rm)
        bar.addStretch()
        bar.addWidget(close)
        bar.addWidget(save)
        lay.addLayout(bar)
        self._reload()

    def _reload(self) -> None:
        try:
            workers = requests.get(f"{self.base}/api/workers", timeout=5).json()
        except Exception as exc:
            QMessageBox.warning(self, "Error", str(exc))
            return
        self.list.clear()
        self._orig.clear()
        for w in workers:
            it = QListWidgetItem(f"{w['name']}  ({w['status']})")
            it.setData(Qt.UserRole, w["id"])
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            enabled = bool(w.get("enabled", 1))
            it.setCheckState(Qt.Checked if enabled else Qt.Unchecked)
            self._orig[w["id"]] = enabled
            self.list.addItem(it)

    def _save(self) -> None:
        for i in range(self.list.count()):
            it = self.list.item(i)
            wid = it.data(Qt.UserRole)
            want = it.checkState() == Qt.Checked
            if want != self._orig.get(wid):
                action = "enable" if want else "disable"
                try:
                    requests.post(f"{self.base}/api/workers/{wid}/{action}", timeout=5)
                except Exception as exc:
                    QMessageBox.warning(self, "Error", str(exc))
                    return
        self._reload()

    def _remove(self) -> None:
        it = self.list.currentItem()
        if not it:
            return
        if QMessageBox.question(self, "Remove worker",
                                f"Remove '{it.text()}'? Its current task is requeued.") == QMessageBox.Yes:
            try:
                requests.delete(f"{self.base}/api/workers/{it.data(Qt.UserRole)}", timeout=5)
            except Exception as exc:
                QMessageBox.warning(self, "Error", str(exc))
                return
            self._reload()


class PoolDialog(QDialog):
    """Pool Management — pools on the left; the selected pool's members on the
    right with explicit Assign / Remove buttons (multi-select friendly)."""

    def __init__(self, base: str, parent=None) -> None:
        super().__init__(parent)
        self.base = base.rstrip("/")
        self.setWindowTitle("Pool Management")
        self.resize(680, 440)
        self._workers: list[dict] = []
        self._build()
        self._reload()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        panes = QHBoxLayout()

        # --- pools ---
        left = QVBoxLayout()
        lbl = QLabel("Pools")
        lbl.setObjectName("hdr")
        left.addWidget(lbl)
        self.pool_list = QListWidget()
        self.pool_list.currentItemChanged.connect(lambda *_: self._refresh_members())
        left.addWidget(self.pool_list, 1)
        btns = QHBoxLayout()
        new_btn = QPushButton("+ New…")
        new_btn.clicked.connect(self._new_pool)
        del_btn = QPushButton("– Delete")
        del_btn.setObjectName("danger")
        del_btn.clicked.connect(self._delete_pool)
        btns.addWidget(new_btn)
        btns.addWidget(del_btn)
        left.addLayout(btns)
        panes.addLayout(left, 1)

        # --- available workers ---
        mid = QVBoxLayout()
        avail_lbl = QLabel("Available workers")
        avail_lbl.setObjectName("hdr")
        mid.addWidget(avail_lbl)
        self.avail_list = QListWidget()
        self.avail_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        mid.addWidget(self.avail_list, 1)
        panes.addLayout(mid, 2)

        # --- assign / remove buttons ---
        arrows = QVBoxLayout()
        arrows.addStretch()
        assign_btn = QPushButton("Assign  →")
        assign_btn.setObjectName("primary")
        assign_btn.clicked.connect(self._assign)
        remove_btn = QPushButton("←  Remove")
        remove_btn.clicked.connect(self._remove)
        arrows.addWidget(assign_btn)
        arrows.addWidget(remove_btn)
        arrows.addStretch()
        panes.addLayout(arrows)

        # --- members of the selected pool ---
        right = QVBoxLayout()
        self.members_lbl = QLabel("In pool")
        self.members_lbl.setObjectName("hdr")
        right.addWidget(self.members_lbl)
        self.member_list = QListWidget()
        self.member_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        right.addWidget(self.member_list, 1)
        panes.addLayout(right, 2)

        root.addLayout(panes, 1)

        bottom = QHBoxLayout()
        self.status = QLabel("Select workers, then Assign / Remove. "
                             "A worker can be in several pools.")
        self.status.setObjectName("hdr")
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        bottom.addWidget(self.status, 1)
        bottom.addWidget(close)
        root.addLayout(bottom)

    # ----------------------------------------------------------- data ----
    def _current_pool(self):
        it = self.pool_list.currentItem()
        return it.text() if it else None

    def _reload(self, keep_pool: str = None) -> None:
        try:
            self._workers = requests.get(f"{self.base}/api/workers", timeout=5).json()
            pools = requests.get(f"{self.base}/api/pools", timeout=5).json()
        except Exception as exc:
            QMessageBox.warning(self, "Error", str(exc))
            return
        # Old managers don't report a "pools" list — multi-pool won't persist
        # until the manager is updated/restarted. Warn instead of looking broken.
        if self._workers and "pools" not in self._workers[0]:
            QMessageBox.warning(
                self, "Manager out of date",
                "This manager doesn't support multiple pools per worker.\n"
                "Restart the Muffin Manager (or rebuild the NAS container) to "
                "enable it — until then pool changes won't stick.")
        want = keep_pool or self._current_pool()
        self.pool_list.blockSignals(True)
        self.pool_list.clear()
        for p in pools:
            self.pool_list.addItem(QListWidgetItem(p))
            if p == want:
                self.pool_list.setCurrentRow(self.pool_list.count() - 1)
        if self.pool_list.currentRow() < 0 and self.pool_list.count():
            self.pool_list.setCurrentRow(0)
        self.pool_list.blockSignals(False)
        self._refresh_members()

    def _refresh_members(self) -> None:
        pool = self._current_pool()
        self.members_lbl.setText(f"In '{pool}'" if pool else "In pool")
        self.avail_list.clear()
        self.member_list.clear()
        self.avail_list.setEnabled(bool(pool))
        self.member_list.setEnabled(bool(pool))
        if not pool:
            return
        for w in self._workers:
            member_pools = w.get("pools") or []
            it = QListWidgetItem(w["name"])
            it.setData(Qt.UserRole, w["id"])
            (self.member_list if pool in member_pools else self.avail_list).addItem(it)

    def _member_ids(self) -> list:
        return [self.member_list.item(i).data(Qt.UserRole)
                for i in range(self.member_list.count())]

    def _put_members(self, ids: list, verb: str, n: int) -> None:
        pool = self._current_pool()
        try:
            requests.put(f"{self.base}/api/pools/{pool}",
                         json={"workers": ids}, timeout=5)
        except Exception as exc:
            QMessageBox.warning(self, "Error", str(exc))
            return
        # Update the local cache and redraw immediately — no second network
        # round-trip, so the lists update instantly and never go stale.
        idset = set(ids)
        for w in self._workers:
            pools = set(w.get("pools") or [])
            if w["id"] in idset:
                pools.add(pool)
            else:
                pools.discard(pool)
            w["pools"] = sorted(pools)
        self.status.setText(f"{verb} {n} worker(s) — '{pool}' now has {len(ids)}")
        self._refresh_members()

    # -------------------------------------------------------- actions ----
    def _assign(self) -> None:
        if not self._current_pool():
            return
        picked = [it.data(Qt.UserRole) for it in self.avail_list.selectedItems()]
        if not picked:
            self.status.setText("Select workers on the left first.")
            return
        self._put_members(self._member_ids() + picked, "Assigned", len(picked))

    def _remove(self) -> None:
        if not self._current_pool():
            return
        picked = {it.data(Qt.UserRole) for it in self.member_list.selectedItems()}
        if not picked:
            self.status.setText("Select workers in the pool first.")
            return
        remaining = [i for i in self._member_ids() if i not in picked]
        self._put_members(remaining, "Removed", len(picked))

    def _new_pool(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "New pool", "Pool name:")
        name = name.strip()
        if not ok or not name:
            return
        try:
            requests.post(f"{self.base}/api/pools", json={"name": name}, timeout=5)
        except Exception as exc:
            QMessageBox.warning(self, "Error", str(exc))
            return
        self.status.setText(f"Pool '{name}' created — assign workers to it")
        self._reload(keep_pool=name)

    def _delete_pool(self) -> None:
        pool = self._current_pool()
        if not pool:
            return
        if QMessageBox.question(self, "Delete pool",
                                f"Delete pool '{pool}'?\nIts workers are released.") != QMessageBox.Yes:
            return
        try:
            requests.delete(f"{self.base}/api/pools/{pool}", timeout=5)
        except Exception as exc:
            QMessageBox.warning(self, "Error", str(exc))
            return
        self.status.setText(f"Pool '{pool}' deleted")
        self._reload()


class CompactColumnsDialog(QDialog):
    """Pick which job columns the compact view keeps. It stays open so several
    can be ticked in one go — a menu of checkable actions closes on every click,
    which made multi-column changes tedious."""

    def __init__(self, headers, chosen, on_toggle, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Compact Columns")
        self._on_toggle = on_toggle
        lay = QVBoxLayout(self)
        hint = QLabel("Ticked columns stay visible in Compact mode.")
        hint.setObjectName("hdr")
        lay.addWidget(hint)
        self.boxes: list[QCheckBox] = []
        for c, name in enumerate(headers):
            cb = QCheckBox(name)
            cb.setChecked(c in chosen)
            cb.toggled.connect(lambda on, col=c: self._on_toggle(col, on))
            lay.addWidget(cb)
            self.boxes.append(cb)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def sync(self, chosen) -> None:
        """Reflect a programmatic change (e.g. the keep-at-least-one fallback)
        without re-firing the toggle handler."""
        chosen = set(chosen)
        for c, cb in enumerate(self.boxes):
            want = c in chosen
            if cb.isChecked() != want:
                cb.blockSignals(True)
                cb.setChecked(want)
                cb.blockSignals(False)


# ------------------------------------------------------------- widget ---------
JOB_HEADERS = ["Job Name", "DCC", "Renderer", "Frames", "Progress", "Job ID",
               "Priority", "Submitter", "Worker", "Submit Time", "Start Time",
               "Job Done Time", "Render Time", "Status"]
# Comfortable starting widths (clamped up to each header's width at build time).
JOB_COL_WIDTHS = [200, 60, 90, 90, 140, 110, 100, 120, 110, 140, 140, 140, 110, 90]


class MonitorWidget(QWidget):
    statusChanged = Signal(str, str)  # text, color

    def __init__(self) -> None:
        super().__init__()
        self.manager_url = settings.load().get("manager_url", "http://127.0.0.1:8080")
        self.super_mode = False  # gated worker editing
        self._jobs_sig = None
        self._detail_id = None
        self._collapsed: set = set()   # batch keys the user collapsed
        self._groups: dict = {}
        self._filters: dict = {}
        self._worker_names: dict = {}
        # Desktop notifications when a job finishes / fails. We diff each poll's
        # job statuses against the last to fire only on a real transition.
        s = settings.load()
        self.notifier = Notifier("Muffin Monitor", self)
        self.notifier.enabled = s.get("monitor_notify", True)
        self.notify_done = s.get("monitor_notify_done", True)
        self.notify_failed = s.get("monitor_notify_failed", True)
        self._job_status: dict[str, str] = {}
        self._notify_primed = False  # skip the first poll so we don't toast history
        self._build()
        self.poller = Poller(lambda: self.manager_url or "http://127.0.0.1:8080",
                             lambda: self._detail_id)
        self.poller.data.connect(self._on_data)
        self.poller.start()

    def set_manager_url(self, url: str) -> None:
        self.manager_url = url.rstrip("/")
        self.force_refresh()

    def force_refresh(self) -> None:
        self._jobs_sig = None
        self.poller.refresh_now()

    # ---------------------------------------------------------------- ui ----
    def _build(self) -> None:
        root = QVBoxLayout(self)
        self.split = QSplitter(Qt.Vertical)

        # Jobs: a tree, so multi-scene submissions expand into scene jobs.
        # Multi-select lets several jobs be started/stopped at once.
        t = FitTreeWidget()
        t.setColumnCount(len(JOB_HEADERS))
        t.setHeaderLabels(JOB_HEADERS)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setSelectionMode(QAbstractItemView.ExtendedSelection)
        # Scrollbar appears only when the columns can't fit at readable widths;
        # last section is NOT stretched, so dragging one column pushes the rest.
        t.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        h = t.header()
        h.setSectionResizeMode(QHeaderView.Interactive)
        h.setSectionsMovable(True)
        h.setStretchLastSection(False)
        h.setCascadingSectionResizes(False)
        _apply_default_widths(t, JOB_COL_WIDTHS)
        t.setSortingEnabled(True)
        h.setSortIndicator(-1, Qt.AscendingOrder)
        t.setItemDelegateForColumn(4, ProgressDelegate(t))
        t.setContextMenuPolicy(Qt.CustomContextMenu)
        t.customContextMenuRequested.connect(self._job_menu)
        t.itemDoubleClicked.connect(self._job_double_clicked)
        t.itemSelectionChanged.connect(self._on_job_selected)
        t.itemExpanded.connect(lambda it: self._collapsed.discard(it.data(0, Qt.UserRole)))
        t.itemCollapsed.connect(lambda it: self._collapsed.add(it.data(0, Qt.UserRole)))
        self.jobs_table = t

        jobs_box = QWidget()
        jlay = QVBoxLayout(jobs_box)
        jlay.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        lbl = QLabel("Jobs")
        lbl.setObjectName("hdr")
        head.addWidget(lbl)
        head.addSpacing(14)
        # Quick status filter: one dropdown with checkable entries.
        from PySide6.QtWidgets import QToolButton
        self._status_filter: set = set()
        self.filter_btn = QToolButton()
        self.filter_btn.setText("Show: all ▾")
        self.filter_btn.setPopupMode(QToolButton.InstantPopup)
        fmenu = QMenu(self.filter_btn)
        self._filter_acts = []
        for text, statuses in (("Rendering", ("running",)),
                               ("Done", ("done",)),
                               ("Queue", ("queued", "requeued"))):
            act = QAction(text, fmenu)
            act.setCheckable(True)
            act.toggled.connect(
                lambda on, st=statuses: self._toggle_status_filter(st, on))
            fmenu.addAction(act)
            self._filter_acts.append(act)
        fmenu.addSeparator()
        clear_act = QAction("Clear all", fmenu)
        clear_act.triggered.connect(
            lambda: [a.setChecked(False) for a in self._filter_acts])
        fmenu.addAction(clear_act)
        self.filter_btn.setMenu(fmenu)
        head.addWidget(self.filter_btn)
        head.addSpacing(8)
        collapse_btn = QToolButton()
        collapse_btn.setText("⊟ Collapse all")
        collapse_btn.setToolTip("Collapse every multi-job submission")
        collapse_btn.clicked.connect(self._collapse_all_jobs)
        head.addWidget(collapse_btn)
        expand_btn = QToolButton()
        expand_btn.setText("⊞ Expand all")
        expand_btn.setToolTip("Expand every multi-job submission")
        expand_btn.clicked.connect(self._expand_all_jobs)
        head.addWidget(expand_btn)
        head.addStretch()
        search = QLineEdit()
        search.setPlaceholderText("Search jobs…")
        search.setClearButtonEnabled(True)
        search.setMaximumWidth(260)
        search.textChanged.connect(lambda text: self._set_filter(t, text))
        head.addWidget(search)
        jlay.addLayout(head)
        jlay.addWidget(t)
        self.jobs_counts = QLabel("—")
        # Green to match the live status bar at the bottom (not the grey "hdr").
        self.jobs_counts.setStyleSheet("color:#4caf72; font-size:12px;")
        jlay.addWidget(self.jobs_counts)
        self.split.addWidget(jobs_box)

        self.tasks_table = self._table(
            ["Task Name", "Task ID", "Frames", "Progress", "Status", "Priority",
             "Worker", "Start Time", "End Time", "Render Time"],
            [160, 110, 90, 140, 90, 100, 110, 150, 150, 110])
        self.tasks_table.setItemDelegateForColumn(3, ProgressDelegate(self.tasks_table))
        self.tasks_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tasks_table.customContextMenuRequested.connect(self._tasks_menu)
        self.tasks_table.itemDoubleClicked.connect(self._task_double_clicked)
        self.tasks_label = QLabel("Tasks — select a job above")
        self.tasks_label.setObjectName("hdr")
        tasks_box = QWidget()
        tlay = QVBoxLayout(tasks_box)
        tlay.setContentsMargins(0, 0, 0, 0)
        tlay.addWidget(self.tasks_label)
        tlay.addWidget(self.tasks_table)
        self.split.addWidget(tasks_box)

        self.workers_table = self._table(
            ["Name", "Pool", "Status", "Current Job", "Task ID", "Last Job",
             "CPU", "GPU", "RAM", "Last active"],
            [150, 90, 100, 150, 110, 150, 120, 120, 90, 150])
        self.workers_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.workers_table.customContextMenuRequested.connect(self._worker_menu)
        self.split.addWidget(self._titled("Workers", self.workers_table, searchable=True))

        self.split.setSizes([340, 160, 220])
        root.addWidget(self.split, 1)

    def _titled(self, title: str, view: QWidget, searchable: bool = False) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        lbl = QLabel(title)
        lbl.setObjectName("hdr")
        head.addWidget(lbl)
        if searchable:
            search = QLineEdit()
            search.setPlaceholderText(f"Search {title.lower()}…")
            search.setClearButtonEnabled(True)
            search.setMaximumWidth(260)
            search.textChanged.connect(
                lambda text, v=view: self._set_filter(v, text))
            head.addStretch()
            head.addWidget(search)
        lay.addLayout(head)
        lay.addWidget(view)
        return w

    def _table(self, headers: list[str], widths: list[int] = None) -> QTableWidget:
        t = FitTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setSelectionMode(QAbstractItemView.SingleSelection)
        h = t.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.Interactive)
        h.setSectionsMovable(True)
        h.setStretchLastSection(False)
        h.setCascadingSectionResizes(False)
        t.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        _apply_default_widths(t, widths or [160] + [100] * (len(headers) - 1))
        t.setSortingEnabled(True)
        h.setSortIndicator(-1, Qt.AscendingOrder)
        return t

    # ------------------------------------------------------------- filter ----
    def _toggle_status_filter(self, statuses: tuple, on: bool) -> None:
        for st in statuses:
            (self._status_filter.add if on else self._status_filter.discard)(st)
        active = [a.text().lower() for a in self._filter_acts if a.isChecked()]
        self.filter_btn.setText(
            f"Show: {', '.join(active)} ▾" if active else "Show: all ▾")
        self._apply_filter(self.jobs_table)

    def _set_filter(self, view, text: str) -> None:
        self._filters[id(view)] = text.strip().lower()
        self._apply_filter(view)

    def _apply_filter(self, view) -> None:
        text = self._filters.get(id(view), "")
        if view is self.jobs_table:
            active = bool(text) or bool(self._status_filter)
            for i in range(view.topLevelItemCount()):
                top = view.topLevelItem(i)
                kids = [top.child(k) for k in range(top.childCount())]
                kid_match = [self._job_visible(c, text) for c in kids]
                top_match = self._job_visible(top, text) or any(kid_match)
                top.setHidden(active and not top_match)
                for c, m in zip(kids, kid_match):
                    c.setHidden(active and not m)
            return
        for r in range(view.rowCount()):
            if not text:
                view.setRowHidden(r, False)
                continue
            match = any(
                view.item(r, c) and text in view.item(r, c).text().lower()
                for c in range(view.columnCount()))
            view.setRowHidden(r, not match)

    def _job_visible(self, item: QTreeWidgetItem, text: str) -> bool:
        if self._status_filter:
            if item.data(0, Qt.UserRole + 2) not in self._status_filter:
                return False
        return self._item_matches(item, text)

    @staticmethod
    def _item_matches(item: QTreeWidgetItem, text: str) -> bool:
        if not text:
            return True
        return any(text in item.text(c).lower() for c in range(item.columnCount()))

    # ------------------------------------------------------------- data ----
    def _on_data(self, payload: dict) -> None:
        if not payload["ok"]:
            self.statusChanged.emit(f"offline — {payload['error']}", "#e0594f")
            return
        jobs, workers = payload["jobs"], payload["workers"]
        online = sum(1 for w in workers if w["status"] != "offline")
        rendering_w = sum(1 for w in workers if w["status"] == "busy")
        # The status bar covers connection + workers; job counts live under
        # the jobs list.
        self.statusChanged.emit(
            f"live   ·   workers: {online}/{len(workers)} online, "
            f"{rendering_w} rendering", "#4caf72")
        self._update_job_counts(jobs)
        self._check_notifications(jobs)
        self._worker_names = {w["id"]: w["name"] for w in workers}
        self._fill_jobs(jobs)
        self._fill_workers(workers)
        self._fill_tasks(payload.get("detail"))

    def _check_notifications(self, jobs: list[dict]) -> None:
        """Toast when a job crosses into done/failed since the last poll."""
        prev, cur = self._job_status, {}
        for j in jobs:
            jid, st = j["id"], j["status"]
            cur[jid] = st
            if not self._notify_primed or prev.get(jid) == st:
                continue
            if st == "done" and self.notify_done:
                self.notifier.notify(
                    "Render finished", f"'{j['name']}' is done.", "info")
            elif st == "failed" and self.notify_failed:
                self.notifier.notify(
                    "Render failed", f"'{j['name']}' failed.", "critical")
        self._job_status = cur
        self._notify_primed = True

    def _update_job_counts(self, jobs: list[dict]) -> None:
        by = lambda *sts: sum(1 for j in jobs if j["status"] in sts)  # noqa: E731
        rendering_tasks = sum(j.get("task_running", 0) for j in jobs)
        self.jobs_counts.setText(
            f"Rendering: {by('running')} jobs / {rendering_tasks} tasks   ·   "
            f"Queued: {by('queued', 'requeued')}   ·   "
            f"Paused: {by('paused')}   ·   "
            f"Done: {by('done')}   ·   "
            f"Failed: {by('failed')}   ·   "
            f"Total: {len(jobs)} jobs")

    # ------------------------------------------------------- jobs (tree) ----
    def _job_item(self, j: dict, parent_label: str = "") -> SortableTreeItem:
        it = SortableTreeItem()
        is_batch = "_members" in j
        name = f"{j['name']}  ({len(j['_members'])} jobs)" if is_batch else j["name"]
        pct = _job_pct(j)
        label = f"{pct}%   ({j['task_done']}/{j['task_total']}"
        if j["task_failed"]:
            label += f", {j['task_failed']}✗"
        label += ")"
        submitter = (j.get("submitter") or "").split("@")[0]
        workers_str = j.get("workers") or ""
        worker_disp = ("multiple" if "," in workers_str else workers_str) or "—"
        values = {
            0: name,
            1: j["dcc"],
            2: j.get("renderer") or "—",
            3: f"{j['frame_start']}–{j['frame_end']}",
            4: "",
            5: j["id"].split("::")[-1] if is_batch else j["id"],
            6: priority.label_for(j.get("priority", 0)),
            7: submitter or "—",
            8: worker_disp,
            9: _fmt_time(j.get("created_at")),
            10: _fmt_time(j.get("render_start")),
            11: _fmt_time(j.get("finished_at")),
            12: _job_render_total(j),
            13: _display_status(j["status"]),
        }
        color = QColor(_JOB_ROW_COLORS.get(j["status"], "#d8dce3"))
        for c, v in values.items():
            it.setText(c, v)
            it.setForeground(c, color)
        it.setData(0, Qt.UserRole, j["id"])
        it.setData(0, Qt.UserRole + 1, j.get("output_path", ""))
        it.setData(0, Qt.UserRole + 2, j["status"])
        it.setData(0, Qt.UserRole + 3, j.get("_members"))
        # progress delegate data + numeric sort keys
        it.setData(4, Qt.UserRole, pct)
        it.setData(4, Qt.UserRole + 1, _JOB_ROW_COLORS.get(j["status"], "#e8a13a"))
        it.setData(4, Qt.UserRole + 2, label)
        it.setData(4, _SORT_ROLE, pct)
        it.setData(6, _SORT_ROLE, int(j.get("priority", 0)))
        return it

    def _fill_jobs(self, jobs: list[dict]) -> None:
        any_running = any(j["status"] == "running" for j in jobs)
        sig = [(j["id"], j["status"], round(j.get("progress", 0), 3),
                j["task_done"], j["task_total"], j["task_failed"],
                j.get("current_tasks", ""), j.get("finished_at"),
                j.get("priority"), j.get("workers", ""),
                int(time.time()) // 5 if any_running else 0) for j in jobs]
        if sig == self._jobs_sig:
            return
        self._jobs_sig = sig

        # Group batch members under one parent (jobs arrive newest-first).
        entries, groups = [], {}
        for j in jobs:
            b = j.get("batch") or ""
            if not b:
                entries.append(j)
                continue
            if b not in groups:
                groups[b] = []
                entries.append(b)
            groups[b].append(j)
        self._groups = groups

        t = self.jobs_table
        sel = self._selected_job_id()
        t.setUpdatesEnabled(False)
        t.setSortingEnabled(False)
        t.blockSignals(True)
        t.clear()
        for entry in entries:
            if isinstance(entry, str):           # a batch key
                members = groups[entry]
                top = self._job_item(_batch_summary(entry, members))
                t.addTopLevelItem(top)
                for m in members:
                    top.addChild(self._job_item(m))
                top.setExpanded(top.data(0, Qt.UserRole) not in self._collapsed)
            else:
                t.addTopLevelItem(self._job_item(entry))
        t.blockSignals(False)
        t.setSortingEnabled(True)
        t.setUpdatesEnabled(True)
        self._apply_filter(t)
        if sel:
            self._reselect_job(sel)

    def _iter_job_items(self):
        t = self.jobs_table
        for i in range(t.topLevelItemCount()):
            top = t.topLevelItem(i)
            yield top
            for k in range(top.childCount()):
                yield top.child(k)

    def _collapse_all_jobs(self) -> None:
        """Collapse every multi-job (batch) row, and remember it so refreshes
        keep them collapsed."""
        t = self.jobs_table
        for i in range(t.topLevelItemCount()):
            top = t.topLevelItem(i)
            if top.childCount():
                self._collapsed.add(top.data(0, Qt.UserRole))
        t.collapseAll()

    def _expand_all_jobs(self) -> None:
        t = self.jobs_table
        for i in range(t.topLevelItemCount()):
            top = t.topLevelItem(i)
            if top.childCount():
                self._collapsed.discard(top.data(0, Qt.UserRole))
        t.expandAll()

    def _selected_job_id(self):
        items = self.jobs_table.selectedItems()
        return items[0].data(0, Qt.UserRole) if items else None

    def _reselect_job(self, ident) -> None:
        for it in self._iter_job_items():
            if it.data(0, Qt.UserRole) == ident:
                self.jobs_table.blockSignals(True)
                self.jobs_table.setCurrentItem(it)
                self.jobs_table.blockSignals(False)
                return

    def _on_job_selected(self) -> None:
        # With multi-select, the tasks panel follows the current (last-clicked) item.
        cur = self.jobs_table.currentItem()
        sel = cur.data(0, Qt.UserRole) if cur and cur.isSelected() else None
        if isinstance(sel, str) and sel.startswith("batch:"):
            # Parent row: tasks belong to the individual scene jobs.
            self._detail_id = None
            self.tasks_label.setText("Tasks — select a scene job to see its tasks")
            self.tasks_table.setRowCount(0)
        else:
            self._detail_id = sel
            if not sel:
                self.tasks_label.setText("Tasks — select a job above")
                self.tasks_table.setRowCount(0)
        self.poller.refresh_now()

    # ------------------------------------------------------------- tasks ----
    def _fill_tasks(self, detail) -> None:
        t = self.tasks_table
        if self._detail_id and detail is None:
            # The selected job no longer exists (deleted) — clear the panel.
            self.tasks_label.setText("Tasks — select a job above")
            t.setRowCount(0)
            return
        if not detail or detail.get("id") != self._detail_id:
            return
        self.tasks_label.setText(f"Tasks — {detail['name']}")
        tasks = detail.get("tasks", [])
        t.setUpdatesEnabled(False)
        t.setSortingEnabled(False)
        t.setRowCount(len(tasks))
        status_color = {"done": "#4caf72", "running": "#4a90d9", "assigned": "#4a90d9",
                        "failed": "#e0594f", "queued": "#d8dce3"}
        job_name = detail["name"]
        for r, task in enumerate(tasks):
            hex_color = status_color.get(task["status"], "#d8dce3")
            color = QColor(hex_color)
            pct = 100 if task["status"] == "done" else min(
                99, int(round((task.get("progress") or 0) * 100)))
            prog = QTableWidgetItem()
            prog.setData(Qt.UserRole, pct)
            prog.setData(Qt.UserRole + 1, hex_color)
            prog.setData(Qt.EditRole, pct)
            worker = self._worker_names.get(task.get("worker_id"), task.get("worker_id") or "—")
            prio = QTableWidgetItem(priority.label_for(detail.get("priority", 0)))
            cells = [
                QTableWidgetItem(job_name),
                QTableWidgetItem(task["id"]),
                QTableWidgetItem(f"{task['frame_start']}–{task['frame_end']}"),
                prog,
                QTableWidgetItem(_display_status(task["status"])),
                prio,
                QTableWidgetItem(worker),
                QTableWidgetItem(_fmt_time_secs(task.get("started_at"))),
                QTableWidgetItem(_fmt_time_secs(task.get("finished_at"))),
                QTableWidgetItem(_task_render_time(task)),
            ]
            # Stash the task's id + its owning job id on the row so a double-
            # click resolves the right log even if the job selection (and so
            # _detail_id) has since changed, and regardless of column order.
            cells[0].setData(Qt.UserRole, task["id"])
            cells[0].setData(Qt.UserRole + 1, detail["id"])
            for c, it in enumerate(cells):
                if c != 3:
                    it.setForeground(color)
                t.setItem(r, c, it)
        t.setSortingEnabled(True)
        t.setUpdatesEnabled(True)

    # ------------------------------------------------------------ workers ----
    def _fill_workers(self, workers: list[dict]) -> None:
        sel = self._selected_id(self.workers_table)
        t = self.workers_table
        t.setUpdatesEnabled(False)
        t.setSortingEnabled(False)
        t.setRowCount(len(workers))
        for r, w in enumerate(workers):
            color = QColor(_worker_row_color(w))
            name = QTableWidgetItem(w["name"])
            name.setData(Qt.UserRole, w["id"])
            name.setData(Qt.UserRole + 1, bool(w.get("enabled", 1)))
            task_id = w.get("current_task_id") or w.get("last_task_id") or "—"
            if not w.get("enabled", 1):
                status_text = "disabled"
            elif w.get("standby") and w.get("status") != "busy":
                status_text = "scheduled off"
            else:
                status_text = _display_status(w["status"])
            cells = [
                name,
                QTableWidgetItem(w.get("pool") or "—"),
                QTableWidgetItem(status_text),
                QTableWidgetItem(w.get("current_job_name") or "—"),
                QTableWidgetItem(task_id),
                QTableWidgetItem(w.get("last_job_name") or "—"),
                QTableWidgetItem(w.get("cpu") or "—"),
                QTableWidgetItem(w.get("gpu") or "—"),
                QTableWidgetItem(w.get("ram") or "—"),
                QTableWidgetItem(_fmt_time_secs(w.get("last_seen", 0))),
            ]
            for c, it in enumerate(cells):
                it.setForeground(color)
                t.setItem(r, c, it)
        t.setSortingEnabled(True)
        t.setUpdatesEnabled(True)
        self._apply_filter(t)
        if sel:
            self._reselect(t, sel)

    def _selected_id(self, table: QTableWidget):
        items = table.selectedItems()
        if not items:
            return None
        return table.item(items[0].row(), 0).data(Qt.UserRole)

    def _reselect(self, table: QTableWidget, ident) -> None:
        for r in range(table.rowCount()):
            if table.item(r, 0).data(Qt.UserRole) == ident:
                table.blockSignals(True)
                table.selectRow(r)
                table.blockSignals(False)
                return

    # ------------------------------------------------------- job actions ----
    def _empty_space_menu(self, view, pos) -> None:
        menu = QMenu(self)
        a = menu.addAction("⟳ Refresh")
        if menu.exec(view.viewport().mapToGlobal(pos)) == a:
            self.force_refresh()

    def _tasks_menu(self, pos) -> None:
        if not self.tasks_table.itemAt(pos):
            self._empty_space_menu(self.tasks_table, pos)
            return
        menu = QMenu(self)
        a_refresh = menu.addAction("⟳ Refresh")
        if menu.exec(self.tasks_table.viewport().mapToGlobal(pos)) == a_refresh:
            self.force_refresh()

    def _job_menu(self, pos) -> None:
        item = self.jobs_table.itemAt(pos)
        if not item:
            self._empty_space_menu(self.jobs_table, pos)
            return
        # Apply to the whole selection when the clicked row is part of it.
        sel_items = self.jobs_table.selectedItems()
        if item not in sel_items:
            sel_items = [item]
        targets, statuses, single_members = [], [], None
        for it in sel_items:
            members = it.data(0, Qt.UserRole + 3)
            statuses.append(it.data(0, Qt.UserRole + 2))
            if members:
                targets += [m for m in members if m not in targets]
                if len(sel_items) == 1:
                    single_members = members
            else:
                jid = it.data(0, Qt.UserRole)
                if jid not in targets:
                    targets.append(jid)
        multi = len(targets) > 1

        menu = QMenu(self)
        a_edit = menu.addAction("✎ Edit job…")
        a_edit.setEnabled(len(sel_items) == 1)
        a_log = menu.addAction("📜 View log")
        a_log.setEnabled(len(sel_items) == 1)
        menu.addSeparator()
        a_open = menu.addAction("📂 Open Render Output")
        menu.addSeparator()
        suffix = f"  ({len(targets)} jobs)" if multi else ""
        a_start = menu.addAction("▶ Start" + suffix)
        a_start.setEnabled("requeued" in statuses)  # only requeued jobs can start
        a_pause = menu.addAction("⏸ Pause" + suffix)
        a_resume = menu.addAction("▶ Resume" + suffix)
        a_requeue = menu.addAction("↻ Requeue" + suffix)
        a_cancel = menu.addAction("✖ Cancel" + suffix)
        menu.addSeparator()
        a_delete = menu.addAction("🗑 Delete" + suffix)

        chosen = menu.exec(self.jobs_table.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen == a_start:
            for jid in targets:
                self._req("post", f"/api/jobs/{jid}/start", show_err=not multi)
        elif chosen == a_edit:
            self._edit_job(targets[0], group_ids=single_members)
        elif chosen == a_log:
            self._show_job_log_by_id(item.data(0, Qt.UserRole))
        elif chosen == a_open:
            self._open_output(item.data(0, Qt.UserRole + 1), targets[0])
        elif chosen == a_pause:
            for jid in targets:
                self._req("post", f"/api/jobs/{jid}/pause")
        elif chosen == a_resume:
            for jid in targets:
                self._req("post", f"/api/jobs/{jid}/resume")
        elif chosen == a_requeue:
            for jid in targets:
                self._req("post", f"/api/jobs/{jid}/retry")
        elif chosen == a_cancel:
            for jid in targets:
                self._req("post", f"/api/jobs/{jid}/cancel")
        elif chosen == a_delete:
            what = f"{len(targets)} jobs" if multi else "this job"
            if QMessageBox.question(self, "Delete job", f"Delete {what}?") == QMessageBox.Yes:
                for jid in targets:
                    self._req("delete", f"/api/jobs/{jid}")

    def _edit_job(self, job_id: str, group_ids=None) -> None:
        job = self._fetch_job(job_id)
        if not job:
            return
        dlg = JobEditDialog(job, self, group=bool(group_ids))
        if dlg.exec():
            payload = dlg.payload()
            for jid in (group_ids or [job_id]):
                self._req("put", f"/api/jobs/{jid}", json=payload, show_err=True)

    def _job_double_clicked(self, item, _col) -> None:
        self._show_job_log_by_id(item.data(0, Qt.UserRole))

    def _task_double_clicked(self, item, _col) -> None:
        """Double-clicking a task row shows that one task's log — the per-task
        counterpart of double-clicking a job. Resolves the task by the ids
        stashed on the row (not _detail_id / a fixed column), so it stays correct
        if the job selection changed or the columns were reordered."""
        anchor = self.tasks_table.item(item.row(), 0)
        if anchor is None:
            return
        task_id = anchor.data(Qt.UserRole)
        job_id = anchor.data(Qt.UserRole + 1)
        if not task_id or not job_id:
            return
        job = self._fetch_job(job_id)
        if not job:
            return
        task = next((t for t in job.get("tasks", []) if t.get("id") == task_id), None)
        if task is None:
            QMessageBox.information(self, "Task log", "No log for this task yet.")
            return
        JobLogDialog({"name": f"{job.get('name', '')} / task {task_id}",
                      "tasks": [task]}, self).exec()

    def _show_job_log_by_id(self, job_id) -> None:
        if isinstance(job_id, str) and job_id.startswith("batch:"):
            members = self._groups.get(job_id[len("batch:"):], [])
            tasks = []
            for m in members:
                detail = self._fetch_job(m["id"])
                if detail:
                    for task in detail.get("tasks", []):
                        task = dict(task)
                        task["id"] = f"{m['name']} / {task['id']}"
                        tasks.append(task)
            JobLogDialog({"name": job_id[len('batch:'):].rsplit('::', 1)[0],
                          "tasks": tasks}, self).exec()
            return
        job = self._fetch_job(job_id)
        if job:
            JobLogDialog(job, self).exec()

    def _fetch_job(self, job_id: str):
        try:
            return requests.get(f"{self._base()}/api/jobs/{job_id}", timeout=5).json()
        except Exception as exc:
            QMessageBox.warning(self, "Error", str(exc))
            return None

    # ---------------------------------------------------- worker actions ----
    def _worker_menu(self, pos) -> None:
        item = self.workers_table.itemAt(pos)
        if not item:
            self._empty_space_menu(self.workers_table, pos)
            return
        if not self.super_mode:
            QMessageBox.information(
                self, "Super Muffin Mode",
                "Worker editing is locked.\nEnable Edit ▸ Super Muffin Mode first.")
            return
        cell = self.workers_table.item(item.row(), 0)
        worker_id = cell.data(Qt.UserRole)
        enabled = cell.data(Qt.UserRole + 1)

        menu = QMenu(self)
        a_edit = menu.addAction("✎ Edit worker…")
        a_toggle = menu.addAction("⏸ Disable" if enabled else "▶ Enable")
        menu.addSeparator()
        a_remove = menu.addAction("🗑 Remove worker")

        chosen = menu.exec(self.workers_table.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen == a_edit:
            self._edit_worker(worker_id)
        elif chosen == a_toggle:
            self._req("post", f"/api/workers/{worker_id}/{'disable' if enabled else 'enable'}")
        elif chosen == a_remove:
            if QMessageBox.question(self, "Remove worker",
                                    "Remove this worker? Its current task is requeued.") == QMessageBox.Yes:
                self._req("delete", f"/api/workers/{worker_id}")

    def _edit_worker(self, worker_id: str) -> None:
        try:
            workers = requests.get(f"{self._base()}/api/workers", timeout=5).json()
        except Exception as exc:
            QMessageBox.warning(self, "Error", str(exc))
            return
        worker = next((w for w in workers if w["id"] == worker_id), None)
        if not worker:
            return
        dlg = WorkerEditDialog(worker, self)
        if dlg.exec():
            action = "enable" if dlg.is_enabled() else "disable"
            self._req("post", f"/api/workers/{worker_id}/{action}")

    def _open_output(self, output_path: str, job_id: str) -> None:
        """Open the render folder on THIS machine (works when the manager runs
        on a NAS). Falls back to the manager-side reveal for paths that only
        exist there."""
        import os

        folder = ""
        if output_path:
            folder = output_path if os.path.isdir(output_path) \
                else os.path.dirname(output_path)
        if folder and os.path.isdir(folder):
            try:
                if hasattr(os, "startfile"):
                    os.startfile(folder)  # noqa: S606 — local convenience
                else:
                    import subprocess
                    import sys
                    opener = "open" if sys.platform == "darwin" else "xdg-open"
                    subprocess.Popen([opener, folder])
                return
            except Exception:
                pass
        self._req("post", f"/api/jobs/{job_id}/reveal", show_err=True)

    # --------------------------------------------------------------- net ----
    def _base(self) -> str:
        return (self.manager_url or "http://127.0.0.1:8080").rstrip("/")

    def _req(self, method: str, path: str, json: dict = None, show_err: bool = False) -> None:
        try:
            r = requests.request(method, self._base() + path, json=json, timeout=5)
            if show_err and not r.ok:
                QMessageBox.warning(self, "Error", r.text)
        except Exception as exc:
            QMessageBox.warning(self, "Error", str(exc))
        self.force_refresh()


# ------------------------------------------------------------- window ---------
class MonitorWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Muffin's Monitor")
        self.setWindowIcon(app_icon())
        self.resize(1100, 760)
        self.view = MonitorWidget()
        self.setCentralWidget(self.view)
        self.view.statusChanged.connect(self._status)

        # Single Edit menu. Worker/farm management lives at the bottom and is
        # hidden until Super Muffin Mode is enabled.
        e = self.menuBar().addMenu("Edit")
        self.super_act = QAction("Super Muffin Mode", self)
        self.super_act.setCheckable(True)
        self.super_act.toggled.connect(self._toggle_super)
        e.addAction(self.super_act)
        e.addSeparator()
        self.edit_workers_act = QAction("Edit workers…", self)
        self.edit_workers_act.triggered.connect(self._edit_workers)
        e.addAction(self.edit_workers_act)
        self.add_pool_act = QAction("Pool Management…", self)
        self.add_pool_act.triggered.connect(self._add_pool)
        e.addAction(self.add_pool_act)
        self.url_act = QAction("Manager URL…", self)
        self.url_act.triggered.connect(self._edit_url)
        e.addAction(self.url_act)
        for act in (self.edit_workers_act, self.add_pool_act, self.url_act):
            act.setVisible(False)

        # Layout menu — save the current arrangement or go back to default
        lm = self.menuBar().addMenu("Layout")
        self.compact_act = QAction("Compact Job List", self)
        self.compact_act.setCheckable(True)
        self.compact_act.toggled.connect(self._set_compact)
        lm.addAction(self.compact_act)
        # Compact Columns — a pop-up so several columns can be ticked at once.
        self._compact_dialog: CompactColumnsDialog | None = None
        cols_act = QAction("Compact Columns…", self)
        cols_act.triggered.connect(self._open_compact_columns)
        lm.addAction(cols_act)
        lm.addSeparator()
        save_lay = QAction("Save Current Layout", self)
        save_lay.triggered.connect(self._save_layout_clicked)
        lm.addAction(save_lay)
        load_lay = QAction("Load Saved Layout", self)
        load_lay.triggered.connect(self._load_layout_clicked)
        lm.addAction(load_lay)
        reset_lay = QAction("Reset Layout", self)
        reset_lay.triggered.connect(self._reset_layout)
        lm.addAction(reset_lay)
        lm.addSeparator()
        # Columns are manual (spreadsheet-style): resizing one pushes the rest
        # and a horizontal scrollbar appears when needed; the window resizing
        # never disturbs them. This fills them to the window on demand.
        fit_act = QAction("Fit columns to window", self)
        fit_act.triggered.connect(self._fit_columns_now)
        lm.addAction(fit_act)

        # Schedule menu — set when each worker is free to render (out of hours).
        schm = self.menuBar().addMenu("Schedule")
        sched_act = QAction("Worker Schedules…", self)
        sched_act.triggered.connect(self._worker_schedules)
        schm.addAction(sched_act)

        # Notifications menu — toast when a job finishes / fails.
        nm = self.menuBar().addMenu("Notifications")
        self.notify_act = QAction("Enable notifications", self)
        self.notify_act.setCheckable(True)
        self.notify_act.setChecked(self.view.notifier.enabled)
        self.notify_act.toggled.connect(self._toggle_notify)
        nm.addAction(self.notify_act)
        nm.addSeparator()
        self.notify_done_act = QAction("Notify when a render finishes", self)
        self.notify_done_act.setCheckable(True)
        self.notify_done_act.setChecked(self.view.notify_done)
        self.notify_done_act.toggled.connect(self._toggle_notify_done)
        nm.addAction(self.notify_done_act)
        self.notify_failed_act = QAction("Notify when a render fails", self)
        self.notify_failed_act.setCheckable(True)
        self.notify_failed_act.setChecked(self.view.notify_failed)
        self.notify_failed_act.toggled.connect(self._toggle_notify_failed)
        nm.addAction(self.notify_failed_act)

        # Refresh — a button on the right side of the menu bar (also F5).
        from PySide6.QtWidgets import QToolButton
        self.refresh_btn = QToolButton(self.menuBar())
        self.refresh_btn.setText("⟳ Refresh")
        self.refresh_btn.clicked.connect(self.view.force_refresh)
        self.menuBar().setCornerWidget(self.refresh_btn, Qt.TopRightCorner)
        refresh_act = QAction("Refresh", self)
        refresh_act.setShortcut(QKeySequence(Qt.Key_F5))
        refresh_act.triggered.connect(self.view.force_refresh)
        self.addAction(refresh_act)

        self.statusBar().showMessage("connecting…")
        # Snapshot the factory-default layout before applying any saved one,
        # so Reset Layout can always get back to it.
        self._default_layout = {
            "jobs_header": self.view.jobs_table.horizontalHeader().saveState(),
            "tasks_header": self.view.tasks_table.horizontalHeader().saveState(),
            "workers_header": self.view.workers_table.horizontalHeader().saveState(),
            "splitter": self.view.split.sizes(),
        }
        self._restore_layout()
        # Compact mode is applied after the layout so it wins over saved state.
        if settings.load().get("monitor_compact"):
            self.compact_act.setChecked(True)

    # Default compact columns: Job Name, Frames, Progress, Status.
    _COMPACT_DEFAULT = [0, 3, 4, 13]

    def _compact_columns(self) -> list[int]:
        """Which job columns compact mode shows — user-chosen, else the default."""
        cols = settings.load().get("monitor_compact_columns")
        cols = [c for c in (cols or []) if 0 <= c < len(JOB_HEADERS)]
        return cols or list(self._COMPACT_DEFAULT)

    def _set_compact(self, on: bool) -> None:
        """Compact job list: only the chosen columns stay visible (Layout ▸
        Compact Columns). Column sizes are kept separately per mode, so resizing
        in compact never disturbs the full layout (and vice versa)."""
        keep = set(self._compact_columns())
        jt = self.view.jobs_table
        h = jt.horizontalHeader()
        s = settings.load()
        jt._fit_restoring = True   # suppress weight re-capture during the swap
        if on:
            # Leaving full mode: remember its column sizes/order.
            s["monitor_jobs_full_header"] = bytes(h.saveState().toHex()).decode()
            compact_state = s.get("monitor_jobs_compact_header")
            if compact_state:
                try:
                    h.restoreState(QByteArray.fromHex(compact_state.encode()))
                except Exception:
                    compact_state = None
            # Enforce visibility regardless of what was restored.
            for c in range(len(JOB_HEADERS)):
                h.setSectionHidden(c, c not in keep)
            self._clear_hidden_sort(h, keep)
        else:
            # Leaving compact mode: remember its column sizes/order.
            s["monitor_jobs_compact_header"] = bytes(h.saveState().toHex()).decode()
            full_state = s.get("monitor_jobs_full_header")
            restored = False
            if full_state:
                try:
                    h.restoreState(QByteArray.fromHex(full_state.encode()))
                    restored = True
                except Exception:
                    pass
            for c in range(len(JOB_HEADERS)):
                h.setSectionHidden(c, False)
            if not restored:
                h.restoreState(self._default_layout["jobs_header"])
        jt._fit_restoring = False
        self._reseed_fit(jt)   # new visible set becomes the fit baseline
        s["monitor_compact"] = on
        settings.save(s)

    def _open_compact_columns(self) -> None:
        """Open (or re-focus) the Compact Columns picker — modeless so the job
        list updates live behind it while columns are toggled."""
        chosen = set(self._compact_columns())
        if self._compact_dialog is None:
            self._compact_dialog = CompactColumnsDialog(
                JOB_HEADERS, chosen, self._toggle_compact_col, self)
        else:
            self._compact_dialog.sync(chosen)
        self._compact_dialog.show()
        self._compact_dialog.raise_()
        self._compact_dialog.activateWindow()

    def _toggle_compact_col(self, col: int, on: bool) -> None:
        """Add/remove a column from the compact selection; persist and, when
        compact mode is live, re-hide columns right away."""
        cols = set(self._compact_columns())
        cols.add(col) if on else cols.discard(col)
        if not cols:  # never leave an empty view — keep Job Name at least
            cols = {0}
        s = settings.load()
        s["monitor_compact_columns"] = sorted(cols)
        settings.save(s)
        if self._compact_dialog is not None:
            self._compact_dialog.sync(cols)  # reflect the fallback if it kicked in
        if self.compact_act.isChecked():
            self._apply_compact_visibility(cols)

    def _apply_compact_visibility(self, keep) -> None:
        """Hide every job column not in ``keep`` (used for live re-apply)."""
        keep = set(keep)
        jt = self.view.jobs_table
        h = jt.horizontalHeader()
        jt._fit_restoring = True
        for c in range(len(JOB_HEADERS)):
            h.setSectionHidden(c, c not in keep)
        self._clear_hidden_sort(h, keep)
        jt._fit_restoring = False
        self._reseed_fit(jt)

    @staticmethod
    def _clear_hidden_sort(h, keep) -> None:
        """Drop the sort indicator if its column was just hidden — otherwise the
        arrow floats over the empty space where the column used to be."""
        if h.sortIndicatorSection() not in set(keep):
            h.setSortIndicator(-1, Qt.AscendingOrder)

    def _toggle_notify(self, on: bool) -> None:
        self.view.notifier.enabled = on
        s = settings.load()
        s["monitor_notify"] = on
        settings.save(s)

    def _toggle_notify_done(self, on: bool) -> None:
        self.view.notify_done = on
        s = settings.load()
        s["monitor_notify_done"] = on
        settings.save(s)

    def _toggle_notify_failed(self, on: bool) -> None:
        self.view.notify_failed = on
        s = settings.load()
        s["monitor_notify_failed"] = on
        settings.save(s)

    def _toggle_super(self, on: bool) -> None:
        self.view.super_mode = on
        for act in (self.edit_workers_act, self.add_pool_act, self.url_act):
            act.setVisible(on)
        self.statusBar().showMessage(
            "Super Muffin Mode ON — worker editing unlocked" if on
            else "Super Muffin Mode off")

    def _edit_workers(self) -> None:
        EditWorkersDialog(self.view.manager_url, self).exec()
        self.view.force_refresh()

    def _add_pool(self) -> None:
        PoolDialog(self.view.manager_url, self).exec()
        self.view.force_refresh()

    def _worker_schedules(self) -> None:
        WorkerSchedulesDialog(self.view.manager_url, self).exec()
        self.view.force_refresh()

    # ------------------------------------------------------- layout memory --
    def _tables(self):
        return (self.view.jobs_table, self.view.tasks_table, self.view.workers_table)

    def _reseed_fit(self, table) -> None:
        """Seed the fit weights from the table's CURRENT widths, then fill the
        window once — immediately if the table is on-screen, otherwise on its
        first real resize (startup). Used after compact-mode changes / Reset
        Layout; afterwards the columns are manual again."""
        h = table.horizontalHeader()
        table._fit_weights = {i: max(1, h.sectionSize(i))
                              for i in range(h.count()) if not h.isSectionHidden(i)}
        if table.isVisible() and table.viewport().width() > 60:
            table.fit_columns()
            table._fit_pending = False
        else:
            table._fit_pending = True   # defer until the first real resize

    def _fit_columns_now(self) -> None:
        """One-shot: distribute the columns to fill the window right now (each
        column still kept at least as wide as its header). Columns stay manual
        afterwards — this is the spreadsheet-style 'fit to window' command."""
        for t in self._tables():
            t.fit_columns()

    def _restore_layout(self) -> None:
        lay = settings.load().get("monitor_layout") or {}
        for t in self._tables():
            t._fit_restoring = True
        try:
            if lay.get("geometry"):
                self.restoreGeometry(QByteArray.fromHex(lay["geometry"].encode()))
            if lay.get("jobs_header"):
                self.view.jobs_table.horizontalHeader().restoreState(
                    QByteArray.fromHex(lay["jobs_header"].encode()))
            if lay.get("tasks_header"):
                self.view.tasks_table.horizontalHeader().restoreState(
                    QByteArray.fromHex(lay["tasks_header"].encode()))
            if lay.get("workers_header"):
                self.view.workers_table.horizontalHeader().restoreState(
                    QByteArray.fromHex(lay["workers_header"].encode()))
            if lay.get("splitter"):
                self.view.split.setSizes([int(x) for x in lay["splitter"]])
        except Exception:
            pass  # a stale layout from an older version should never break startup
        finally:
            for t in self._tables():
                t._fit_restoring = False
        # Default order = newest job on top: never restore a sort on the jobs
        # list (the user can still click a header to sort during the session).
        self.view.jobs_table.horizontalHeader().setSortIndicator(-1, Qt.AscendingOrder)
        # With a saved layout, keep its exact column widths (manual, no auto-fit).
        # Without one, fill the window once on first show. Either way, seed the
        # fit weights so a later Fit-columns keeps the current proportions.
        has_saved = bool(lay)
        for t in self._tables():
            h = t.horizontalHeader()
            t._fit_weights = {i: max(1, h.sectionSize(i))
                              for i in range(h.count()) if not h.isSectionHidden(i)}
            t._fit_pending = not has_saved

    def _save_layout(self) -> None:
        s = settings.load()
        s["monitor_layout"] = {
            "geometry": bytes(self.saveGeometry().toHex()).decode(),
            "jobs_header": bytes(
                self.view.jobs_table.horizontalHeader().saveState().toHex()).decode(),
            "tasks_header": bytes(
                self.view.tasks_table.horizontalHeader().saveState().toHex()).decode(),
            "workers_header": bytes(
                self.view.workers_table.horizontalHeader().saveState().toHex()).decode(),
            "splitter": self.view.split.sizes(),
        }
        settings.save(s)

    def _save_layout_clicked(self) -> None:
        self._save_layout()
        self.statusBar().showMessage("Layout saved — it will be restored next time", 4000)

    def _load_layout_clicked(self) -> None:
        if not settings.load().get("monitor_layout"):
            self.statusBar().showMessage("No saved layout yet — use Save Current Layout first", 4000)
            return
        self._restore_layout()
        self.statusBar().showMessage("Saved layout loaded", 4000)

    def _reset_layout(self) -> None:
        """Reset the view to the factory default. The saved layout is kept —
        Load Saved Layout still works afterwards."""
        d = self._default_layout
        for t in self._tables():
            t._fit_restoring = True
        try:
            self.view.jobs_table.horizontalHeader().restoreState(d["jobs_header"])
            self.view.tasks_table.horizontalHeader().restoreState(d["tasks_header"])
            self.view.workers_table.horizontalHeader().restoreState(d["workers_header"])
            self.view.split.setSizes(d["splitter"])
        finally:
            for t in self._tables():
                t._fit_restoring = False
        for t in self._tables():
            t.horizontalHeader().setSortIndicator(-1, Qt.AscendingOrder)
            self._reseed_fit(t)
        self.statusBar().showMessage("Layout reset to default (saved layout kept)", 4000)

    def _status(self, text: str, color: str) -> None:
        self.statusBar().showMessage(text)
        self.statusBar().setStyleSheet(f"color:{color}")

    def _edit_url(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        url, ok = QInputDialog.getText(self, "Manager URL", "Manager URL:",
                                       text=self.view.manager_url)
        if ok and url.strip():
            self.view.set_manager_url(url.strip())
            s = settings.load()
            s["manager_url"] = url.strip()
            settings.save(s)

    def closeEvent(self, event) -> None:
        # Layout is saved explicitly via Layout ▸ Save Layout.
        self.view.poller.stop()
        self.view.notifier.hide()
        event.accept()


def main() -> None:
    import sys

    from PySide6.QtCore import QLockFile

    from .. import config
    from .style import bring_to_front

    app = QApplication(sys.argv)
    app.setStyleSheet(QSS)
    apply_app_icon(app)

    # One Monitor per machine — focus the existing window instead of opening
    # another copy.
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(config.DATA_DIR / "muffin_monitor_gui.lock"))
    if not lock.tryLock(100):
        bring_to_front("Muffin's Monitor")
        sys.exit(0)

    win = MonitorWindow()
    win._lock = lock  # hold the lock for the app's lifetime
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
