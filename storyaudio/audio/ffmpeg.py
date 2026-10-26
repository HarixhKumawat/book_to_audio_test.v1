"""ffmpeg/ffprobe driver: format conversion and MP3 encoding with chapters."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from storyaudio.audio.wavio import CHANNELS, SAMPLE_RATE

TRUE_PEAK_DB = -2.0
LOUDNESS_RANGE = 11.0
_LOUDNORM_JSON = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}", re.DOTALL)


class FfmpegError(RuntimeError):
    pass


@dataclass(frozen=True)
class Marker:
    """An MP3 chapter marker (ID3 CHAP frame)."""

    start_ms: int
    end_ms: int
    title: str


@dataclass(frozen=True)
class Tags:
    title: str
    artist: str
    album: str
    track: str


def require() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise FfmpegError(f"{tool} not found on PATH (winget install Gyan.FFmpeg)")


def to_canonical(source: Path, target: Path) -> None:
    """Any audio file -> 44.1 kHz mono int16 WAV."""
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(source), "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE), "-sample_fmt", "s16", str(target)])


def encode_mp3(wav: Path, mp3: Path, tags: Tags, markers: list[Marker], bitrate: str, lufs: float) -> None:
    """Two-pass EBU R128 loudness normalization, then MP3 with chapters.

    Pass 1 measures the file; pass 2 applies a linear gain with those numbers,
    which avoids the pumping of one-pass loudnorm.
    """
    measured = _measure(wav, lufs)
    loudnorm = (
        f"loudnorm=I={lufs}:TP={TRUE_PEAK_DB}:LRA={LOUDNESS_RANGE}"
        f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
        f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
        f":offset={measured['target_offset']}:linear=true"
    )

    with tempfile.TemporaryDirectory() as tmp:
        meta = Path(tmp) / "meta.txt"
        meta.write_text(_ffmetadata(tags, markers), encoding="utf-8")
        _run([
            "ffmpeg", "-y", "-v", "error",
            "-i", str(wav), "-i", str(meta),
            "-map", "0:a", "-map_metadata", "1", "-map_chapters", "1",
            "-af", loudnorm, "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS),
            "-codec:a", "libmp3lame", "-b:a", bitrate, "-id3v2_version", "3",
            str(mp3),
        ])


def duration(path: Path) -> float:
    out = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)])
    return float(json.loads(out)["format"]["duration"])


def chapters(path: Path) -> list[dict]:
    out = _run(["ffprobe", "-v", "error", "-show_chapters", "-of", "json", str(path)])
    return json.loads(out).get("chapters", [])


def _measure(wav: Path, lufs: float) -> dict[str, str]:
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(wav),
         "-af", f"loudnorm=I={lufs}:TP={TRUE_PEAK_DB}:LRA={LOUDNESS_RANGE}:print_format=json",
         "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    match = _LOUDNORM_JSON.search(result.stderr)
    if result.returncode != 0 or match is None:
        raise FfmpegError(f"loudness measure failed: {result.stderr[-500:]}")

    measured = json.loads(match.group(0))
    # Pure digital silence measures -inf; fall back to a neutral gain.
    if "inf" in measured["input_i"]:
        measured.update(input_i="-70", input_tp="-70", input_lra="0", input_thresh="-80", target_offset="0")
    return measured


def _ffmetadata(tags: Tags, markers: list[Marker]) -> str:
    lines = [
        ";FFMETADATA1",
        f"title={_escape(tags.title)}",
        f"artist={_escape(tags.artist)}",
        f"album={_escape(tags.album)}",
        f"track={_escape(tags.track)}",
        "genre=Audiobook",
    ]
    for marker in markers:
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={marker.start_ms}", f"END={marker.end_ms}", f"title={_escape(marker.title)}"]
    return "\n".join(lines) + "\n"


def _escape(value: str) -> str:
    """ffmetadata reserves = ; # \\ and newlines."""
    return re.sub(r"([=;#\\\n])", r"\\\1", value)


def _run(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise FfmpegError(f"{args[0]} failed ({result.returncode}): {result.stderr.strip()[-800:]}")
    return result.stdout
