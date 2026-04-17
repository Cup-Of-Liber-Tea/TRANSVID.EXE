from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
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
DEFAULT_CQ = 28
DEFAULT_TARGET_FPS = 24
DEFAULT_PARALLEL_JOBS = 2
DEFAULT_BASELINE_REALTIME = 29.0
DEFAULT_SUFFIX_TEMPLATE = ".{codec}_{preset}_cq{cq}{fps_suffix}"
FPS_OPTIONS = [0, 24, 30]
PARALLEL_JOB_OPTIONS = [1, 2, 3]


@dataclass(frozen=True, slots=True)
class EncoderProfile:
    codec: str
    preset: str
    display_name: str
    quality_label: str
    baseline_realtime: float
    parallel_jobs: int = DEFAULT_PARALLEL_JOBS


@dataclass(frozen=True, slots=True)
class EncoderDetectionResult:
    profile: EncoderProfile
    message: str


ENCODER_PROFILES = {
    "hevc_nvenc": EncoderProfile(
        codec="hevc_nvenc",
        preset="p1",
        display_name="NVIDIA NVENC",
        quality_label="CQ",
        baseline_realtime=29.0,
        parallel_jobs=2,
    ),
    "hevc_qsv": EncoderProfile(
        codec="hevc_qsv",
        preset="veryfast",
        display_name="Intel Quick Sync",
        quality_label="GQ",
        baseline_realtime=18.0,
        parallel_jobs=2,
    ),
    "hevc_amf": EncoderProfile(
        codec="hevc_amf",
        preset="speed",
        display_name="AMD AMF",
        quality_label="CQ",
        baseline_realtime=22.0,
        parallel_jobs=2,
    ),
    "libx265": EncoderProfile(
        codec="libx265",
        preset="veryfast",
        display_name="CPU x265",
        quality_label="CRF",
        baseline_realtime=4.0,
        parallel_jobs=1,
    ),
}
ENCODER_DETECTION_ORDER = ("hevc_nvenc", "hevc_qsv", "hevc_amf", "libx265")
DEFAULT_PRESET = ENCODER_PROFILES[DEFAULT_CODEC].preset


@dataclass(slots=True)
class VideoInfo:
    source_path: Path
    codec_name: str
    audio_codec_name: str | None
    width: int
    height: int
    duration_seconds: float
    container_duration_seconds: float
    video_duration_seconds: float
    audio_duration_seconds: float
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


def get_subprocess_windowless_kwargs() -> dict[str, object]:
    if os.name != "nt":
        return {}

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {
        "creationflags": subprocess.CREATE_NO_WINDOW,
        "startupinfo": startupinfo,
    }


def get_encoder_profile(codec: str = DEFAULT_CODEC) -> EncoderProfile:
    return ENCODER_PROFILES.get(codec, ENCODER_PROFILES[DEFAULT_CODEC])


@lru_cache(maxsize=1)
def _list_ffmpeg_encoders() -> set[str]:
    completed = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        **get_subprocess_windowless_kwargs(),
    )
    if completed.returncode != 0:
        return set()

    encoders: set[str] = set()
    for line in completed.stdout.splitlines():
        match = re.match(r"^\s*[A-Z\.]{6}\s+([a-z0-9_]+)\s", line)
        if match:
            encoders.add(match.group(1))
    return encoders


def _build_profile_video_args(profile: EncoderProfile, quality_value: int) -> list[str]:
    if profile.codec == "hevc_nvenc":
        return [
            "-c:v",
            profile.codec,
            "-preset",
            profile.preset,
            "-rc",
            "vbr",
            "-cq",
            str(quality_value),
            "-b:v",
            "0",
            "-tag:v",
            "hvc1",
        ]

    if profile.codec == "hevc_qsv":
        return [
            "-c:v",
            profile.codec,
            "-preset",
            profile.preset,
            "-global_quality",
            str(quality_value),
            "-b:v",
            "0",
            "-tag:v",
            "hvc1",
        ]

    if profile.codec == "hevc_amf":
        return [
            "-c:v",
            profile.codec,
            "-quality",
            profile.preset,
            "-rc",
            "qvbr",
            "-qvbr_quality_level",
            str(quality_value),
            "-tag:v",
            "hvc1",
        ]

    if profile.codec == "libx265":
        return [
            "-c:v",
            profile.codec,
            "-preset",
            profile.preset,
            "-crf",
            str(quality_value),
            "-tag:v",
            "hvc1",
        ]

    raise ValueError(f"지원하지 않는 인코더입니다: {profile.codec}")


def _probe_encoder_profile(profile: EncoderProfile, quality_value: int = DEFAULT_CQ) -> tuple[bool, str]:
    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=1280x720:r=24:d=0.2",
        "-frames:v",
        "3",
        "-an",
        *_build_profile_video_args(profile, quality_value),
        "-f",
        "null",
        "-",
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=20,
            **get_subprocess_windowless_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return False, "encoder probe timed out"
    stderr_text = (completed.stderr or "").strip()
    return completed.returncode == 0, stderr_text


