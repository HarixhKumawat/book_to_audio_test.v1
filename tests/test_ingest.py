from pathlib import Path

import pytest

from storyaudio.ingest import UnsupportedFormatError, load_book
from storyaudio.ingest.pdf import PdfNoTextError
from storyaudio.ingest.text import SCENE_BREAK, clean, split_chapters
from tests.conftest import make_pdf

# ---------------------------------------------------------------- epub


def test_epub_reads_metadata_and_chapters(epub_book: Path):
    book = load_book(epub_book)

    assert book.title == "Tiny Book"
    assert book.author == "A. Writer"
    assert [c.title for c in book.chapters] == ["Prologue", "The Visit"]


def test_epub_skips_heading_and_keeps_scene_break(epub_book: Path):
    prologue = load_book(epub_book).chapters[0]

    assert prologue.paragraphs[0].startswith("The rain had not stopped")
    assert prologue.paragraphs[1] == SCENE_BREAK
    assert prologue.paragraphs[2] == "Later, the storm broke."
    assert "Prologue" not in prologue.paragraphs


def test_epub_sample_book_if_present():
    sample = next(Path("books").glob("*.epub"), None)
    if sample is None:
        pytest.skip("no sample book in books/")

    book = load_book(sample)
    assert len(book.chapters) >= 2
    assert book.word_count() > 1000


# ---------------------------------------------------------------- txt


def test_txt_strips_gutenberg_and_splits_chapters(tmp_path: Path):
    text = (
        "Title: Storm House\nAuthor: Jane Roe\n\n"
        "*** START OF THE PROJECT GUTENBERG EBOOK STORM HOUSE ***\n\n"
        "CHAPTER I\n\nThe Arrival\n\n"
        "It was a dark and stormy night, and the wind\nhowled through the trees.\n\n"
        "* * *\n\n"
        "Morning came at last.\n\n"
        "CHAPTER II\n\nThe rose-\ntree was _very_ red.\n\n"
        "*** END OF THE PROJECT GUTENBERG EBOOK ***\nlicense text here\n"
    )
    path = tmp_path / "storm.txt"
    path.write_text(text, encoding="utf-8")

    book = load_book(path)

    assert (book.title, book.author) == ("Storm House", "Jane Roe")
    assert [c.title for c in book.chapters] == ["CHAPTER I: The Arrival", "CHAPTER II"]
    assert book.chapters[0].paragraphs == [
        "It was a dark and stormy night, and the wind howled through the trees.",
        SCENE_BREAK,
        "Morning came at last.",
    ]
    assert book.chapters[1].paragraphs == ["The rose-tree was very red."]


def test_txt_table_of_contents_is_dropped(tmp_path: Path):
    path = tmp_path / "toc.txt"
    path.write_text("Contents\n\nChapter 1. Start\nChapter 2. End\n\nChapter 1. Start\n\nIt began.\n\nChapter 2. End\n\nIt ended.\n", encoding="utf-8")

    book = load_book(path)

    assert [(c.title, c.paragraphs) for c in book.chapters] == [("Chapter 1. Start", ["It began."]), ("Chapter 2. End", ["It ended."])]


def test_txt_cp1252_fallback(tmp_path: Path):
    path = tmp_path / "old.txt"
    path.write_bytes("Caf\xe9 society. \x93Quoted.\x94".encode("latin-1"))

    paragraph = load_book(path).chapters[0].paragraphs[0]

    assert paragraph == "Café society. “Quoted.”"


def test_txt_without_headings_becomes_sections(tmp_path: Path):
    path = tmp_path / "plain.txt"
    path.write_text("One paragraph.\n\nAnother paragraph.", encoding="utf-8")

    book = load_book(path)

    assert [c.title for c in book.chapters] == ["Section 1"]
    assert book.chapters[0].paragraphs == ["One paragraph.", "Another paragraph."]


# ---------------------------------------------------------------- pdf


def test_pdf_drops_headers_page_numbers_and_finds_chapters(tmp_path: Path):
    body = "The house stood alone on the hill and nobody had lived there for years and years."
    pages = []
    for number in range(1, 6):
        lines = ["THE HOUSE ON THE HILL"]
        if number == 1:
            lines += ["Chapter 1", "", body, "It was quiet."]
        elif number == 3:
            lines += ["Chapter 2", "", body, "Then the lights came on."]
        else:
            lines += [body, "Nobody came."]
        lines += [str(number)]
        pages.append(lines)
    path = make_pdf(tmp_path / "hill.pdf", pages)

    book = load_book(path)
    text = " ".join(p for c in book.chapters for p in c.paragraphs)

    assert [c.title for c in book.chapters] == ["Chapter 1", "Chapter 2"]
    assert "THE HOUSE ON THE HILL" not in text
    assert not any(p.strip().isdigit() for c in book.chapters for p in c.paragraphs)
    assert "Then the lights came on." in text


def test_pdf_without_text_layer_fails_clearly(tmp_path: Path):
    path = make_pdf(tmp_path / "scan.pdf", [[]])

    with pytest.raises(PdfNoTextError):
        load_book(path)


# ---------------------------------------------------------------- shared


def test_unsupported_extension(tmp_path: Path):
    path = tmp_path / "book.docx"
    path.write_bytes(b"x")

    with pytest.raises(UnsupportedFormatError):
        load_book(path)


def test_clean_normalizes_whitespace():
    assert clean("a\u00a0 b\n\tc\u00adx") == "a b cx"


def test_split_chapters_keeps_long_front_matter():
    front = " ".join(["word"] * 60)
    chapters = split_chapters([front, "Chapter 1", "Text.", "Chapter 2", "More."])

    assert [c.title for c in chapters] == ["Opening", "Chapter 1", "Chapter 2"]
