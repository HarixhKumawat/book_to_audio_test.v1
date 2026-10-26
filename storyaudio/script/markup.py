"""Voice direction tags (MVP_DOCS/voice_diretion_trigger_instructions.md).

    [laughs]                 event   - fixed vocabulary, EVENTS
    <whisper>a few</whisper> span    - fixed vocabulary, SPANS, 1-5 words
    [icy calm]               mood    - any other [bracket] text
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from storyaudio.config import TagPolicy

EVENTS = frozenset({"laughs", "chuckles", "sighs", "gasps", "pause", "clears throat", "hesitates"})
SPANS = frozenset({"whisper", "slow", "emphasis"})
MAX_SPAN_WORDS = 5

BRACKET = re.compile(r"\[([^\[\]]+)\]")
SPAN_TAG = re.compile(r"</?([a-zA-Z][a-zA-Z ]*)>")
LEADING_MOOD = re.compile(r"^\s*\[([^\[\]]+)\]\s*")

# Sentence end: punctuation, optional closing quote/bracket, then space.
# Lookbehinds keep the quote with its sentence: '"Go!" she said' splits after '"'.
SENTENCE_END = re.compile(r"(?:(?<=[.!?…])|(?<=[.!?…][\"'”’)\]]))\s+")
_CLAUSE_END = re.compile(r"(?<=[,;:—–])\s+")
_WORD = re.compile(r"[\w']+")


class TagKind(Enum):
    EVENT = "event"
    MOOD = "mood"


@dataclass(frozen=True)
class Bracket:
    kind: TagKind
    text: str
    start: int


def brackets(text: str) -> list[Bracket]:
    found = []
    for match in BRACKET.finditer(text):
        label = match.group(1).strip()
        kind = TagKind.EVENT if label.lower() in EVENTS else TagKind.MOOD
        found.append(Bracket(kind, label, match.start()))
    return found


def leading_mood(text: str) -> str | None:
    """'[icy calm] Go.' -> 'icy calm'; '[sighs] Go.' -> None."""
    match = LEADING_MOOD.match(text)
    if match is None or match.group(1).strip().lower() in EVENTS:
        return None
    return match.group(1).strip()


def last_mood(text: str) -> str | None:
    moods = [b.text for b in brackets(text) if b.kind is TagKind.MOOD]
    return moods[-1] if moods else None


def spoken(text: str) -> str:
    """Text with every tag removed, for subtitles and word checks."""
    text = BRACKET.sub(" ", text)
    text = SPAN_TAG.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def for_tts(text: str, policy: TagPolicy) -> str:
    """Text as sent to Drama 3 under the chosen tag policy."""
    if policy is TagPolicy.KEEP:
        return text

    if policy is TagPolicy.EVENTS_ONLY:
        kept = BRACKET.sub(lambda m: m.group(0) if m.group(1).strip().lower() in EVENTS else " ", text)
        return re.sub(r"\s+", " ", SPAN_TAG.sub("", kept)).strip()

    return spoken(text)


def words(text: str) -> list[str]:
    """Normalized spoken words: 'Don’t—STOP!' -> ["don't", 'stop']."""
    plain = spoken(text).lower().replace("’", "'").replace("‘", "'")
    return [w.strip("'") for w in _WORD.findall(plain) if w.strip("'")]


def split_text(text: str, max_chars: int) -> list[str]:
    """Split on sentence ends (then clauses, then spaces) into <= max_chars pieces.

    Never splits inside a <span>...</span>.
    Example (max 20): 'One two. Three four five.' -> ['One two.', 'Three four five.']
    """
    if len(text) <= max_chars:
        return [text]

    pieces: list[str] = []
    for sentence in _split_at(text, SENTENCE_END):
        if len(sentence) <= max_chars:
            pieces.append(sentence)
            continue
        for clause in _split_at(sentence, _CLAUSE_END):
            pieces += [clause] if len(clause) <= max_chars else _hard_split(clause, max_chars)

    return _pack(pieces, max_chars)


def _split_at(text: str, pattern: re.Pattern) -> list[str]:
    parts: list[str] = []
    last = 0
    for match in pattern.finditer(text):
        if _inside_span(text, match.start()):
            continue
        parts.append(text[last:match.start()].strip())
        last = match.end()
    parts.append(text[last:].strip())
    return [p for p in parts if p]


def _inside_span(text: str, index: int) -> bool:
    opened = 0
    for match in SPAN_TAG.finditer(text, 0, index):
        opened += -1 if match.group(0).startswith("</") else 1
    return opened > 0


def _hard_split(text: str, max_chars: int) -> list[str]:
    pieces: list[str] = []
    current = ""
    for word in text.split():
        if current and len(current) + 1 + len(word) > max_chars and not _inside_span(current + " ", len(current)):
            pieces.append(current)
            current = word
            continue
        current = f"{current} {word}" if current else word
    if current:
        pieces.append(current)
    return pieces


def _pack(pieces: list[str], max_chars: int) -> list[str]:
    """Greedily re-join short pieces so requests are not tiny."""
    packed: list[str] = []
    for piece in pieces:
        if packed and len(packed[-1]) + 1 + len(piece) <= max_chars:
            packed[-1] = f"{packed[-1]} {piece}"
            continue
        packed.append(piece)
    return packed
