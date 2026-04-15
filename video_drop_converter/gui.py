from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QTime, Qt, Signal
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QProgressBar,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .core import (
    DEFAULT_CODEC,
    DEFAULT_CQ,
    DEFAULT_PRESET,
    QueueEntry,
    build_output_path,
    discover_video_files,
    ensure_ffmpeg_tools,
    format_duration,
    format_file_size,
    get_runtime_output_directory,
    make_suffix,
    make_unique_output_path,
    probe_video,
)
from .worker import ConversionWorker


INPUT_COLUMN = 0
DURATION_COLUMN = 1
CODEC_COLUMN = 2
RESOLUTION_COLUMN = 3
SIZE_COLUMN = 4
OUTPUT_COLUMN = 5
STATUS_COLUMN = 6
PROGRESS_COLUMN = 7
SPEED_COLUMN = 8
DETAIL_COLUMN = 9


@dataclass(slots=True)
class JobRow:
    row_index: int
    queue_entry: QueueEntry
    status: str = "대기"
    progress: float = 0.0
    speed: str = "-"
    detail: str = ""


class DropArea(QFrame):
    paths_dropped = Signal(list)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.StyledPanel)
        self.setObjectName("dropArea")

        label = QLabel("여기로 영상 파일이나 폴더를 드래그해서 놓으면 바로 큐에 추가합니다.")
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignCenter)

        secondary = QLabel("기본 설정: hevc_nvenc / p1 / 오디오 copy")
        secondary.setAlignment(Qt.AlignCenter)
        secondary.setObjectName("secondaryText")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.addWidget(label)
        layout.addWidget(secondary)

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event) -> None:  # type: ignore[override]
        paths = []
        for url in event.mimeData().urls():
            if url.isLocalFile():
                paths.append(url.toLocalFile())
        if paths:
            self.paths_dropped.emit(paths)
            event.acceptProposedAction()
            return
        event.ignore()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("영상 드래그앤드롭 변환기")
        self.resize(1320, 820)

        self._jobs: list[JobRow] = []
        self._source_keys: set[str] = set()
        self._output_directory = get_runtime_output_directory()
        self._worker: ConversionWorker | None = None

        self._build_ui()
        self._check_tools()
        self._append_log("프로그램을 시작했습니다.")
        self._append_log(f"출력 폴더: {self._output_directory}")

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("rootPanel")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(16, 16, 16, 16)
        root_layout.setSpacing(12)

        toolbar = self._build_toolbar()
        drop_area = DropArea()
        drop_area.paths_dropped.connect(self._handle_paths_dropped)
        self._drop_area = drop_area

        settings_group = self._build_settings_panel()

        self._summary_label = QLabel("대기 중인 작업이 없습니다.")
        self._summary_label.setObjectName("summaryLabel")
        self._overall_progress = QProgressBar()
        self._overall_progress.setRange(0, 100)
        self._overall_progress.setValue(0)
        self._overall_progress.setObjectName("overallProgress")

        self._table = QTableWidget(0, 10)
        self._table.setObjectName("jobTable")
        self._table.setHorizontalHeaderLabels(
            [
                "입력 파일",
                "길이",
                "비디오",
                "해상도",
                "원본 크기",
                "출력 파일",
                "상태",
                "진행률",
                "속도",
                "메모",
            ]
        )
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(INPUT_COLUMN, QHeaderView.Stretch)
        header.setSectionResizeMode(OUTPUT_COLUMN, QHeaderView.Stretch)
        header.setSectionResizeMode(DETAIL_COLUMN, QHeaderView.Stretch)
        for column in (DURATION_COLUMN, CODEC_COLUMN, RESOLUTION_COLUMN, SIZE_COLUMN, STATUS_COLUMN, PROGRESS_COLUMN, SPEED_COLUMN):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setAlternatingRowColors(True)

        self._log_output = QPlainTextEdit()
        self._log_output.setObjectName("logPanel")
        self._log_output.setReadOnly(True)
        self._log_output.setMaximumBlockCount(1000)
        self._log_output.setPlaceholderText("로그가 여기 표시됩니다.")

        log_label = QLabel("로그")
        log_label.setObjectName("sectionLabel")

        root_layout.addWidget(toolbar)
        root_layout.addWidget(settings_group)
        root_layout.addWidget(drop_area)
        root_layout.addWidget(self._summary_label)
        root_layout.addWidget(self._overall_progress)
        root_layout.addWidget(self._table, stretch=1)
        root_layout.addWidget(log_label)
        root_layout.addWidget(self._log_output, stretch=1)

        self.setCentralWidget(root)
        self.setStyleSheet(
            """
            QWidget {
                font-size: 13px;
                color: #17212f;
            }
            #rootPanel {
                background: #eff3f7;
            }
            #toolbarPanel, #settingsPanel {
                background: #f8fbfd;
                border: 1px solid #d9e3ea;
                border-radius: 12px;
            }
            QLabel {
                color: #324152;
            }
            #summaryLabel {
                color: #304255;
                font-size: 14px;
                font-weight: 600;
                padding: 4px 2px;
            }
            #sectionLabel {
                color: #3f5368;
                font-size: 12px;
                font-weight: 700;
                letter-spacing: 0.08em;
                text-transform: uppercase;
            }
            QPushButton {
                background: #2f455c;
                color: #f8fbff;
                border: 1px solid #25384c;
                border-radius: 8px;
                padding: 9px 14px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #38536e;
            }
            QPushButton:pressed {
                background: #24384b;
            }
            QPushButton:disabled {
                background: #a7b2bd;
                color: #eef2f5;
                border-color: #a7b2bd;
            }
            QPushButton[kind="primary"] {
                background: #0e7490;
                border-color: #0b5b71;
            }
            QPushButton[kind="primary"]:hover {
                background: #1188aa;
            }
            QPushButton[kind="stop"] {
                background: #9a3412;
                border-color: #7c2d12;
            }
            QPushButton[kind="stop"]:hover {
                background: #b45309;
            }
            QPushButton[kind="clear"] {
                background: #5b6570;
                border-color: #49535d;
            }
            QPushButton[kind="clear"]:hover {
                background: #6b7682;
            }
            QCheckBox {
                color: #425466;
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border-radius: 4px;
                border: 1px solid #8ea1b2;
                background: #ffffff;
            }
            QCheckBox::indicator:checked {
                background: #0e7490;
                border-color: #0e7490;
            }
            QSpinBox {
                min-width: 72px;
                padding: 6px 8px;
                border-radius: 8px;
                border: 1px solid #b8c4cf;
                background: #ffffff;
                color: #17212f;
                selection-background-color: #0e7490;
            }
            QSpinBox:focus {
                border: 1px solid #0e7490;
            }
            QHeaderView::section {
                background: #243447;
                color: #f3f7fb;
                border: none;
                padding: 8px 10px;
                font-weight: 700;
            }
            #jobTable {
                background: #ffffff;
                alternate-background-color: #f4f8fb;
                gridline-color: #d7e0e8;
                border: 1px solid #cfd8df;
                border-radius: 10px;
                color: #1c2734;
                selection-background-color: #d4ebf2;
                selection-color: #10212f;
            }
            #jobTable::item {
                padding: 6px;
            }
            #jobTable::item:selected {
                background: #d4ebf2;
                color: #10212f;
            }
            #logPanel {
                background: #15202b;
                color: #dce8f2;
                border: 1px solid #243447;
                border-radius: 10px;
                padding: 10px;
                selection-background-color: #0e7490;
            }
            #overallProgress {
                min-height: 16px;
                border-radius: 8px;
                border: 1px solid #c8d3db;
                background: #dfe7ed;
                text-align: center;
                color: #213547;
                font-weight: 600;
            }
            #overallProgress::chunk {
                border-radius: 7px;
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 0,
                    stop: 0 #0e7490,
                    stop: 1 #14b8a6
                );
            }
            #dropArea {
                border: 2px dashed #0e7490;
                border-radius: 16px;
                background: #f3fbfc;
                color: #123044;
            }
            #dropArea QLabel {
                color: #274257;
                font-size: 14px;
            }
            #secondaryText {
                color: #53718c;
                font-weight: 600;
            }
            """
        )

    def _build_toolbar(self) -> QWidget:
        container = QWidget()
        container.setObjectName("toolbarPanel")
        layout = QHBoxLayout(container)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        add_files_button = QPushButton("파일 추가")
        add_files_button.clicked.connect(self._pick_files)
        add_folder_button = QPushButton("폴더 추가")
        add_folder_button.clicked.connect(self._pick_folder)
        self._start_button = QPushButton("대기열 시작")
        self._start_button.setProperty("kind", "primary")
        self._start_button.clicked.connect(self._start_processing)
        self._stop_button = QPushButton("중지")
        self._stop_button.setProperty("kind", "stop")
        self._stop_button.clicked.connect(self._stop_processing)
        self._stop_button.setEnabled(False)
        clear_button = QPushButton("목록 비우기")
        clear_button.setProperty("kind", "clear")
        clear_button.clicked.connect(self._clear_jobs)

        layout.addWidget(add_files_button)
        layout.addWidget(add_folder_button)
        layout.addSpacing(8)
        layout.addWidget(self._start_button)
        layout.addWidget(self._stop_button)
        layout.addStretch(1)
        layout.addWidget(clear_button)

        return container

    def _build_settings_panel(self) -> QWidget:
        container = QWidget()
        container.setObjectName("settingsPanel")
        layout = QGridLayout(container)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(8)

        codec_value = QLabel(DEFAULT_CODEC)
        preset_value = QLabel(DEFAULT_PRESET)

        self._cq_spin = QSpinBox()
        self._cq_spin.setRange(18, 35)
        self._cq_spin.setValue(DEFAULT_CQ)
        self._cq_spin.valueChanged.connect(self._update_suffix_label)

        self._suffix_label = QLabel()
        self._auto_start_checkbox = QCheckBox("드롭 시 자동 시작")
        self._auto_start_checkbox.setChecked(True)
        self._skip_hevc_checkbox = QCheckBox("이미 HEVC면 건너뛰기")
        self._skip_hevc_checkbox.setChecked(True)
        self._skip_existing_checkbox = QCheckBox("출력 파일이 있으면 건너뛰기")
        self._skip_existing_checkbox.setChecked(True)

        layout.addWidget(QLabel("코덱"), 0, 0)
        layout.addWidget(codec_value, 0, 1)
        layout.addWidget(QLabel("프리셋"), 0, 2)
        layout.addWidget(preset_value, 0, 3)
        layout.addWidget(QLabel("CQ"), 0, 4)
        layout.addWidget(self._cq_spin, 0, 5)
        layout.addWidget(QLabel("출력 접미사"), 0, 6)
        layout.addWidget(self._suffix_label, 0, 7)
        layout.addWidget(self._auto_start_checkbox, 1, 0, 1, 2)
        layout.addWidget(self._skip_hevc_checkbox, 1, 2, 1, 2)
        layout.addWidget(self._skip_existing_checkbox, 1, 4, 1, 3)
        layout.setColumnStretch(7, 1)

        self._update_suffix_label()
        return container

    def _check_tools(self) -> None:
        missing = ensure_ffmpeg_tools()
        if not missing:
            return
        message = "다음 도구를 PATH에서 찾지 못했습니다: " + ", ".join(missing)
        QMessageBox.critical(self, "도구 없음", message)
        self._append_log(message)
        self._set_controls_enabled(False)

    def _pick_files(self) -> None:
        file_names, _ = QFileDialog.getOpenFileNames(
            self,
            "영상 파일 선택",
            "",
            "Video Files (*.mp4 *.m4v *.mov *.mkv *.avi *.wmv *.webm *.ts *.m2ts)",
        )
        if file_names:
            self._handle_paths_dropped(file_names)

    def _pick_folder(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "폴더 선택", "")
        if directory:
            self._handle_paths_dropped([directory])

    def _handle_paths_dropped(self, raw_paths: list[str]) -> None:
        suffix = make_suffix(cq=self._cq_spin.value())
        discovered = discover_video_files((Path(path) for path in raw_paths), suffix=suffix)
        if not discovered:
            self._append_log("추가할 영상 파일을 찾지 못했습니다.")
            return

        reserved_output_paths = {job.queue_entry.output_path.resolve() for job in self._jobs}
        added_count = 0
        for source_path in discovered:
            source_key = str(source_path).lower()
            if source_key in self._source_keys:
                self._append_log(f"중복 입력이라 건너뜀: {source_path.name}")
                continue

            try:
                info = probe_video(source_path)
            except (OSError, subprocess.CalledProcessError, ValueError) as exc:
                self._append_log(f"분석 실패: {source_path.name} ({exc})")
                continue

            output_path = build_output_path(
                source_path,
                suffix=suffix,
                output_dir=self._output_directory,
            )
            if self._skip_hevc_checkbox.isChecked() and info.codec_name.lower() == "hevc":
                self._append_log(f"이미 HEVC라 건너뜀: {source_path.name}")
                continue
            if self._skip_existing_checkbox.isChecked() and output_path.exists():
                self._append_log(f"출력 파일이 이미 있어 건너뜀: {output_path.name}")
                continue
            if output_path.resolve() in reserved_output_paths or output_path.exists():
                output_path = make_unique_output_path(output_path, reserved_output_paths)

            queue_entry = QueueEntry(source_path=source_path, output_path=output_path, info=info)
            row_index = self._table.rowCount()
            self._table.insertRow(row_index)
            self._populate_row(row_index, queue_entry)
            self._jobs.append(JobRow(row_index=row_index, queue_entry=queue_entry))
            self._source_keys.add(source_key)
            reserved_output_paths.add(output_path.resolve())
            added_count += 1

        if added_count == 0:
            self._append_log("새로 큐에 추가된 파일이 없습니다.")
            return

        self._append_log(f"{added_count}개 파일을 큐에 추가했습니다.")
        self._update_summary()
        if self._auto_start_checkbox.isChecked():
            self._start_processing()

    def _populate_row(self, row_index: int, queue_entry: QueueEntry) -> None:
        info = queue_entry.info
        values = [
            queue_entry.source_path.name,
            format_duration(info.duration_seconds),
            info.codec_name,
            f"{info.width}x{info.height}",
            format_file_size(info.file_size_bytes),
            str(queue_entry.output_path),
            "대기",
            "0%",
            "-",
            "",
        ]
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            if column in {DURATION_COLUMN, CODEC_COLUMN, RESOLUTION_COLUMN, SIZE_COLUMN, STATUS_COLUMN, PROGRESS_COLUMN, SPEED_COLUMN}:
                item.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(row_index, column, item)

    def _start_processing(self) -> None:
        if self._worker is not None:
            return

        pending_rows = [
            (job.row_index, job.queue_entry)
            for job in self._jobs
            if job.status == "대기"
        ]
        if not pending_rows:
            self._append_log("대기 중인 작업이 없습니다.")
            return

        self._worker = ConversionWorker(
            queued_rows=pending_rows,
            codec=DEFAULT_CODEC,
            preset=DEFAULT_PRESET,
            cq=self._cq_spin.value(),
            parent=self,
        )
        self._worker.job_started.connect(self._on_job_started)
        self._worker.job_progress.connect(self._on_job_progress)
        self._worker.job_finished.connect(self._on_job_finished)
        self._worker.batch_message.connect(self._append_log)
        self._worker.batch_finished.connect(self._on_batch_finished)
        self._worker.start()

        self._start_button.setEnabled(False)
        self._stop_button.setEnabled(True)
        self._cq_spin.setEnabled(False)
        self._append_log(
            f"변환 배치를 시작합니다. 설정: {DEFAULT_CODEC} / {DEFAULT_PRESET} / cq {self._cq_spin.value()}"
        )

    def _stop_processing(self) -> None:
        if self._worker is None:
            return
        self._append_log("중지 요청을 보냈습니다.")
        self._worker.request_cancel()
        self._stop_button.setEnabled(False)

    def _on_job_started(self, row_index: int) -> None:
        job = self._jobs[row_index]
        job.status = "변환 중"
        self._set_cell_text(row_index, STATUS_COLUMN, "변환 중")
        self._set_cell_text(row_index, DETAIL_COLUMN, "")
        self._update_summary()

    def _on_job_progress(self, row_index: int, percent: float, speed: str) -> None:
        job = self._jobs[row_index]
        job.progress = percent
        job.speed = speed
        self._set_cell_text(row_index, PROGRESS_COLUMN, f"{percent:.1f}%")
        self._set_cell_text(row_index, SPEED_COLUMN, speed)
        self._update_summary()

    def _on_job_finished(self, row_index: int, success: bool, speed: str, detail: str) -> None:
        job = self._jobs[row_index]
        job.status = "완료" if success else "실패"
        job.speed = speed
        job.detail = detail
        if success:
            job.progress = 100.0

        self._set_cell_text(row_index, STATUS_COLUMN, job.status)
        self._set_cell_text(row_index, PROGRESS_COLUMN, f"{job.progress:.1f}%")
        self._set_cell_text(row_index, SPEED_COLUMN, speed)
        self._set_cell_text(row_index, DETAIL_COLUMN, detail)
        if success:
            self._append_log(f"완료: {job.queue_entry.source_path.name} -> {detail}")
        else:
            self._append_log(f"실패: {job.queue_entry.source_path.name} ({detail})")
        self._update_summary()

    def _on_batch_finished(self, cancelled: bool) -> None:
        self._worker = None
        self._start_button.setEnabled(True)
        self._stop_button.setEnabled(False)
        self._cq_spin.setEnabled(True)
        if cancelled:
            self._append_log("배치 작업이 중지되었습니다.")
        else:
            self._append_log("배치 작업이 끝났습니다.")
        self._update_summary()
        if self._auto_start_checkbox.isChecked() and any(job.status == "대기" for job in self._jobs):
            self._start_processing()

    def _clear_jobs(self) -> None:
        if self._worker is not None:
            QMessageBox.information(self, "진행 중", "변환 중에는 목록을 비울 수 없습니다.")
            return
        self._jobs.clear()
        self._source_keys.clear()
        self._table.setRowCount(0)
        self._overall_progress.setValue(0)
        self._summary_label.setText("대기 중인 작업이 없습니다.")
        self._append_log("목록을 비웠습니다.")

    def _update_summary(self) -> None:
        total_count = len(self._jobs)
        if total_count == 0:
            self._summary_label.setText("대기 중인 작업이 없습니다.")
            self._overall_progress.setValue(0)
            return

        waiting = sum(1 for job in self._jobs if job.status == "대기")
        active = sum(1 for job in self._jobs if job.status == "변환 중")
        completed = sum(1 for job in self._jobs if job.status == "완료")
        failed = sum(1 for job in self._jobs if job.status == "실패")
        total_progress = sum(job.progress for job in self._jobs) / total_count
        self._summary_label.setText(
            f"전체 {total_count}개 | 대기 {waiting} | 진행 중 {active} | 완료 {completed} | 실패 {failed}"
        )
        self._overall_progress.setValue(int(total_progress))

    def _update_suffix_label(self) -> None:
        self._suffix_label.setText(make_suffix(cq=self._cq_spin.value()))

    def _set_controls_enabled(self, enabled: bool) -> None:
        self._drop_area.setEnabled(enabled)
        self._start_button.setEnabled(enabled)
        self._stop_button.setEnabled(False)
        self._cq_spin.setEnabled(enabled)

    def _set_cell_text(self, row_index: int, column: int, text: str) -> None:
        item = self._table.item(row_index, column)
        if item is None:
            item = QTableWidgetItem()
            self._table.setItem(row_index, column, item)
        item.setText(text)

    def _append_log(self, message: str) -> None:
        timestamp = QTime.currentTime().toString("HH:mm:ss")
        self._log_output.appendPlainText(f"[{timestamp}] {message}")

    def closeEvent(self, event: QCloseEvent) -> None:  # type: ignore[override]
        if self._worker is None:
            event.accept()
            return

        reply = QMessageBox.question(
            self,
            "종료 확인",
            "변환이 진행 중입니다. 중지하고 종료하시겠습니까?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            event.ignore()
            return

        self._worker.request_cancel()
        self._worker.wait(3000)
        event.accept()


def run() -> None:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("영상 드래그앤드롭 변환기")

    window = MainWindow()
    window.show()

    quit_action = QAction("종료", window)
    quit_action.triggered.connect(window.close)
    window.addAction(quit_action)

    app.exec()
