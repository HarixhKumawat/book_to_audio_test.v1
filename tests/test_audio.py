"""Part partitioning, subtitles, WAV trim, ffmpeg encoding."""

from pathlib import Path

import numpy as np
import pytest

from storyaudio.audio import ffmpeg, wavio
from storyaudio.audio.assemble import Album, Clip, Cut, assemble, partition
from storyaudio.audio.subtitles import job_cues, render_srt
from storyaudio.config import PartsCfg

HOUR = 3600.0
CFG = PartsCfg(target_s=HOUR, hard_max_s=2 * HOUR)


# ---------------------------------------------------------------- partition


def test_short_book_is_one_part():
    assert partition([600, 900, 1200], CFG) == [[0, 1, 2]]


def test_parts_stay_under_target_and_balance():
    minutes = [m * 60 for m in (40, 30, 20, 50)]

    assert partition(minutes, CFG) == [[0], [1, 2], [3]]


def test_long_chapter_may_stand_alone():
    assert partition([30 * 60, 90 * 60, 30 * 60], CFG) == [[0], [1], [2]]


def test_partition_prefers_even_parts():
    # 6 x 20 min: two parts of 60, not 60 + 40 + 20.
    assert partition([20 * 60] * 6, CFG) == [[0, 1, 2], [3, 4, 5]]


# ---------------------------------------------------------------- subtitles


def test_cues_cover_the_clip_exactly():
    cues = job_cues("First sentence. Second sentence is longer than the first.", 10.0, 16.0)

    assert cues[0].start_s == 10.0
    assert cues[-1].end_s == pytest.approx(16.0)
    assert all(a.end_s == pytest.approx(b.start_s) for a, b in zip(cues, cues[1:]))


def test_srt_format():
    srt = render_srt(job_cues("Hello there.", 3661.5, 3662.0))

    assert srt.startswith("1\n01:01:01,500 --> 01:01:02,000\nHello there.\n")


# ---------------------------------------------------------------- wav


def test_trim_removes_edge_silence_with_padding():
    tone = (np.sin(np.arange(wavio.SAMPLE_RATE) / 5) * 10000).astype(np.int16)
    samples = np.concatenate([wavio.silence(500), tone, wavio.silence(500)])

    trimmed = wavio.trim(samples, pad_ms=40)

    assert abs(trimmed.size - (tone.size + 2 * wavio.SAMPLE_RATE * 0.04)) < wavio.SAMPLE_RATE // 50


def test_trim_keeps_pure_silence():
    assert wavio.trim(wavio.silence(300)).size == wavio.silence(300).size


# ---------------------------------------------------------------- assemble


def _tone_wav(path: Path, seconds: float) -> Path:
    t = np.arange(int(seconds * wavio.SAMPLE_RATE)) / wavio.SAMPLE_RATE
    wavio.write(path, (np.sin(2 * np.pi * 220 * t) * 8000).astype(np.int16))
    return path


def test_assemble_splits_into_parts_with_chapters(tmp_path: Path):
    ffmpeg.require()
    clips = []
    for chapter in range(3):
        for line in range(2):
            wav = _tone_wav(tmp_path / f"c{chapter}l{line}.wav", 1.0)
            clips.append(Clip(wav, f"Chapter {chapter} line {line}.", chapter, f"Ch {chapter}", 300, Cut.OK))

    parts = assemble(clips, Album("Book", "Author", "Book"), tmp_path / "out", PartsCfg(target_s=5, hard_max_s=10), "64k", -19.0)

    assert len(parts) == 2
    assert parts[0].mp3.name == "Book - Part 01.mp3"
    assert parts[0].chapters == ["Ch 0", "Ch 1"]
    assert [c["tags"]["title"] for c in ffmpeg.chapters(parts[0].mp3)] == ["Ch 0", "Ch 1"]
    assert ffmpeg.duration(parts[0].mp3) == pytest.approx(parts[0].seconds, abs=0.1)
    assert "Chapter 2 line 1." in parts[1].srt.read_text(encoding="utf-8")


def test_assemble_slices_chapter_over_hard_max_at_scene(tmp_path: Path):
    ffmpeg.require()
    cuts = [Cut.OK, Cut.OK, Cut.GOOD, Cut.OK]
    clips = [Clip(_tone_wav(tmp_path / f"l{i}.wav", 1.0), f"Line {i}.", 0, "Long", 0, cut) for i, cut in enumerate(cuts)]

    parts = assemble(clips, Album("B", "A", "B"), tmp_path / "out", PartsCfg(target_s=3.0, hard_max_s=3.5), "64k", -19.0)

    assert [p.chapters for p in parts] == [["Long"], ["Long (continued)"]]
    assert "Line 2." in parts[1].srt.read_text(encoding="utf-8")
