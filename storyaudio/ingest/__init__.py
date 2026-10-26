"""Book readers. load_book() picks the reader from the file extension."""

from __future__ import annotations

from pathlib import Path

from storyaudio.book import Book
from storyaudio.ingest.epub import read_epub
from storyaudio.ingest.pdf import read_pdf
from storyaudio.ingest.txt import read_txt

_READERS = {
    ".epub": read_epub,
    ".pdf": read_pdf,
    ".txt": read_txt,
    ".text": read_txt,
    ".md": read_txt,
}

SUPPORTED = tuple(_READERS)


class UnsupportedFormatError(ValueError):
    pass


def load_book(path: Path) -> Book:
    reader = _READERS.get(path.suffix.lower())
    if reader is None:
        raise UnsupportedFormatError(f"{path.name}: use one of {', '.join(SUPPORTED)}")

    book = reader(path)
    if not book.chapters:
        raise ValueError(f"{path.name}: no readable text found")
    return book
