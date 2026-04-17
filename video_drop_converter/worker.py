from __future__ import annotations

import subprocess
import threading
from collections import deque
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .core import (
    EncoderProfile,
    QueueEntry,
    build_ffmpeg_command,
    format_file_size,
    get_subprocess_windowless_kwargs,
)


_PROGRESS_KEYS = {
    "bitrate",
    "drop_frames",
    "dup_frames",
    "fps",
    "frame",
    "out_time",
    "out_time_ms",
    "out_time_us",
    "progress",
    "speed",
    "stream_0_0_q",
    "total_size",
}


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
            self.batch_message.emit(f"\ubcc0\ud658 \uc2dc\uc791: {queue_entry.source_path.name}")

            try:
                self._process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    **get_subprocess_windowless_kwargs(),
                )
            except OSError as exc:
                self.job_finished.emit(row_index, False, "", str(exc))
                continue

            latest_speed = "-"
            last_percent = 0.0
            finalizing_logged = False
            progress_buffer: dict[str, str] = {}
            stderr_lines: deque[str] = deque(maxlen=40)

            assert self._process.stdout is not None
            assert self._process.stderr is not None

            stderr_reader = threading.Thread(
                target=self._drain_stream,
                args=(self._process.stderr, stderr_lines),
                daemon=True,
            )
            stderr_reader.start()

            for raw_line in self._process.stdout:
                line = raw_line.strip()
                if not line or "=" not in line:
                    continue

                key, value = line.split("=", 1)
                if key not in _PROGRESS_KEYS:
                    continue

                progress_buffer[key] = value

                if key == "speed":
                    latest_speed = value

                if key == "progress":
                    last_percent = self._extract_percent(
                        progress_buffer,
                        queue_entry.info.duration_seconds,
                        last_percent,
                    )
                    if value == "end":
                        last_percent = 100.0
                        if not finalizing_logged:
                            self.batch_message.emit(f"\ub9c8\ubb34\ub9ac \uc911: {queue_entry.source_path.name}")
                            finalizing_logged = True

                    self.job_progress.emit(row_index, last_percent, latest_speed)
                    progress_buffer.clear()

                    if self._cancel_requested:
                        self.request_cancel()
                        break

            return_code = self._process.wait()
            stderr_reader.join()
            stderr_output = "\n".join(stderr_lines).strip()
            self._process = None

            if self._cancel_requested:
                cancelled = True
                self._cleanup_partial_output(queue_entry.output_path)
                self.job_finished.emit(row_index, False, latest_speed, "\uc0ac\uc6a9\uc790\uac00 \uc911\uc9c0\ud588\uc2b5\ub2c8\ub2e4.")
                break

            if return_code == 0 and queue_entry.output_path.exists():
                output_size = format_file_size(queue_entry.output_path.stat().st_size)
                self.job_progress.emit(row_index, 100.0, latest_speed)
                self.job_finished.emit(row_index, True, latest_speed, output_size)
                continue

            self._cleanup_partial_output(queue_entry.output_path)
            error_message = stderr_output or f"ffmpeg \uc885\ub8cc \ucf54\ub4dc: {return_code}"
            self.job_finished.emit(row_index, False, latest_speed, error_message)

        self.batch_finished.emit(cancelled)

    @staticmethod
    def _drain_stream(stream, sink: deque[str]) -> None:
        for raw_line in stream:
            line = raw_line.strip()
            if line:
                sink.append(line)

    @staticmethod
    def _extract_percent(
        progress_buffer: dict[str, str],
        duration_seconds: float,
        current_percent: float,
    ) -> float:
        out_time_us = progress_buffer.get("out_time_us")
        if not out_time_us or duration_seconds <= 0:
            return current_percent

        try:
            processed_seconds = max(float(out_time_us) / 1_000_000.0, 0.0)
        except ValueError:
            return current_percent

        return min(processed_seconds / duration_seconds, 1.0) * 100.0

    @staticmethod
    def _cleanup_partial_output(output_path: Path) -> None:
        if output_path.exists():
            try:
                output_path.unlink()
            except OSError:
                pass
