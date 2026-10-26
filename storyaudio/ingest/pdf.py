"""PDF reader: text layer only (no OCR), header/footer removal, line unwrap.

Chapters come from the PDF outline when it has 2+ top-level entries,
else from heading lines, else fixed-size sections.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from pathlib import Path

from pypdf import PdfReader

from storyaudio.book import Book, Chapter
from storyaudio.ingest.text import clean, is_heading, is_scene_break, split_chapters

_PAGE_NUMBER = re.compile(r"^\s*(?:page\s+)?\d{1,4}(?:\s*(?:of|/)\s*\d{1,4})?\s*$", re.IGNORECASE)
_REPEAT_SHARE = 0.3       # a line on 30%+ of pages is a running header/footer
_MIN_PAGES_FOR_REPEAT = 4
_SHORT_LINE = 0.75        # line shorter than 75% of median width may end a paragraph
_END_PUNCT = (".", "!", "?", '"', "”", "’", "'", ":", "…")


class PdfNoTextError(ValueError):
    """The PDF has no text layer (scanned images). OCR it first."""


def read_pdf(path: Path) -> Book:
    reader = PdfReader(str(path))
    pages = [_page_lines(page.extract_text() or "") for page in reader.pages]
    if not any(line for lines in pages for line in lines):
        raise PdfNoTextError(f"{path.name}: no extractable text; run OCR first")

    pages = _drop_running_lines(pages)
    meta = reader.metadata or {}
    chapters = _outline_chapters(reader, pages) or split_chapters(_paragraphs(_flatten(pages)))

    return Book(
        title=clean(str(meta.get("/Title") or "")) or path.stem.replace("_", " "),
        author=clean(str(meta.get("/Author") or "")) or "Unknown",
        language="en",
        source=path.name,
        chapters=chapters,
    )


def _page_lines(text: str) -> list[str]:
    return [line.strip() for line in text.replace("\r", "\n").split("\n")]


def _drop_running_lines(pages: list[list[str]]) -> list[list[str]]:
    """Remove page numbers and lines repeated on many pages (headers/footers)."""
    counts: Counter[str] = Counter()
    for lines in pages:
        counts.update({_digitless(line) for line in lines if line})

    repeated: set[str] = set()
    if len(pages) >= _MIN_PAGES_FOR_REPEAT:
        limit = max(2, int(len(pages) * _REPEAT_SHARE))
        repeated = {line for line, n in counts.items() if n >= limit}

    # Headings repeat in digitless form ("Chapter #") but are never headers.
    return [
        [line for line in lines if not _PAGE_NUMBER.match(line) and (is_heading(line) or _digitless(line) not in repeated)]
        for lines in pages
    ]


def _digitless(line: str) -> str:
    """'Chapter Title — 12' and 'Chapter Title — 13' count as one header."""
    return re.sub(r"\d+", "#", line)


def _flatten(pages: list[list[str]]) -> list[str]:
    return [line for lines in pages for line in lines]


def _paragraphs(lines: list[str]) -> list[str]:
    """Rebuild paragraphs from visual lines.

    A paragraph ends at a blank line, a heading, a scene break, or a short
    line that ends in sentence punctuation.
    """
    widths = [len(line) for line in lines if len(line) > 20]
    median = statistics.median(widths) if widths else 60

    paragraphs: list[str] = []
    current = ""

    for line in lines:
        if not line or is_heading(line) or is_scene_break(line):
            if current:
                paragraphs.append(clean(current))
                current = ""
            if line:
                paragraphs.append(clean(line))
            continue

        if current.endswith("-") and line[:1].islower():
            current = current[:-1] + line
        else:
            current = f"{current} {line}" if current else line

        if len(line) < median * _SHORT_LINE and line.endswith(_END_PUNCT):
            paragraphs.append(clean(current))
            current = ""

    if current:
        paragraphs.append(clean(current))
    return paragraphs


def _outline_chapters(reader: PdfReader, pages: list[list[str]]) -> list[Chapter] | None:
    """One chapter per top-level outline entry, spanning its pages."""
    try:
        outline = reader.outline
    except Exception:  # malformed outlines are common; fall back to headings
        return None

    starts: list[tuple[int, str]] = []
    for entry in outline:
        if isinstance(entry, list):
            continue  # nested children
        try:
            starts.append((reader.get_destination_page_number(entry), clean(entry.title)))
        except Exception:
            continue

    starts = sorted({page: title for page, title in starts}.items())
    if len(starts) < 2:
        return None

    chapters: list[Chapter] = []
    bounds = [page for page, _ in starts[1:]] + [len(pages)]
    for (first, title), end in zip(starts, bounds):
        paragraphs = _paragraphs(_flatten(pages[first:end]))
        if paragraphs and clean(paragraphs[0]).lower() == title.lower():
            paragraphs = paragraphs[1:]  # heading repeated in body text
        if paragraphs:
            chapters.append(Chapter(title, paragraphs))

    # Text before the first outline entry is front matter; keep if substantial.
    lead = _paragraphs(_flatten(pages[: starts[0][0]]))
    if sum(len(p.split()) for p in lead) >= 50:
        chapters.insert(0, Chapter("Opening", lead))

    return chapters or None
