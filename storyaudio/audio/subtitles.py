"""SRT subtitles. Each job becomes 1+ cues; time is shared by character count."""

from __future__ import annotations

import math
import re
import textwrap
from dataclasses import dataclass

MAX_CUE_CHARS = 84    # two lines of 42, the common broadcast limit
LINE_CHARS = 42
BALANCE_SLACK = 8   # extra chars per balanced cue so word ends fit
# Split after sentence punctuation, keeping a closing quote: '"Go!" she said'.
_SENTENCE_END = re.compile(r"(?:(?<=[.!?…])|(?<=[.!?…][\"'”’)]))\s+")


@dataclass(frozen=True)
class Cue:
    start_s: float
    end_s: float
    text: str


def job_cues(text: str, start_s: float, end_s: float) -> list[Cue]:
    """Split one job's text into readable cues across [start_s, end_s]."""
    chunks = _chunks(text)
    total = sum(len(c) for c in chunks) or 1
    span = end_s - start_s

    cues: list[Cue] = []
    cursor = start_s
    for chunk in chunks:
        length = span * len(chunk) / total
        cues.append(Cue(cursor, cursor + length, chunk))
        cursor += length
    return cues


def render_srt(cues: list[Cue]) -> str:
    blocks = []
    for number, cue in enumerate(cues, start=1):
        lines = "\n".join(textwrap.wrap(cue.text, LINE_CHARS)) or cue.text
        blocks.append(f"{number}\n{_stamp(cue.start_s)} --> {_stamp(cue.end_s)}\n{lines}\n")
    return "\n".join(blocks)


def _chunks(text: str) -> list[str]:
    chunks: list[str] = []
    for sentence in (s for s in _SENTENCE_END.split(text) if s.strip()):
        if len(sentence) <= MAX_CUE_CHARS:
            chunks.append(sentence.strip())
            continue
        # Balanced wrap: 130 chars -> two cues of ~65, not 84 + 46.
        count = math.ceil(len(sentence) / MAX_CUE_CHARS)
        chunks += textwrap.wrap(sentence, min(MAX_CUE_CHARS, math.ceil(len(sentence) / count) + BALANCE_SLACK))

    # Re-join short neighbours: "Yes." + "He left." -> "Yes. He left."
    packed: list[str] = []
    for chunk in chunks:
        if packed and len(packed[-1]) + 1 + len(chunk) <= MAX_CUE_CHARS:
            packed[-1] = f"{packed[-1]} {chunk}"
            continue
        packed.append(chunk)
    return packed or [text]


def _stamp(seconds: float) -> str:
    """3725.5 -> '01:02:05,500'."""
    ms = max(0, round(seconds * 1000))
    hours, ms = divmod(ms, 3_600_000)
    minutes, ms = divmod(ms, 60_000)
    secs, ms = divmod(ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"
