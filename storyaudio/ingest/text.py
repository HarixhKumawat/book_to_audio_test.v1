"""Shared text cleanup and chapter detection for all book formats."""

from __future__ import annotations

import re
import unicodedata

from storyaudio.book import Chapter

SCENE_BREAK = "* * *"
FRONT_MATTER_TITLE = "Opening"
SECTION_TITLE = "Section {n}"
MIN_FRONT_MATTER_WORDS = 50   # shorter text before chapter 1 is title-page noise
SECTION_WORDS = 5000          # chapterless books are cut into sections this size
MAX_HEADING_CHARS = 80
MAX_HEADING_WORDS = 12
MAX_SUBTITLE_CHARS = 60
_QUOTES = ('"', "“", "”", "'", "‘", "’")

_NUMBER_WORDS = (
    "one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|"
    "fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty"
)

# "Chapter 12", "CHAPTER XII. The Storm", "Part Two", "Prologue", "Epilogue: After".
_HEADING = re.compile(
    rf"^(?:(?:chapter|book|part)\s+(?:\d+|[ivxlcdm]+|(?:{_NUMBER_WORDS})(?:[- ](?:{_NUMBER_WORDS}))?)\b"
    r"|(?:prologue|epilogue|preface|introduction|interlude|afterword|foreword)\b)",
    re.IGNORECASE,
)

# "IV. THE BOSCOMBE VALLEY MYSTERY", "12. THE END": numbered all-caps titles.
_NUMBERED_CAPS = re.compile(r"^(?:[IVXLC]+|\d{1,3})\.\s+(?=.*[A-Z]{2})[A-Z0-9][A-Z0-9 ’'\",.:;!?&-]*$")

# "***", "* * *", "#", "~~~", "-----", "§".
_SCENE = re.compile(r"^\s*(?:[*#~•·§-]\s*){1,7}$")


def clean(text: str) -> str:
    """Normalize unicode and collapse whitespace to single spaces."""
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\u00ad", "")  # soft hyphen
    text = re.sub(r"[\u00a0\u2000-\u200b\u202f\u205f\u3000]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def is_scene_break(paragraph: str) -> bool:
    return bool(_SCENE.match(paragraph))


def is_heading(paragraph: str) -> bool:
    """Short, capitalized, chapter-like line. 'introduction of his name' is not."""
    if len(paragraph) > MAX_HEADING_CHARS or not paragraph[:1].isupper():
        return False
    if len(paragraph.split()) > MAX_HEADING_WORDS or paragraph.endswith((",", ";")):
        return False
    return bool(_HEADING.match(paragraph) or _NUMBERED_CAPS.match(paragraph))


def split_chapters(paragraphs: list[str]) -> list[Chapter]:
    """Cut a flat paragraph list into chapters at heading lines.

    Example: ["Chapter 1", "The Storm", "It rained."] ->
             [Chapter("Chapter 1: The Storm", ["It rained."])]
    """
    paragraphs = [_normalize_break(p) for p in paragraphs if p]
    chapters: list[Chapter] = []
    current = Chapter(FRONT_MATTER_TITLE, [])

    index = 0
    while index < len(paragraphs):
        paragraph = paragraphs[index]
        index += 1

        if not is_heading(paragraph):
            current.paragraphs.append(paragraph)
            continue

        # A bare "Chapter 3" followed by a short title line merges with it.
        title = paragraph.rstrip(".:")
        if _is_bare(title) and index < len(paragraphs) and _is_subtitle(paragraphs[index]):
            title = f"{title}: {paragraphs[index].rstrip('.')}"
            index += 1

        chapters.append(current)
        current = Chapter(title, [])

    chapters.append(current)
    chapters = [c for c in chapters if _keep(c)]
    return chapters if len(chapters) > 1 else _sections(paragraphs)


def _normalize_break(paragraph: str) -> str:
    return SCENE_BREAK if is_scene_break(paragraph) else paragraph


def _is_bare(title: str) -> bool:
    return len(title.split()) <= 2


def _is_subtitle(paragraph: str) -> bool:
    """'Who Stole the Tarts?' yes; '"Help!"' (dialogue) and 'He ran.' no."""
    if len(paragraph) > MAX_SUBTITLE_CHARS or paragraph.startswith(_QUOTES) or is_heading(paragraph):
        return False
    return not paragraph.endswith((".", *_QUOTES))


def _keep(chapter: Chapter) -> bool:
    words = sum(len(p.split()) for p in chapter.paragraphs if p != SCENE_BREAK)
    if chapter.title == FRONT_MATTER_TITLE:
        return words >= MIN_FRONT_MATTER_WORDS
    return words > 0


def _sections(paragraphs: list[str]) -> list[Chapter]:
    """No headings found: cut into ~SECTION_WORDS chunks on paragraph ends."""
    sections: list[Chapter] = []
    current: list[str] = []
    words = 0

    for paragraph in paragraphs:
        current.append(paragraph)
        words += len(paragraph.split())
        if words < SECTION_WORDS:
            continue
        sections.append(Chapter(SECTION_TITLE.format(n=len(sections) + 1), current))
        current, words = [], 0

    if current:
        sections.append(Chapter(SECTION_TITLE.format(n=len(sections) + 1), current))

    if len(sections) == 1:
        sections[0].title = SECTION_TITLE.format(n=1)
    return sections
