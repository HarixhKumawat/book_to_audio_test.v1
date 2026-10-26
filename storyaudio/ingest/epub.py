"""EPUB reader: spine order, TOC titles, one chapter per content document."""

from __future__ import annotations

import posixpath
import re
import warnings
from pathlib import Path

import ebooklib
from bs4 import BeautifulSoup, Tag, XMLParsedAsHTMLWarning
from ebooklib import epub

from storyaudio.book import Book, Chapter
from storyaudio.ingest.text import SCENE_BREAK, clean, is_scene_break

_BLOCKS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre", "div", "dd", "dt", "figcaption"}
_HEADINGS = ("h1", "h2", "h3")
_SKIP_NAMES = re.compile(r"(cover|toc|contents|copyright|nav|titlepage|colophon)", re.IGNORECASE)
_SKIP_MAX_WORDS = 150  # a "cover"/"toc" document with more words is real text


def read_epub(path: Path) -> Book:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # ebooklib warns about future defaults
        doc = epub.read_epub(str(path), options={"ignore_ncx": False})

    titles = _toc_titles(doc.toc)
    chapters: list[Chapter] = []

    for item_id, _linear in doc.spine:
        item = doc.get_item_with_id(item_id)
        if item is None or item.get_type() != ebooklib.ITEM_DOCUMENT:
            continue
        if isinstance(item, epub.EpubNav):
            continue

        chapter = _read_document(item, titles.get(item.get_name()), len(chapters) + 1)
        if chapter is None:
            continue
        chapters.append(chapter)

    return Book(
        title=_meta(doc, "title") or path.stem,
        author=_meta(doc, "creator") or "Unknown",
        language=_meta(doc, "language") or "en",
        source=path.name,
        chapters=chapters,
    )


def _meta(doc: epub.EpubBook, name: str) -> str:
    values = doc.get_metadata("DC", name)
    if not values:
        return ""
    # "Title || Series: Book 1" is a common packer artefact.
    return clean(values[0][0]).replace(" || ", " - ")


def _toc_titles(toc: list) -> dict[str, str]:
    """Map document href (no #fragment) -> first TOC title that points at it."""
    titles: dict[str, str] = {}
    stack = list(toc)
    while stack:
        entry = stack.pop(0)
        if isinstance(entry, tuple):
            section, children = entry
            stack[0:0] = [section, *children]
            continue
        href = getattr(entry, "href", "") or ""
        title = clean(getattr(entry, "title", "") or "")
        name = posixpath.normpath(href.split("#")[0])
        if title and name not in titles:
            titles[name] = title
    return titles


def _read_document(item: epub.EpubItem, toc_title: str | None, number: int) -> Chapter | None:
    # The HTML parser tolerates broken XHTML, which real EPUBs often contain.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        soup = BeautifulSoup(item.get_content(), "lxml")
    for junk in soup(["script", "style", "nav"]):
        junk.decompose()

    body = soup.body or soup
    heading = body.find(_HEADINGS)
    heading_text = clean(heading.get_text(" ")) if heading else ""
    paragraphs = _paragraphs(body, heading)

    words = sum(len(p.split()) for p in paragraphs)
    if words == 0:
        return None
    if _SKIP_NAMES.search(item.get_name()) and words < _SKIP_MAX_WORDS:
        return None

    title = toc_title or heading_text or f"Chapter {number}"
    return Chapter(title=title, paragraphs=paragraphs)


def _paragraphs(body: Tag, heading: Tag | None) -> list[str]:
    """Text of every leaf block in document order; <hr> becomes a scene break."""
    paragraphs: list[str] = []

    for node in body.find_all([*_BLOCKS, "hr"]):
        if node is heading:
            continue
        if node.name == "hr":
            paragraphs.append(SCENE_BREAK)
            continue
        if node.find(_BLOCKS):
            continue  # container; its leaf blocks are visited on their own

        text = clean(node.get_text(" "))
        text = re.sub(r"\s+([,.;:!?])", r"\1", text)  # "word ," from inline tags
        if not text:
            continue
        paragraphs.append(SCENE_BREAK if is_scene_break(text) else text)

    return _trim_breaks(paragraphs)


def _trim_breaks(paragraphs: list[str]) -> list[str]:
    """Drop leading, trailing and repeated scene breaks."""
    result: list[str] = []
    for paragraph in paragraphs:
        if paragraph == SCENE_BREAK and (not result or result[-1] == SCENE_BREAK):
            continue
        result.append(paragraph)
    while result and result[-1] == SCENE_BREAK:
        result.pop()
    return result
