"""Baseline script from a Book: every word kept, dialogue split out when the
speaker is named in the same paragraph ("...," said Peggy).

The draft is runnable as is (unattributed speech stays with NARRATOR).
The agent then improves cast, attribution and direction tags.
"""

from __future__ import annotations

import re

from storyaudio.book import Book
from storyaudio.ingest.text import SCENE_BREAK
from storyaudio.script.model import NARRATOR, CastMember, Kind, Mode, Script, ScriptChapter, Segment

NARRATOR_VOICE = "warm, clear audiobook narrator; measured pace; gentle and expressive"
TODO_VOICE = "TODO: age, gender, accent, texture, temperament"

_VERBS = (
    "said|asked|replied|answered|whispered|shouted|cried|called|muttered|murmured|"
    "added|snapped|yelled|exclaimed|continued|insisted|admitted|greeted|told|began|"
    "repeated|demanded|hissed|sighed|laughed|growled|groaned|breathed|offered|agreed"
)
_HONORIFICS = r"(?:Mr|Mrs|Ms|Miss|Dr|Sister|Brother|Father|Mother|Reverend|Lady|Lord|Sir|Madam|Captain|Professor|Aunt|Uncle)\.?"
_NAME = rf"((?:{_HONORIFICS}\s+)?[A-Z][a-z]+(?:\s+[A-Z][a-z]+){{0,2}})"
_NOT_NAMES = frozenset({"He", "She", "They", "I", "We", "You", "It", "The", "A", "An", "His", "Her", "Their", "Then", "And", "But"})

# "said Peggy" / "Peggy said" / "the Reverend Oliver Holyoake greeted".
_SAID_NAME = re.compile(rf"\b(?:{_VERBS})\s+(?:the\s+)?{_NAME}")
_NAME_SAID = re.compile(rf"(?:\bthe\s+)?{_NAME}\s+(?:{_VERBS})\b")

# Paired quotes: curly “...” or straight "...".
_QUOTE = re.compile(r"“[^”]*”|\"[^\"]*\"")


def draft_script(book: Book) -> Script:
    cast: dict[str, CastMember] = {NARRATOR: CastMember(NARRATOR, "Narrator", NARRATOR_VOICE)}
    chapters = [_draft_chapter(index, chapter.title, chapter.paragraphs, cast) for index, chapter in enumerate(book.chapters, start=1)]
    return Script(title=book.title, author=book.author, mode=Mode.FAITHFUL, cast=list(cast.values()), chapters=chapters)


def _draft_chapter(number: int, title: str, paragraphs: list[str], cast: dict[str, CastMember]) -> ScriptChapter:
    chapter = ScriptChapter(number=number, title=title, segments=[Segment(Kind.TITLE, NARRATOR, title)])

    for paragraph in paragraphs:
        if paragraph == SCENE_BREAK:
            chapter.segments.append(Segment(Kind.SCENE))
            continue
        chapter.segments += _split_paragraph(paragraph, cast)

    return chapter


def _split_paragraph(paragraph: str, cast: dict[str, CastMember]) -> list[Segment]:
    """Narration and quotes as separate segments when a speaker is named."""
    speaker = _speaker(paragraph)
    if speaker is None:
        return [Segment(Kind.LINE, NARRATOR, paragraph)]

    speaker_id, name = speaker
    cast.setdefault(speaker_id, CastMember(speaker_id, name, TODO_VOICE))

    segments: list[Segment] = []
    last = 0
    for match in _QUOTE.finditer(paragraph):
        _append(segments, NARRATOR, paragraph[last:match.start()])
        _append(segments, speaker_id, match.group(0))
        last = match.end()
    _append(segments, NARRATOR, paragraph[last:])
    return segments


def _append(segments: list[Segment], speaker: str, text: str) -> None:
    text = text.strip()
    if not text:
        return
    # Lone punctuation between quotes ('"Hi" , "Bye"') joins the previous segment.
    if not re.search(r"\w", text) and segments:
        segments[-1].text = f"{segments[-1].text} {text}".strip()
        return
    segments.append(Segment(Kind.LINE, speaker, text))


def _speaker(paragraph: str) -> tuple[str, str] | None:
    """(ID, name) of the one named speaker in a paragraph with dialogue, else None."""
    if not _QUOTE.search(paragraph):
        return None

    # Look only at narration, never inside the quotes.
    narration = _QUOTE.sub(" | ", paragraph)
    for pattern in (_SAID_NAME, _NAME_SAID):
        for match in pattern.finditer(narration):
            name = match.group(1)
            if name.split()[0] in _NOT_NAMES:
                continue
            return _speaker_id(name), name
    return None


def _speaker_id(name: str) -> str:
    """'Reverend Oliver Holyoake' -> 'OLIVER_HOLYOAKE'; 'Dr. Carter' -> 'DR_CARTER'."""
    parts = re.sub(r"[^A-Za-z ]", "", name).split()
    if len(parts) > 1 and re.fullmatch(_HONORIFICS, parts[0]) and parts[0] not in {"Dr", "Mr", "Mrs", "Ms", "Miss"}:
        parts = parts[1:]
    return "_".join(parts).upper()[:32]
