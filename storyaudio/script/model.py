"""Domain types for a performance script: cast, chapters, segments."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

NARRATOR = "NARRATOR"


class Mode(Enum):
    """FAITHFUL keeps every source word (checked). ADAPTED allows rewrites."""

    FAITHFUL = "faithful"
    ADAPTED = "adapted"


class Kind(Enum):
    TITLE = "title"   # chapter heading, read by the narrator
    LINE = "line"     # one spoken segment
    SCENE = "scene"   # scene break: a longer pause, nothing spoken


@dataclass
class Segment:
    kind: Kind
    speaker: str = NARRATOR
    text: str = ""
    line_no: int = 0


@dataclass
class CastMember:
    id: str
    name: str
    voice: str  # natural-language description, used to seed a voice


@dataclass
class ScriptChapter:
    number: int        # 1-based index into the source book's chapters
    title: str
    segments: list[Segment] = field(default_factory=list)
    path: str = ""
    skipped: bool = False   # file holds <!-- skip -->: not narrated (TOC, copyright)


@dataclass
class Script:
    title: str
    author: str
    mode: Mode
    cast: list[CastMember]
    chapters: list[ScriptChapter] = field(default_factory=list)

    def member(self, speaker_id: str) -> CastMember | None:
        return next((m for m in self.cast if m.id == speaker_id), None)
