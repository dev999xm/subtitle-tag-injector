from __future__ import annotations

"""Subtitle Tag Injector

A single-file PySide6 application for inserting subtitle tag lines into
subtitle files (.srt, .ass, .ssa, .vtt) using a predictable, gap-based
algorithm.

Core behavior:
- START rules:
  1) If Allow edge insertion is ON, try inserting before subtitle 1 first.
  2) Then search gaps inside the first N subtitle lines.
  3) If nothing fits and Auto reduce duration is ON, reduce duration in 1 second
     steps until Min duration.
  4) If still nothing fits, log a clear red failure line.

- END rules:
  1) Search gaps inside the last N subtitle lines.
  2) If nothing fits and Auto reduce duration is ON, reduce duration in 1 second
     steps until Min duration.
  3) If still nothing fits and Allow edge insertion is ON, append after the last
     subtitle using the original duration.
  4) If still nothing fits, log a clear red failure line.

Dependencies:
    pip install PySide6 pysubs2 charset-normalizer

Run:
    python main.py
"""

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple
import os
import shutil
import sys
import tempfile
import zipfile

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtGui import QAction, QDragEnterEvent, QDropEvent, QFont, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

try:
    import pysubs2
    from pysubs2 import SSAEvent, SSAFile
except Exception:
    pysubs2 = None
    SSAEvent = None
    SSAFile = None

try:
    from charset_normalizer import from_bytes as cn_from_bytes
except Exception:
    cn_from_bytes = None


APP_NAME = "Subtitle Tag Injector v0.0.1"
APP_ORG = "OpenAI"
APP_SUFFIX = "[SubtitleTagInjector]"
DEFAULT_MIN_GAP_MS = 0
SUPPORTED_EXTS = {".srt", ".ass", ".ssa", ".vtt"}
ENCODING_CHOICES = ["Auto", "UTF-8", "WINDOWS-1256", "ISO-8859-1", "CP1252"]


@dataclass
class TagRule:
    enabled: bool
    text: str
    position: str  # start | end | both
    search_range: int
    duration_sec: float
    allow_edge: bool = True
    auto_reduce: bool = True
    min_duration_sec: float = 1.0


@dataclass
class ProcessingOptions:
    output_folder: str
    zip_output: bool
    zip_name: str
    encoding: str
    min_gap_ms: int = DEFAULT_MIN_GAP_MS
    suffix: str = APP_SUFFIX


@dataclass
class InsertionCandidate:
    insert_pos: int
    start_ms: int
    end_ms: int
    reason: str


class FileListWidget(QListWidget):
    filesDropped = Signal(list)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setSelectionMode(QListWidget.ExtendedSelection)
        self.setAlternatingRowColors(True)
        self.setMinimumHeight(180)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        paths: List[str] = []
        for url in event.mimeData().urls():
            if url.isLocalFile():
                paths.append(url.toLocalFile())
        if paths:
            self.filesDropped.emit(paths)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    def add_files(self, paths: Sequence[str]) -> None:
        existing = {self.item(i).text() for i in range(self.count())}
        for path in paths:
            p = os.path.abspath(path)
            if not os.path.isfile(p):
                continue
            if Path(p).suffix.lower() not in SUPPORTED_EXTS:
                continue
            if p in existing:
                continue
            item = QListWidgetItem(p)
            item.setToolTip(p)
            self.addItem(item)
            existing.add(p)

    def file_paths(self) -> List[str]:
        return [self.item(i).text() for i in range(self.count())]


