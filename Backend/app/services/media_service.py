"""Lecture recordings: content checks, probing and audio decoding with FFmpeg.

FFmpeg runs as a separate process with a timeout, so a crafted media file cannot hang
the API or worker. Recordings are never parsed by Python media libraries in-process.
"""
import json
import logging
import subprocess
import numpy as np
from app.core.config import settings

logger = logging.getLogger(__name__)

RECORDING_TYPES = {"mp3", "m4a", "wav", "ogg", "webm", "mp4"}
MEDIA_TYPES = {"mp3": "audio/mpeg", "m4a": "audio/mp4", "wav": "audio/wav", "ogg": "audio/ogg",
               "webm": "video/webm", "mp4": "video/mp4"}
SAMPLE_RATE = 16000
PROBE_TIMEOUT_SECONDS = 30


class MediaError(ValueError):
    """The upload is not a usable recording; the message is safe to show."""


class MediaToolMissing(RuntimeError):
    """FFmpeg is not installed or not on the configured path."""


def is_recording(file_type: str | None) -> bool:
    return file_type in RECORDING_TYPES


def check_signature(file, file_type: str) -> None:
    """Match the container's magic bytes to the extension before the file is stored."""
    file.seek(0)
    head = file.read(16)
    file.seek(0)
    matches = {
        "wav": head[:4] == b"RIFF" and head[8:12] == b"WAVE",
        "mp3": head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0),
        "ogg": head[:4] == b"OggS",
        "webm": head[:4] == b"\x1aE\xdf\xa3",
        "mp4": head[4:8] == b"ftyp",
        "m4a": head[4:8] == b"ftyp",
    }
    if not matches.get(file_type):
        raise MediaError("The file content does not match its extension")


def _run(command: list[str], timeout: float) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, capture_output=True, timeout=timeout, check=False, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise MediaToolMissing("FFmpeg is not available")


def probe(path: str) -> dict:
    """Return the recording's duration and whether it has audio and video streams."""
    try:
        result = _run([settings.FFPROBE_PATH, "-v", "error", "-print_format", "json",
                       "-show_format", "-show_streams", path], PROBE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        raise MediaError("The recording took too long to read; it may be damaged")
    if result.returncode != 0:
        raise MediaError("The recording could not be read; check that it is a complete audio or video file")
    try:
        info = json.loads(result.stdout or b"{}")
        streams = info.get("streams") or []
        duration = float(info.get("format", {}).get("duration") or 0)
    except (ValueError, TypeError, AttributeError):
        raise MediaError("The recording could not be read; check that it is a complete audio or video file")
    kinds = {stream.get("codec_type") for stream in streams if isinstance(stream, dict)}
    # Cover art is a video stream with a single attached picture; it does not make audio a video.
    video = any(stream.get("codec_type") == "video" and not (stream.get("disposition") or {}).get("attached_pic")
                for stream in streams if isinstance(stream, dict))
    if "audio" not in kinds:
        raise MediaError("The recording has no audio track")
    if not np.isfinite(duration) or duration <= 0:
        raise MediaError("The recording's length could not be determined")
    if duration > settings.MAX_RECORDING_MINUTES * 60:
        raise MediaError(f"Recordings are limited to {settings.MAX_RECORDING_MINUTES} minutes")
    return {"duration": duration, "kind": "video" if video else "audio"}


def decode_audio(path: str, duration: float | None = None) -> np.ndarray:
    """Decode the first audio track to 16 kHz mono float32 samples for speech recognition."""
    # Decoding is far faster than real time; allow generous headroom for slow machines.
    timeout = 120 + 2 * (duration or settings.MAX_RECORDING_MINUTES * 60)
    try:
        result = _run([settings.FFMPEG_PATH, "-nostdin", "-v", "error", "-i", path, "-map", "0:a:0", "-vn",
                       "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"], timeout)
    except subprocess.TimeoutExpired:
        raise MediaError("Decoding the recording took too long")
    if result.returncode != 0 or not result.stdout:
        raise MediaError("The recording's audio could not be decoded")
    return np.frombuffer(result.stdout, dtype=np.float32)
