"""Plain-text reader: encoding fallback, Gutenberg boilerplate, unwrapped lines."""

from __future__ import annotations

import re
from pathlib import Path

from storyaudio.book import Book
from storyaudio.ingest.text import clean, is_heading, is_scene_break, split_chapters

_ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")
_GUTENBERG_START = re.compile(r"^\*{3}\s*START OF.*?\*{3}.*$", re.IGNORECASE | re.MULTILINE)
_GUTENBERG_END = re.compile(r"^\*{3}\s*END OF.*$", re.IGNORECASE | re.MULTILINE)
_GUTENBERG_TITLE = re.compile(r"^Title:\s*(.+)$", re.MULTILINE)
_GUTENBERG_AUTHOR = re.compile(r"^Author:\s*(.+)$", re.MULTILINE)
_ITALIC = re.compile(r"(?<!\w)_(\S(?:[^_]*\S)?)_(?!\w)")   # _the_ -> the


def read_txt(path: Path) -> Book:
    raw = _decode(path.read_bytes())
    title, author = _gutenberg_meta(raw)
    body = _strip_gutenberg(raw)
    paragraphs = text_paragraphs(body)

    return Book(
        title=title or path.stem.replace("_", " "),
        author=author or "Unknown",
        language="en",
        source=path.name,
        chapters=split_chapters(paragraphs),
    )


def text_paragraphs(body: str) -> list[str]:
    """Split on blank lines and unwrap hard-wrapped lines.

    A file with no blank lines at all is read as one paragraph per line.
    """
    body = body.replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n", body)
    if len(blocks) == 1:
        blocks = body.split("\n")

    paragraphs: list[str] = []
    for block in blocks:
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        paragraphs += _unwrap(lines)
    return paragraphs


def _unwrap(lines: list[str]) -> list[str]:
    """Join wrapped lines, keeping headings and scene breaks on their own.

    Only a block's first line can be a heading, so a wrapped body line that
    starts with "Chapter 3 of the report..." stays body text.
    """
    # A block of nothing but headings is a table of contents: one per line,
    # so each becomes an empty chapter that split_chapters() drops.
    if len(lines) > 1 and all(is_heading(line) for line in lines):
        return [clean(line) for line in lines]

    result: list[str] = []
    current: list[str] = []

    for position, line in enumerate(lines):
        if (position == 0 and is_heading(line)) or is_scene_break(line):
            if current:
                result.append(_join(current))
                current = []
            result.append(clean(line))
            continue
        current.append(line)

    if current:
        result.append(_join(current))
    return result


def _join(lines: list[str]) -> str:
    """Join wrapped lines and drop _italic_ markers.

    Plain-text books rarely hyphenate for layout, so a line-end hyphen is a
    real one: 'rose-' + 'tree' -> 'rose-tree'.
    """
    text = ""
    for line in lines:
        if text.endswith("-") and line[:1].islower():
            text += line
            continue
        text = f"{text} {line}" if text else line
    return clean(_ITALIC.sub(r"\1", text))


def _decode(data: bytes) -> str:
    for encoding in _ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _strip_gutenberg(text: str) -> str:
    start = _GUTENBERG_START.search(text)
    if start:
        text = text[start.end():]
    end = _GUTENBERG_END.search(text)
    if end:
        text = text[: end.start()]
    return text


def _gutenberg_meta(text: str) -> tuple[str, str]:
    head = text[:5000]
    title = _GUTENBERG_TITLE.search(head)
    author = _GUTENBERG_AUTHOR.search(head)
    return (clean(title.group(1)) if title else "", clean(author.group(1)) if author else "")