class TagRuleWidget(QFrame):
    removed = Signal(object)

    def __init__(self, index: int, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self.setObjectName("tagRuleWidget")

        self.enabled = QCheckBox("Enabled")
        self.enabled.setChecked(True)

        self.text_edit = QLineEdit()
        self.text_edit.setPlaceholderText("Text to insert")

        self.position_combo = QComboBox()
        self.position_combo.addItems(["start", "end", "both"])

        self.range_spin = QSpinBox()
        self.range_spin.setRange(1, 9999)
        self.range_spin.setValue(20)
        self.range_spin.setToolTip("Search within the first/last N subtitle entries")

        self.duration_spin = QDoubleSpinBox()
        self.duration_spin.setRange(0.10, 3600.0)
        self.duration_spin.setDecimals(2)
        self.duration_spin.setSingleStep(0.50)
        self.duration_spin.setValue(2.00)
        self.duration_spin.setSuffix(" sec")

        self.min_duration_spin = QDoubleSpinBox()
        self.min_duration_spin.setRange(0.10, 3600.0)
        self.min_duration_spin.setDecimals(2)
        self.min_duration_spin.setSingleStep(0.50)
        self.min_duration_spin.setValue(1.00)
        self.min_duration_spin.setSuffix(" sec")

        self.allow_edge_cb = QCheckBox("Allow edge insertion")
        self.allow_edge_cb.setChecked(True)

        self.auto_reduce_cb = QCheckBox("Auto reduce duration")
        self.auto_reduce_cb.setChecked(True)

        self.remove_btn = QPushButton("Remove")
        self.remove_btn.clicked.connect(self._on_remove)

        self.title_label = QLabel(f"Rule {index + 1}")
        self.title_label.setStyleSheet("font-weight: 600;")

        layout = QGridLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(8)

        layout.addWidget(self.title_label, 0, 0)
        layout.addWidget(self.enabled, 0, 1)
        layout.addWidget(self.remove_btn, 0, 3)

        layout.addWidget(QLabel("Text"), 1, 0)
        layout.addWidget(self.text_edit, 1, 1, 1, 3)

        layout.addWidget(QLabel("Range"), 2, 0)
        layout.addWidget(self.range_spin, 2, 1)
        layout.addWidget(QLabel("Position"), 2, 2)
        layout.addWidget(self.position_combo, 2, 3)

        layout.addWidget(QLabel("Duration"), 3, 0)
        layout.addWidget(self.duration_spin, 3, 1)
        layout.addWidget(QLabel("Min duration"), 3, 2)
        layout.addWidget(self.min_duration_spin, 3, 3)

        layout.addWidget(self.allow_edge_cb, 4, 0, 1, 2)
        layout.addWidget(self.auto_reduce_cb, 4, 2, 1, 2)

    def _on_remove(self) -> None:
        self.removed.emit(self)

    def rule(self) -> TagRule:
        return TagRule(
            enabled=self.enabled.isChecked(),
            text=self.text_edit.text().strip(),
            position=self.position_combo.currentText(),
            search_range=int(self.range_spin.value()),
            duration_sec=float(self.duration_spin.value()),
            allow_edge=self.allow_edge_cb.isChecked(),
            auto_reduce=self.auto_reduce_cb.isChecked(),
            min_duration_sec=float(self.min_duration_spin.value()),
        )

    def set_title(self, text: str) -> None:
        self.title_label.setText(text)


class TagRulesPanel(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._rows: List[TagRuleWidget] = []
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)
        self._layout.addStretch(1)

    def add_rule(self, preset: Optional[TagRule] = None) -> TagRuleWidget:
        row = TagRuleWidget(len(self._rows))
        if preset is not None:
            row.enabled.setChecked(preset.enabled)
            row.text_edit.setText(preset.text)
            row.position_combo.setCurrentText(preset.position)
            row.range_spin.setValue(preset.search_range)
            row.duration_spin.setValue(preset.duration_sec)
            row.min_duration_spin.setValue(preset.min_duration_sec)
            row.allow_edge_cb.setChecked(preset.allow_edge)
            row.auto_reduce_cb.setChecked(preset.auto_reduce)
        row.removed.connect(self.remove_rule_widget)
        self._layout.insertWidget(self._layout.count() - 1, row)
        self._rows.append(row)
        self._relabel()
        return row

    def remove_rule_widget(self, widget: TagRuleWidget) -> None:
        if widget in self._rows:
            self._rows.remove(widget)
            widget.setParent(None)
            widget.deleteLater()
            self._relabel()

    def _relabel(self) -> None:
        for i, row in enumerate(self._rows):
            row.set_title(f"Rule {i + 1}")

    def rules(self) -> List[TagRule]:
        return [row.rule() for row in self._rows]


class SubtitleEngine:
    def __init__(self, min_gap_ms: int = DEFAULT_MIN_GAP_MS) -> None:
        self.min_gap_ms = int(min_gap_ms)

    def detect_encoding(self, file_path: str) -> str:
        if cn_from_bytes is None:
            return "utf-8"
        try:
            raw = Path(file_path).read_bytes()
            result = cn_from_bytes(raw[:200000]).best()
            if result and result.encoding:
                return result.encoding
        except Exception:
            pass
        return "utf-8"

    def load_subtitles(self, file_path: str, encoding_mode: str) -> Tuple[SSAFile, str]:
        if pysubs2 is None:
            raise RuntimeError("Missing dependency: pysubs2. Install it with: pip install pysubs2")

        attempts: List[str] = []
        if encoding_mode.lower() == "auto":
            detected = self.detect_encoding(file_path)
            attempts.extend([detected, "utf-8", "windows-1256", "cp1252", "iso-8859-1"])
        else:
            attempts.extend([encoding_mode, "utf-8", "windows-1256", "cp1252", "iso-8859-1"])

        tried = set()
        last_err: Optional[Exception] = None
        for enc in attempts:
            enc_norm = enc.strip() if enc else enc
            if not enc_norm or enc_norm.lower() in tried:
                continue
            tried.add(enc_norm.lower())
            try:
                subs = pysubs2.load(file_path, encoding=enc_norm)
                return subs, enc_norm
            except Exception as exc:
                last_err = exc

        raise RuntimeError(
            f"Unable to load subtitle file '{file_path}' with available encodings. Last error: {last_err}"
        )

    def normalize_text(self, text: str) -> str:
        return text.strip()

    def build_output_path(self, file_path: str, suffix: str, output_dir: str) -> str:
        src = Path(file_path)
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        safe_suffix = suffix.strip() or APP_SUFFIX
        return str(out_dir / f"{src.stem} {safe_suffix}{src.suffix}")

    def process_file(
        self,
        file_path: str,
        rules: Sequence[TagRule],
        options: ProcessingOptions,
        output_dir: str,
    ) -> Tuple[str, List[str]]:
        subs, used_encoding = self.load_subtitles(file_path, options.encoding)
        log_lines: List[str] = [
            f"Loaded '{Path(file_path).name}' using encoding: {used_encoding}",
        ]

        events = list(subs.events)
        events.sort(key=lambda e: (int(e.start), int(e.end)))

        for rule_index, rule in enumerate(rules, start=1):
            if not rule.enabled or not rule.text.strip():
                continue

            if rule.position == "start":
                events, msg, ok = self.apply_start_rule(events, rule)
                if ok:
                    log_lines.append(f"Rule {rule_index} (start): {msg}")
                else:
                    log_lines.append(f"Rule {rule_index} (start): {msg}")

            elif rule.position == "end":
                events, msg, ok = self.apply_end_rule(events, rule)
                if ok:
                    log_lines.append(f"Rule {rule_index} (end): {msg}")
                else:
                    log_lines.append(f"Rule {rule_index} (end): {msg}")

            elif rule.position == "both":
                events, msg, ok = self.apply_start_rule(events, rule)
                log_lines.append(f"Rule {rule_index} (both/start): {msg}")
                events, msg, ok = self.apply_end_rule(events, rule)
                log_lines.append(f"Rule {rule_index} (both/end): {msg}")

        subs.events = events
        outfile = self.build_output_path(file_path, options.suffix, output_dir)
        subs.save(outfile)
        log_lines.append(f"Saved: {outfile}")
        return outfile, log_lines

    def apply_start_rule(self, events: Sequence[SSAEvent], rule: TagRule) -> Tuple[List[SSAEvent], str, bool]:
        """START algorithm:
        1) If allow_edge is enabled, try before subtitle 1.
        2) Search the first N subtitle entries.
        3) If no fit, reduce duration by 1 second until min duration.
        4) If still no fit, fail.
        """

        current_duration = float(rule.duration_sec)
        min_duration = float(rule.min_duration_sec)
        text = self.normalize_text(rule.text)

        while current_duration >= min_duration:
            if rule.allow_edge:
                candidate = self.try_before_first_edge(events, current_duration)
                if candidate is not None:
                    return (
                        self._insert_event(events, candidate, text),
                        self._success_msg(candidate, "start edge"),
                        True,
                    )

            candidate = self.find_gap_in_first_n(events, rule.search_range, current_duration)
            if candidate is not None:
                return (
                    self._insert_event(events, candidate, text),
                    self._success_msg(candidate, "start"),
                    True,
                )

            if not rule.auto_reduce:
                break
            current_duration -= 1.0

        return (
            list(events),
            f"{self._failure_prefix(rule.text, 'START')} no valid space found in first {rule.search_range} line(s)",
            False,
        )

    def apply_end_rule(self, events: Sequence[SSAEvent], rule: TagRule) -> Tuple[List[SSAEvent], str, bool]:
        """END algorithm:
        1) Search the last N subtitle entries.
        2) If no fit, reduce duration by 1 second until min duration.
        3) If still no fit and allow_edge is enabled, append after the last subtitle
           using the original duration.
        4) If still no fit, fail.
        """

        original_duration = float(rule.duration_sec)
        current_duration = original_duration
        min_duration = float(rule.min_duration_sec)
        text = self.normalize_text(rule.text)

        while current_duration >= min_duration:
            candidate = self.find_gap_in_last_n(events, rule.search_range, current_duration)
            if candidate is not None:
                return (
                    self._insert_event(events, candidate, text),
                    self._success_msg(candidate, "end"),
                    True,
                )

            if not rule.auto_reduce:
                break
            current_duration -= 1.0

        if rule.allow_edge:
            candidate = self.try_after_last_edge(events, original_duration)
            if candidate is not None:
                return (
                    self._insert_event(events, candidate, text),
                    self._success_msg(candidate, "end edge"),
                    True,
                )

        return (
            list(events),
            f"{self._failure_prefix(rule.text, 'END')} no valid space found in last {rule.search_range} line(s)",
            False,
        )

    def _success_msg(self, candidate: InsertionCandidate, kind: str) -> str:
        return f"inserted at {candidate.start_ms}..{candidate.end_ms} ms ({kind}: {candidate.reason})"

    def _failure_prefix(self, tag_text: str, position: str) -> str:
        safe_text = tag_text.strip() or "<empty tag>"
        return f"[{position}] tag='{safe_text}'"

    def _insert_event(self, events: Sequence[SSAEvent], candidate: InsertionCandidate, text: str) -> List[SSAEvent]:
        if SSAEvent is None:
            raise RuntimeError("Missing dependency: pysubs2")
        new_events = list(events)
        new_events.insert(
            candidate.insert_pos,
            SSAEvent(start=int(candidate.start_ms), end=int(candidate.end_ms), text=text),
        )
        new_events.sort(key=lambda e: (int(e.start), int(e.end)))
        return new_events

    def try_before_first_edge(self, events: Sequence[SSAEvent], duration_sec: float) -> Optional[InsertionCandidate]:
        if not events:
            duration_ms = max(100, int(round(duration_sec * 1000.0)))
            return InsertionCandidate(0, 0, duration_ms, "empty file")

        duration_ms = max(100, int(round(duration_sec * 1000.0)))
        first_start = int(events[0].start)
        gap_start = self.min_gap_ms
        gap_end = first_start - self.min_gap_ms
        if gap_start + duration_ms <= gap_end:
            return InsertionCandidate(0, gap_start, gap_start + duration_ms, "before first subtitle")
        return None

    def try_after_last_edge(self, events: Sequence[SSAEvent], duration_sec: float) -> Optional[InsertionCandidate]:
        duration_ms = max(100, int(round(duration_sec * 1000.0)))
        if not events:
            return InsertionCandidate(0, 0, duration_ms, "empty file")

        last_end = int(events[-1].end)
        gap_start = last_end + self.min_gap_ms
        return InsertionCandidate(len(events), gap_start, gap_start + duration_ms, "after last subtitle")

    def find_gap_in_first_n(self, events: Sequence[SSAEvent], search_range: int, duration_sec: float) -> Optional[InsertionCandidate]:
        """Search internal gaps among the first N subtitle entries.

        Start algorithm uses this after edge insertion attempt. We intentionally
        do not include the space before subtitle 1 here because that is the edge
        case.
        """

        count = len(events)
        if count < 2:
            return None

        n = max(1, min(int(search_range), count))
        duration_ms = max(100, int(round(duration_sec * 1000.0)))

        # Internal positions in the first N entries: before event 2 up to before event N.
        # That means insert positions 1 .. n-1.
        for insert_pos in range(1, n):
            candidate = self.compute_gap(events, insert_pos, duration_ms)
            if candidate is not None:
                return candidate
        return None

    def find_gap_in_last_n(self, events: Sequence[SSAEvent], search_range: int, duration_sec: float) -> Optional[InsertionCandidate]:
        """Search internal gaps among the last N subtitle entries.

        We scan from the end backwards so the latest valid gap is preferred.
        """

        count = len(events)
        if count < 2:
            return None

        n = max(1, min(int(search_range), count))
        duration_ms = max(100, int(round(duration_sec * 1000.0)))

        start_pos = max(1, count - n + 1)
        end_pos = count - 1

        for insert_pos in range(end_pos, start_pos - 1, -1):
            candidate = self.compute_gap(events, insert_pos, duration_ms)
            if candidate is not None:
                return candidate
        return None

    def compute_gap(self, events: Sequence[SSAEvent], insert_pos: int, duration_ms: int) -> Optional[InsertionCandidate]:
        count = len(events)
        min_gap = self.min_gap_ms

        if count == 0:
            return InsertionCandidate(0, 0, duration_ms, "empty file")

        if insert_pos <= 0:
            # Before first subtitle.
            next_start = int(events[0].start)
            gap_start = min_gap
            gap_end = next_start - min_gap
            if gap_start + duration_ms <= gap_end:
                return InsertionCandidate(0, gap_start, gap_start + duration_ms, "before first subtitle")
            return None

        if insert_pos >= count:
            # After last subtitle.
            prev_end = int(events[-1].end)
            gap_start = prev_end + min_gap
            return InsertionCandidate(count, gap_start, gap_start + duration_ms, "after last subtitle")

        prev_end = int(events[insert_pos - 1].end)
        next_start = int(events[insert_pos].start)
        gap_start = prev_end + min_gap
        gap_end = next_start - min_gap

        if gap_start + duration_ms <= gap_end:
            return InsertionCandidate(insert_pos, gap_start, gap_start + duration_ms, f"gap between {insert_pos - 1} and {insert_pos}")
        return None


class ProcessingWorker(QThread):
    log = Signal(str)
    progress = Signal(int, int)
    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, files: Sequence[str], rules: Sequence[TagRule], options: ProcessingOptions, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.files = list(files)
        self.rules = list(rules)
        self.options = options
        self.engine = SubtitleEngine(min_gap_ms=options.min_gap_ms)

    def run(self) -> None:
        staging_dir = tempfile.mkdtemp(prefix="subtitle_inject_")
        output_dir = Path(self.options.output_folder).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            if not self.files:
                raise RuntimeError("No subtitle files selected.")

            total = len(self.files)
            processed_paths: List[str] = []
            failures: List[str] = []

            self.log.emit(f"Staging directory: {staging_dir}")
            self.log.emit(f"Output folder: {output_dir}")
            self.log.emit(f"Files queued: {total}")
            self.log.emit(f"Rules queued: {len(self.rules)}")

            for index, file_path in enumerate(self.files, start=1):
                self.progress.emit(index - 1, total)
                self.log.emit(f"Processing {index}/{total}: {Path(file_path).name}")
                try:
                    out_file, messages = self.engine.process_file(file_path, self.rules, self.options, staging_dir)
                    processed_paths.append(out_file)
                    for line in messages:
                        self.log.emit(line)
                    self.log.emit(f"Processed {index}/{total}: {Path(file_path).name}")
                except Exception as exc:
                    msg = f"Failed on file '{file_path}': {exc}"
                    failures.append(msg)
                    self.log.emit(msg)

            self.progress.emit(total, total)

            if not processed_paths:
                raise RuntimeError("No files were processed successfully.")

            if self.options.zip_output:
                zip_name = self.options.zip_name.strip() or "processed_subtitles.zip"
                if not zip_name.lower().endswith(".zip"):
                    zip_name += ".zip"
                zip_path = output_dir / zip_name
                self._make_zip(processed_paths, str(zip_path))
                self.log.emit(f"ZIP created: {zip_path}")
                self.finished_ok.emit(str(zip_path))
            else:
                for staged in processed_paths:
                    shutil.copy2(staged, output_dir / Path(staged).name)
                self.log.emit("Individual processed files copied to output folder.")
                self.finished_ok.emit(str(output_dir))

            if failures:
                self.log.emit(f"Completed with {len(failures)} failure(s).")

        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)

    def _make_zip(self, file_paths: Sequence[str], zip_path: str) -> None:
        if os.path.exists(zip_path):
            os.remove(zip_path)
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in file_paths:
                zf.write(path, arcname=Path(path).name)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.worker: Optional[ProcessingWorker] = None

        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(1200, 780)

        root = QWidget(self)
        self.setCentralWidget(root)
        main_layout = QHBoxLayout(root)
        main_layout.setSpacing(12)

        left = QVBoxLayout()
        right = QVBoxLayout()
        main_layout.addLayout(left, 1)
        main_layout.addLayout(right, 1)

        # File box
        file_box = QGroupBox("Subtitle Files")
        file_layout = QVBoxLayout(file_box)
        self.file_list = FileListWidget()
        self.file_list.filesDropped.connect(self.add_files)
        file_layout.addWidget(self.file_list)

        file_buttons = QHBoxLayout()
        self.add_file_btn = QPushButton("Add Files")
        self.remove_file_btn = QPushButton("Remove Selected")
        self.clear_file_btn = QPushButton("Clear All")
        file_buttons.addWidget(self.add_file_btn)
        file_buttons.addWidget(self.remove_file_btn)
        file_buttons.addWidget(self.clear_file_btn)
        file_layout.addLayout(file_buttons)

        self.add_file_btn.clicked.connect(self.pick_files)
        self.remove_file_btn.clicked.connect(self.remove_selected_files)
        self.clear_file_btn.clicked.connect(self.file_list.clear)

        left.addWidget(file_box, 2)

        # Log box
        log_box = QGroupBox("Log")
        log_layout = QVBoxLayout(log_box)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Consolas", 10))
        log_layout.addWidget(self.log_view)
        left.addWidget(log_box, 2)

        # Rules box
        tag_box = QGroupBox("Tag Rules")
        tag_layout = QVBoxLayout(tag_box)

        top_tag_buttons = QHBoxLayout()
        self.add_rule_btn = QPushButton("Add Rule")
        self.add_rule_btn.clicked.connect(self.add_rule)
        self.add_start_rule_btn = QPushButton("Quick Start Rule")
        self.add_end_rule_btn = QPushButton("Quick End Rule")
        self.add_start_rule_btn.clicked.connect(lambda: self.add_rule(TagRule(True, "", "start", 20, 2.0, True, True, 1.0)))
        self.add_end_rule_btn.clicked.connect(lambda: self.add_rule(TagRule(True, "", "end", 20, 2.0, True, True, 1.0)))
        top_tag_buttons.addWidget(self.add_rule_btn)
        top_tag_buttons.addWidget(self.add_start_rule_btn)
        top_tag_buttons.addWidget(self.add_end_rule_btn)
        top_tag_buttons.addStretch(1)
        tag_layout.addLayout(top_tag_buttons)

        self.tag_panel = TagRulesPanel()
        self.tag_scroll = QScrollArea()
        self.tag_scroll.setWidgetResizable(True)
        self.tag_scroll.setWidget(self.tag_panel)
        tag_layout.addWidget(self.tag_scroll)

        right.addWidget(tag_box, 3)

        # Output box
        output_box = QGroupBox("Output")
        output_form = QFormLayout(output_box)

        self.output_folder_edit = QLineEdit(str(Path.cwd() / "output"))
        self.output_folder_btn = QPushButton("Browse")
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.output_folder_edit, 1)
        folder_row.addWidget(self.output_folder_btn)
        folder_wrap = QWidget()
        folder_wrap.setLayout(folder_row)
        output_form.addRow("Output folder", folder_wrap)

        self.zip_check = QCheckBox("Create ZIP")
        self.zip_check.setChecked(False)
        output_form.addRow("Mode", self.zip_check)

        self.zip_name_edit = QLineEdit("processed_subtitles.zip")
        output_form.addRow("ZIP filename", self.zip_name_edit)

        self.encoding_combo = QComboBox()
        self.encoding_combo.addItems(ENCODING_CHOICES)
        output_form.addRow("Encoding", self.encoding_combo)

        self.min_gap_spin = QSpinBox()
        self.min_gap_spin.setRange(0, 9999)
        self.min_gap_spin.setValue(DEFAULT_MIN_GAP_MS)
        self.min_gap_spin.setSuffix(" ms")
        output_form.addRow("Minimum gap", self.min_gap_spin)

        self.suffix_edit = QLineEdit(APP_SUFFIX)
        output_form.addRow("Filename suffix", self.suffix_edit)

        right.addWidget(output_box)

        # Run box
        process_box = QGroupBox("Run")
        process_layout = QVBoxLayout(process_box)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        process_layout.addWidget(self.progress)

        self.run_btn = QPushButton("Process Subtitle Files")
        self.run_btn.clicked.connect(self.start_processing)
        process_layout.addWidget(self.run_btn)

        right.addWidget(process_box)

        self.statusBar().showMessage("Ready")

        # Example starter rules
        self.tag_panel.add_rule(TagRule(True, "Subtitle Tag Injector - Sample Tag #1", "start", 100, 5.0, min_duration_sec=2.0))
        self.tag_panel.add_rule(TagRule(True, "Subtitle Tag Injector - Sample Tag #2", "start", 100, 5.0, min_duration_sec=2.0))
        self.tag_panel.add_rule(TagRule(True, "Subtitle Tag Injector - Sample Tag #3", "end", 75, 5.0,  min_duration_sec=2.0))
        self.tag_panel.add_rule(TagRule(True, "Subtitle Tag Injector - Sample Tag #4", "end", 75, 5.0,  min_duration_sec=2.0))

        self.output_folder_btn.clicked.connect(self.choose_output_folder)
        self._build_menu()

    def _build_menu(self) -> None:
        menu = self.menuBar().addMenu("File")
        open_action = QAction("Add Files...", self)
        open_action.triggered.connect(self.pick_files)
        menu.addAction(open_action)

        clear_log_action = QAction("Clear Log", self)
        clear_log_action.triggered.connect(self.log_view.clear)
        menu.addAction(clear_log_action)

        menu.addSeparator()
        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        menu.addAction(exit_action)

    def add_rule(self, preset: Optional[TagRule] = None) -> None:
        self.tag_panel.add_rule(preset or TagRule(True, "", "start", 20, 2.0, True, True, 1.0))

    def add_files(self, paths: Sequence[str]) -> None:
        self.file_list.add_files(paths)
        self.statusBar().showMessage(f"{self.file_list.count()} file(s) loaded")

    def pick_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select subtitle files",
            str(Path.cwd()),
            "Subtitle files (*.srt *.ass *.ssa *.vtt);;All files (*.*)",
        )
        if paths:
            self.add_files(paths)

    def choose_output_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "Choose output folder",
            self.output_folder_edit.text().strip() or str(Path.cwd()),
        )
        if folder:
            self.output_folder_edit.setText(folder)

    def remove_selected_files(self) -> None:
        for item in self.file_list.selectedItems():
            row = self.file_list.row(item)
            self.file_list.takeItem(row)
        self.statusBar().showMessage(f"{self.file_list.count()} file(s) loaded")

    def append_log(self, text: str, color: str = "black") -> None:
        self.log_view.append(f'<span style="color:{color}">{text}</span>')
        cursor = self.log_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.log_view.setTextCursor(cursor)

    def collect_rules(self) -> List[TagRule]:
        return self.tag_panel.rules()

    def start_processing(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self, "Busy", "Processing is already running.")
            return

        files = self.file_list.file_paths()
        if not files:
            QMessageBox.warning(self, "No files", "Please add one or more subtitle files.")
            return

        rules = [r for r in self.collect_rules() if r.enabled and r.text.strip()]
        if not rules:
            QMessageBox.warning(self, "No rules", "Please add at least one enabled tag rule with text.")
            return

        output_folder = self.output_folder_edit.text().strip()
        if not output_folder:
            QMessageBox.warning(self, "Output folder", "Please choose an output folder.")
            return

        options = ProcessingOptions(
            output_folder=output_folder,
            zip_output=self.zip_check.isChecked(),
            zip_name=self.zip_name_edit.text().strip(),
            encoding=self.encoding_combo.currentText().strip(),
            min_gap_ms=int(self.min_gap_spin.value()),
            suffix=self.suffix_edit.text().strip() or APP_SUFFIX,
        )

        self.log_view.clear()
        self.append_log("Starting processing...", "#444")
        self.append_log(f"Files: {len(files)}", "#444")
        self.append_log(f"Rules: {len(rules)}", "#444")
        self.append_log(f"Minimum gap: {options.min_gap_ms} ms", "#444")

        self.progress.setRange(0, len(files))
        self.progress.setValue(0)
        self.run_btn.setEnabled(False)

        self.worker = ProcessingWorker(files, rules, options, self)
        self.worker.log.connect(self._on_worker_log)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished_ok.connect(self.on_finished)
        self.worker.failed.connect(self.on_failed)
        self.worker.start()

    def _on_worker_log(self, text: str) -> None:
        # Color failures and explicit no-space messages in red.
        lower = text.lower()
        if "failed on file" in lower or "no valid space found" in lower or lower.startswith("error:"):
            self.append_log(text, "#b00020")
        elif "inserted at" in lower or "saved:" in lower or "processed" in lower:
            self.append_log(text, "#0b6e4f")
        else:
            self.append_log(text, "#222")

    def on_progress(self, current: int, total: int) -> None:
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(current)
        self.statusBar().showMessage(f"Processing {current}/{total}")

    def on_finished(self, output_path: str) -> None:
        self.run_btn.setEnabled(True)
        self.progress.setValue(self.progress.maximum())
        self.statusBar().showMessage("Done")
        QMessageBox.information(self, "Finished", f"Processing completed successfully. Output: {output_path}")
        self.append_log(f"Done: {output_path}", "#0b6e4f")

    def on_failed(self, message: str) -> None:
        self.run_btn.setEnabled(True)
        self.progress.setValue(0)
        self.statusBar().showMessage("Failed")
        self.append_log("ERROR: " + message, "#b00020")
        QMessageBox.critical(self, "Error", message)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_ORG)

    if pysubs2 is None:
        QMessageBox.critical(
            None,
            "Missing dependency",
            "This app requires 'pysubs2'. Install it with: pip install pysubs2",
        )
        return 1

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
