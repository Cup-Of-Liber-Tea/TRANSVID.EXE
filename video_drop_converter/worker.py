from __future__ import annotations

import subprocess
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .core import EncoderProfile, QueueEntry, build_ffmpeg_command, format_file_size


class ConversionWorker(QThread):
    job_started = Signal(int)
    job_progress = Signal(int, float, str)
    job_finished = Signal(int, bool, str, str)
    batch_finished = Signal(bool)
    batch_message = Signal(str)

    def __init__(
        self,
        queued_rows: list[tuple[int, QueueEntry]],
        encoder_profile: EncoderProfile,
        cq: int,
        target_fps: int,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._queued_rows = queued_rows
        self._encoder_profile = encoder_profile
        self._cq = cq
        self._target_fps = target_fps
        self._cancel_requested = False
        self._process: subprocess.Popen[str] | None = None

    def request_cancel(self) -> None:
        self._cancel_requested = True
        if self._process and self._process.poll() is None:
            self._process.terminate()

    def run(self) -> None:
        cancelled = False
        for row_index, queue_entry in self._queued_rows:
            if self._cancel_requested:
                cancelled = True
                break

            self.job_started.emit(row_index)
            command = build_ffmpeg_command(
                queue_entry.source_path,
                queue_entry.output_path,
                encoder_profile=self._encoder_profile,
                cq=self._cq,
                target_fps=self._target_fps,
            )
            self.batch_message.emit(f"변환 시작: {queue_entry.source_path.name}")

            try:
                self._process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                )
            except OSError as exc:
                self.job_finished.emit(row_index, False, "", str(exc))
                continue

            latest_speed = "-"
            last_percent = 0.0

            assert self._process.stdout is not None
            progress_buffer: dict[str, str] = {}
            for raw_line in self._process.stdout:
                line = raw_line.strip()
                if not line or "=" not in line:
                    continue

                key, value = line.split("=", 1)
                progress_buffer[key] = value

                if key == "speed":
                    latest_speed = value

                if key == "progress":
                    out_time_us = progress_buffer.get("out_time_us")
                    if out_time_us:
                        processed_seconds = max(float(out_time_us) / 1_000_000.0, 0.0)
                        if queue_entry.info.duration_seconds > 0:
                            last_percent = min(processed_seconds / queue_entry.info.duration_seconds, 1.0) * 100.0
                    self.job_progress.emit(row_index, last_percent, latest_speed)
                    progress_buffer.clear()

                    if self._cancel_requested:
                        self.request_cancel()
                        break

            stderr_output = ""
            if self._process.stderr is not None:
                stderr_output = self._process.stderr.read().strip()

            return_code = self._process.wait()
            self._process = None

            if self._cancel_requested:
                cancelled = True
                self._cleanup_partial_output(queue_entry.output_path)
                self.job_finished.emit(row_index, False, latest_speed, "사용자가 중지했습니다.")
                break

            if return_code == 0 and queue_entry.output_path.exists():
                output_size = format_file_size(queue_entry.output_path.stat().st_size)
                self.job_progress.emit(row_index, 100.0, latest_speed)
                self.job_finished.emit(row_index, True, latest_speed, output_size)
                continue

            self._cleanup_partial_output(queue_entry.output_path)
            error_message = stderr_output or f"ffmpeg 종료 코드: {return_code}"
            self.job_finished.emit(row_index, False, latest_speed, error_message)

        self.batch_finished.emit(cancelled)

    @staticmethod
    def _cleanup_partial_output(output_path: Path) -> None:
        if output_path.exists():
            try:
                output_path.unlink()
            except OSError:
                pass
