"""Clips -> MP3 parts with chapter markers and SRT subtitles.

    clips  ──trim──▶ durations ──▶ chapter units ──partition──▶ parts
                                        │                         │
                     (chapter > hard max: cut at scene/segment)   ▼
                                                   part.wav ─▶ part.mp3 + part.srt

Parts follow chapter boundaries. Each part is <= target (1 h) when chapters
allow it; one chapter may stand alone up to hard max (2 h); longer chapters
are cut at scene breaks (else segment ends) into <= target units.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from storyaudio.audio import ffmpeg, wavio
from storyaudio.audio.ffmpeg import Marker, Tags
from storyaudio.audio.subtitles import Cue, job_cues, render_srt
from storyaudio.config import PartsCfg

CONTINUED = "{title} (continued)"


class Cut(Enum):
    """How good a place the start of a clip is to cut a long chapter."""

    NEVER = 0   # mid-segment piece
    OK = 1      # segment or speaker change
    GOOD = 2    # scene break


@dataclass(frozen=True)
class Clip:
    wav: Path
    subtitle: str
    chapter: int
    chapter_title: str
    gap_ms: int          # silence before this clip (ignored at a part start)
    cut: Cut


@dataclass
class Unit:
    """A run of clips that must stay in one part (a chapter or chapter slice)."""

    title: str
    clips: list[int] = field(default_factory=list)
    seconds: float = 0.0


@dataclass(frozen=True)
class Part:
    number: int
    mp3: Path
    srt: Path
    seconds: float
    chapters: list[str]


@dataclass(frozen=True)
class Album:
    title: str
    author: str
    stem: str   # file name stem, e.g. "The Mysteries of Bran House"


def assemble(clips: list[Clip], album: Album, out_dir: Path, parts_cfg: PartsCfg, bitrate: str, lufs: float) -> list[Part]:
    out_dir.mkdir(parents=True, exist_ok=True)
    lengths = [_trimmed_length(c.wav) for c in clips]
    units = _units(clips, lengths, parts_cfg)
    groups = partition([u.seconds for u in units], parts_cfg)
    single = len(groups) == 1

    parts: list[Part] = []
    for number, group in enumerate(groups, start=1):
        part_units = [units[i] for i in group]
        stem = album.stem if single else f"{album.stem} - Part {number:02d}"
        parts.append(_write_part(clips, part_units, album, number, len(groups), out_dir / stem, bitrate, lufs))
    return parts


# ---------------------------------------------------------------- planning


def _trimmed_length(path: Path) -> int:
    return wavio.trim(wavio.read(path)).size


def _clip_seconds(clip: Clip, samples: int, first_in_unit: bool) -> float:
    gap = 0 if first_in_unit else clip.gap_ms
    return gap / 1000 + samples / wavio.SAMPLE_RATE


def _units(clips: list[Clip], lengths: list[int], cfg: PartsCfg) -> list[Unit]:
    """One unit per chapter; chapters over hard max are sliced."""
    chapters: list[Unit] = []
    for index, clip in enumerate(clips):
        if not chapters or clips[chapters[-1].clips[-1]].chapter != clip.chapter:
            chapters.append(Unit(clip.chapter_title))
        unit = chapters[-1]
        unit.seconds += _clip_seconds(clip, lengths[index], not unit.clips)
        unit.clips.append(index)

    units: list[Unit] = []
    for chapter in chapters:
        units += [chapter] if chapter.seconds <= cfg.hard_max_s else _slice(chapter, clips, lengths, cfg.target_s)
    return units


def _slice(chapter: Unit, clips: list[Clip], lengths: list[int], target_s: float) -> list[Unit]:
    """Cut a long chapter into <= target slices, preferring scene breaks."""
    slices: list[Unit] = []
    pending = list(chapter.clips)

    while pending:
        # Find the furthest cut that keeps the slice under target, best cut kind first.
        elapsed = 0.0
        best: dict[Cut, int] = {}
        for position, index in enumerate(pending):
            elapsed += _clip_seconds(clips[index], lengths[index], position == 0)
            if elapsed > target_s and position > 0:
                break
            if position > 0:
                best[clips[index].cut] = position

        fits_all = elapsed <= target_s
        end = len(pending) if fits_all else best.get(Cut.GOOD) or best.get(Cut.OK) or best.get(Cut.NEVER) or 1
        taken, pending = pending[:end], pending[end:]

        title = chapter.title if not slices else CONTINUED.format(title=chapter.title)
        seconds = sum(_clip_seconds(clips[i], lengths[i], p == 0) for p, i in enumerate(taken))
        slices.append(Unit(title, taken, seconds))

    return slices


def partition(seconds: list[float], cfg: PartsCfg) -> list[list[int]]:
    """Split consecutive units into the fewest parts, then the most even ones.

    A part is valid when its total is <= target, or it is a single unit.
    Example (target 60): [40, 30, 20, 50] -> [[0], [1, 2], [3]]
    """
    count = len(seconds)
    prefix = [0.0]
    for value in seconds:
        prefix.append(prefix[-1] + value)

    # best[i] = (parts, longest part, split points) for the first i units.
    best: list[tuple[int, float, list[int]] | None] = [None] * (count + 1)
    best[0] = (0, 0.0, [])

    for end in range(1, count + 1):
        for start in range(end - 1, -1, -1):
            total = prefix[end] - prefix[start]
            if total > cfg.target_s and end - start > 1:
                break  # earlier starts only grow the part
            previous = best[start]
            if previous is None:
                continue
            candidate = (previous[0] + 1, max(previous[1], total), previous[2] + [start])
            if best[end] is None or candidate[:2] < best[end][:2]:
                best[end] = candidate

    starts = best[count][2] + [count]
    return [list(range(a, b)) for a, b in zip(starts, starts[1:])]


# ---------------------------------------------------------------- writing


def _write_part(clips: list[Clip], units: list[Unit], album: Album, number: int, total: int, stem: Path, bitrate: str, lufs: float) -> Part:
    mp3 = stem.with_suffix(".mp3")
    srt = stem.with_suffix(".srt")
    tags = Tags(
        title=album.title if total == 1 else f"{album.title} - Part {number}",
        artist=album.author,
        album=album.title,
        track=f"{number}/{total}",
    )

    # Stream clips into one WAV: a 1 h part is ~300 MB of samples.
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "part.wav"
        with wavio.Writer(wav) as writer:
            cues, markers = _stream_units(clips, units, writer)
        seconds = writer.samples / wavio.SAMPLE_RATE
        ffmpeg.encode_mp3(wav, mp3, tags, markers, bitrate, lufs)

    srt.write_text(render_srt(cues), encoding="utf-8")
    return Part(number, mp3, srt, seconds, [u.title for u in units])


def _stream_units(clips: list[Clip], units: list[Unit], writer: wavio.Writer) -> tuple[list[Cue], list[Marker]]:
    cues: list[Cue] = []
    markers: list[Marker] = []

    for unit in units:
        unit_start = writer.samples
        for index in unit.clips:
            clip = clips[index]
            if writer.samples > 0:
                writer.add(wavio.silence(clip.gap_ms))

            start = writer.samples / wavio.SAMPLE_RATE
            writer.add(wavio.trim(wavio.read(clip.wav)))
            cues += job_cues(clip.subtitle, start, writer.samples / wavio.SAMPLE_RATE)

        markers.append(Marker(_ms(unit_start), _ms(writer.samples), unit.title))

    return cues, markers


def _ms(samples: int) -> int:
    return round(samples * 1000 / wavio.SAMPLE_RATE)