@lru_cache(maxsize=1)
def detect_encoder() -> EncoderDetectionResult:
    available_encoders = _list_ffmpeg_encoders()
    failed_hardware_profiles: list[str] = []

    for codec in ENCODER_DETECTION_ORDER:
        profile = get_encoder_profile(codec)
        if codec not in available_encoders:
            continue
        if codec == "libx265":
            if failed_hardware_profiles:
                message = (
                    "하드웨어 HEVC 인코더를 초기화하지 못해 CPU x265로 전환합니다. "
                    f"({', '.join(failed_hardware_profiles)} 실패)"
                )
            else:
                message = "하드웨어 HEVC 인코더를 찾지 못해 CPU x265로 전환합니다."
            return EncoderDetectionResult(profile=profile, message=message)

        is_ready, error_message = _probe_encoder_profile(profile)
        if is_ready:
            message = f"하드웨어 감지 완료: {profile.display_name} ({profile.codec} / {profile.preset})"
            return EncoderDetectionResult(profile=profile, message=message)

        if error_message:
            failed_hardware_profiles.append(profile.display_name)

    fallback_codec = "libx265" if "libx265" in available_encoders else DEFAULT_CODEC
    fallback_profile = get_encoder_profile(fallback_codec)
    return EncoderDetectionResult(
        profile=fallback_profile,
        message=(
            "사용 가능한 HEVC 인코더를 찾지 못했습니다. "
            f"{fallback_profile.display_name} 설정으로 동작합니다."
        ),
    )


def make_suffix(
    codec: str = DEFAULT_CODEC,
    preset: str = DEFAULT_PRESET,
    cq: int = DEFAULT_CQ,
    target_fps: int = 0,
) -> str:
    fps_suffix = f"_fps{target_fps}" if target_fps > 0 else ""
    return DEFAULT_SUFFIX_TEMPLATE.format(codec=codec, preset=preset, cq=cq, fps_suffix=fps_suffix)


def format_target_fps(target_fps: int) -> str:
    if target_fps <= 0:
        return "원본 유지"
    return f"{target_fps} fps"


def estimate_speed_multiplier(
    cq: int,
    target_fps: int,
    parallel_jobs: int,
) -> float:
    cq_factor = 1.0 + ((cq - DEFAULT_CQ) * 0.02)
    cq_factor = max(0.84, min(cq_factor, 1.18))

    if target_fps <= 0:
        fps_factor = 1.0
    else:
        fps_factor = 30.0 / float(target_fps)
        fps_factor = max(0.8, min(fps_factor, 1.35))

    parallel_factor_map = {
        1: 1.0,
        2: 1.65,
        3: 1.95,
    }
    parallel_factor = parallel_factor_map.get(parallel_jobs, 1.0 + (parallel_jobs - 1) * 0.45)

    return round(cq_factor * fps_factor * parallel_factor, 2)


def estimate_realtime_speed(
    baseline_realtime: float,
    cq: int,
    target_fps: int,
    parallel_jobs: int,
) -> float:
    multiplier = estimate_speed_multiplier(cq, target_fps, parallel_jobs)
    return round(baseline_realtime * multiplier, 1)


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


def _parse_duration_value(value: object) -> float:
    if value in (None, "", "N/A"):
        return 0.0
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    if parsed <= 0:
        return 0.0
    return parsed


def _select_duration_seconds(
    video_duration_seconds: float,
    audio_duration_seconds: float,
    container_duration_seconds: float,
) -> float:
    stream_durations = [value for value in (video_duration_seconds, audio_duration_seconds) if value > 0]
    if stream_durations:
        stream_duration = max(stream_durations)
        if container_duration_seconds <= 0:
            return stream_duration
        if container_duration_seconds > stream_duration * 1.02:
            return stream_duration
    return container_duration_seconds if container_duration_seconds > 0 else max(stream_durations, default=0.0)


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
            "stream=index,codec_type,codec_name,width,height,duration"
        ),
        "-of",
        "json",
        str(source_path),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        **get_subprocess_windowless_kwargs(),
    )
    if completed.returncode != 0:
        stderr_text = (completed.stderr or "").strip()
        normalized = stderr_text.lower()
        if "moov atom not found" in normalized:
            raise ValueError("moov atom not found: 손상되었거나 녹화가 정상 종료되지 않은 MP4 파일입니다.")
        if stderr_text:
            raise ValueError(stderr_text)
        raise ValueError("ffprobe가 파일을 해석하지 못했습니다.")

    payload = json.loads(completed.stdout)
    streams = payload.get("streams", [])
    format_info = payload.get("format", {})

    video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    if video_stream is None:
        raise ValueError(f"비디오 스트림이 없습니다: {source_path}")

    audio_stream = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    container_duration_seconds = _parse_duration_value(format_info.get("duration"))
    video_duration_seconds = _parse_duration_value(video_stream.get("duration"))
    audio_duration_seconds = _parse_duration_value(audio_stream.get("duration")) if audio_stream else 0.0
    duration_seconds = _select_duration_seconds(
        video_duration_seconds=video_duration_seconds,
        audio_duration_seconds=audio_duration_seconds,
        container_duration_seconds=container_duration_seconds,
    )

    return VideoInfo(
        source_path=source_path,
        codec_name=video_stream.get("codec_name", "unknown"),
        audio_codec_name=audio_stream.get("codec_name") if audio_stream else None,
        width=int(video_stream.get("width") or 0),
        height=int(video_stream.get("height") or 0),
        duration_seconds=duration_seconds,
        container_duration_seconds=container_duration_seconds,
        video_duration_seconds=video_duration_seconds,
        audio_duration_seconds=audio_duration_seconds,
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
    encoder_profile: EncoderProfile | None = None,
    cq: int = DEFAULT_CQ,
    target_fps: int = 0,
) -> list[str]:
    profile = encoder_profile or get_encoder_profile()
    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-nostats",
        "-progress",
        "pipe:1",
        "-i",
        str(source_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
    ]
    if target_fps > 0:
        command.extend(["-r", str(target_fps)])
    command.extend(_build_profile_video_args(profile, cq))
    command.extend(["-c:a", "copy", str(output_path)])
    return command
