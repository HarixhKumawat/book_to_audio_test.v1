"""Turn a Script into an ordered list of TTS jobs.

Each Drama 3 request is independent, so the planner:
  - splits long segments on sentence ends (server prompt/latency limits),
  - re-sends the speaker's active mood on every piece ("a mood stays active
    until the next one"),
  - records the boundary before each job so the assembler picks the gap.

    Segment  PEGGY: [bright] "A. B. C."   (max_chars small)
      -> Job PEGGY "[bright] "A. B."      boundary=SPEAKER
      -> Job PEGGY "[bright] C.""         boundary=PIECE
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from storyaudio.config import TagPolicy
from storyaudio.script import markup
from storyaudio.script.model import NARRATOR, Kind, Script


class Boundary(Enum):
    """What separates a job from the one before it."""

    START = "start"
    PIECE = "piece"       # same segment, split for length
    SEGMENT = "segment"   # next segment, same speaker
    SPEAKER = "speaker"   # speaker changes
    SCENE = "scene"
    CHAPTER = "chapter"


@dataclass(frozen=True)
class Job:
    index: int
    chapter: int          # position in script.chapters, 0-based
    chapter_title: str
    speaker: str
    tts_text: str         # sent to Drama 3
    subtitle: str         # shown in .srt
    boundary: Boundary


def plan_jobs(script: Script, max_chars: int, policy: TagPolicy) -> list[Job]:
    jobs: list[Job] = []

    for chapter_index, chapter in enumerate(script.chapters):
        if chapter.skipped:
            continue

        moods: dict[str, str] = {}
        boundary = Boundary.START if not jobs else Boundary.CHAPTER
        previous: str | None = None

        for segment in chapter.segments:
            if segment.kind is Kind.SCENE:
                if boundary not in (Boundary.START, Boundary.CHAPTER):
                    boundary = Boundary.SCENE
                continue

            speaker = NARRATOR if segment.kind is Kind.TITLE else segment.speaker
            if boundary not in (Boundary.START, Boundary.CHAPTER, Boundary.SCENE):
                boundary = Boundary.SEGMENT if speaker == previous else Boundary.SPEAKER

            for piece in markup.split_text(segment.text, max_chars):
                subtitle = markup.spoken(piece)
                if not subtitle:
                    continue

                text = _with_mood(piece, moods.get(speaker), segment.kind)
                jobs.append(Job(len(jobs), chapter_index, chapter.title, speaker, markup.for_tts(text, policy), subtitle, boundary))
                boundary = Boundary.PIECE

                # The last mood in this piece carries into the speaker's next line.
                mood = markup.last_mood(piece)
                if mood is not None:
                    moods[speaker] = mood

            previous = speaker

    return jobs


def _with_mood(piece: str, active: str | None, kind: Kind) -> str:
    """Prefix the active mood unless the piece opens with its own (titles stay plain)."""
    if kind is Kind.TITLE or active is None or markup.leading_mood(piece) is not None:
        return piece
    return f"[{active}] {piece}"
