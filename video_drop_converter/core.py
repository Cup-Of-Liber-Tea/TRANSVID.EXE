from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


VIDEO_EXTENSIONS = {
    ".mp4",
    ".m4v",
    ".mov",
    ".mkv",
    ".avi",
    ".wmv",
    ".webm",
    ".ts",
    ".m2ts",
}

DEFAULT_CODEC = "hevc_nvenc"
DEFAULT_PRESET = "p1"
DEFAULT_CQ = 28
DEFAULT_SUFFIX_TEMPLATE = ".{codec}_{preset}_cq{cq}"


@dataclass(slots=True)
class VideoInfo:
    source_path: Path
    codec_name: str
    audio_codec_name: str | None
    width: int
    height: int
    duration_seconds: float
    file_size_bytes: int


@dataclass(slots=True)
class QueueEntry:
    source_path: Path
    output_path: Path
    info: VideoInfo


def ensure_ffmpeg_tools() -> list[str]:
    missing = []
    for tool_name in ("ffmpeg", "ffprobe"):
        if shutil.which(tool_name) is None:
            missing.append(tool_name)
    return missing


def make_suffix(codec: str = DEFAULT_CODEC, preset: str = DEFAULT_PRESET, cq: int = DEFAULT_CQ) -> str:
    return DEFAULT_SUFFIX_TEMPLATE.format(codec=codec, preset=preset, cq=cq)


def format_duration(duration_seconds: float) -> str:
    rounded = max(int(duration_seconds), 0)
    hours, remainder = divmod(rounded, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def format_file_size(file_size_bytes: int) -> str:
    size = float(file_size_bytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024.0 or unit == "TiB":
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} TiB"


def is_video_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS


def discover_video_files(paths: Iterable[Path], suffix: str, recursive: bool = True) -> list[Path]:
    discovered: dict[str, Path] = {}
    for raw_path in paths:
        path = raw_path.expanduser().resolve()
        if not path.exists():
            continue
        if path.is_dir():
            pattern = "*"
            iterator = path.rglob(pattern) if recursive else path.glob(pattern)
            for child in iterator:
                if is_video_file(child) and suffix not in child.stem:
                    discovered[str(child).lower()] = child
            continue
        if is_video_file(path) and suffix not in path.stem:
            discovered[str(path).lower()] = path
    return sorted(discovered.values(), key=lambda item: str(item).lower())


def probe_video(source_path: Path) -> VideoInfo:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        (
            "format=duration,size:"
            "stream=index,codec_type,codec_name,width,height"
        ),
        "-of",
        "json",
        str(source_path),
    ]
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    payload = json.loads(completed.stdout)
    streams = payload.get("streams", [])
    format_info = payload.get("format", {})

    video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    if video_stream is None:
        raise ValueError(f"비디오 스트림이 없습니다: {source_path}")

    audio_stream = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)

    return VideoInfo(
        source_path=source_path,
        codec_name=video_stream.get("codec_name", "unknown"),
        audio_codec_name=audio_stream.get("codec_name"),
        width=int(video_stream.get("width") or 0),
        height=int(video_stream.get("height") or 0),
        duration_seconds=float(format_info.get("duration") or 0.0),
        file_size_bytes=int(format_info.get("size") or 0),
    )


def build_output_path(
    source_path: Path,
    suffix: str,
    extension: str | None = None,
) -> Path:
    final_extension = extension or source_path.suffix
    return source_path.with_name(f"{source_path.stem}{suffix}{final_extension}")


def build_ffmpeg_command(
    source_path: Path,
    output_path: Path,
    codec: str = DEFAULT_CODEC,
    preset: str = DEFAULT_PRESET,
    cq: int = DEFAULT_CQ,
) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostats",
        "-progress",
        "pipe:1",
        "-i",
        str(source_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-c:v",
        codec,
        "-preset",
        preset,
        "-rc",
        "vbr",
        "-cq",
        str(cq),
        "-b:v",
        "0",
        "-tag:v",
        "hvc1",
        "-c:a",
        "copy",
        str(output_path),
    ]
