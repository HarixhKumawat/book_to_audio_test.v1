"""Shared fixtures: tiny books in every format and a mock Drama 3 server."""

from __future__ import annotations

from pathlib import Path

import pytest
from ebooklib import epub

from storyaudio.tts.mock_server import MockDrama3

LOREM = (
    "The rain had not stopped for three days, and the old house groaned under it. "
    "Peggy listened to the pipes and counted the seconds between each knock."
)


@pytest.fixture
def mock_server():
    server = MockDrama3().start()
    yield server
    server.stop()


@pytest.fixture
def epub_book(tmp_path: Path) -> Path:
    return make_epub(tmp_path / "Tiny Book.epub")


def make_epub(path: Path) -> Path:
    book = epub.EpubBook()
    book.set_identifier("tiny-1")
    book.set_title("Tiny Book")
    book.set_language("en")
    book.add_author("A. Writer")

    chapters = [
        ("Prologue", f"<p>{LOREM}</p><hr/><p>Later, the storm broke.</p>"),
        ("The Visit", '<p>"Morning, Peggy," said Oliver with a smile.</p><p>"Morning," she answered.</p><p>She sighed.</p>'),
    ]
    items = []
    for index, (title, body) in enumerate(chapters, start=1):
        item = epub.EpubHtml(title=title, file_name=f"ch{index}.xhtml", lang="en")
        item.content = f"<html><body><h1>{title}</h1>{body}</body></html>"
        book.add_item(item)
        items.append(item)

    book.toc = items
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", *items]
    epub.write_epub(str(path), book)
    return path


def make_pdf(path: Path, pages: list[list[str]]) -> Path:
    """Write a minimal valid PDF, one Helvetica text line per list entry."""
    objects: list[bytes] = []
    page_ids = [4 + 2 * i for i in range(len(pages))]

    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    for index, lines in enumerate(pages):
        content_id = page_ids[index] + 1
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>".encode()
        )
        ops = ["BT", "/F1 11 Tf", "14 TL", "72 740 Td"]
        for line in lines:
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops.append(f"({escaped}) Tj T*")
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1")
        objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))
    return path
