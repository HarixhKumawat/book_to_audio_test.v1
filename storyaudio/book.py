"""Domain types for an extracted book: plain text, split into chapters."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Chapter:
    title: str
    paragraphs: list[str]


@dataclass
class Book:
    title: str
    author: str
    language: str
    source: str
    chapters: list[Chapter] = field(default_factory=list)

    @property
    def slug(self) -> str:
        return slugify(self.title)

    def word_count(self) -> int:
        return sum(len(p.split()) for c in self.chapters for p in c.paragraphs)

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=1, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def load(path: Path) -> Book:
        raw = json.loads(path.read_text(encoding="utf-8"))
        chapters = [Chapter(**c) for c in raw.pop("chapters")]
        return Book(**raw, chapters=chapters)

    def to_markdown(self) -> str:
        """Readable dump of the extracted text, one '#' heading per chapter."""
        lines = [f"<!-- {self.title} | {self.author} | {self.word_count()} words -->", ""]
        for index, chapter in enumerate(self.chapters, start=1):
            lines += [f"# {index:02d}. {chapter.title}", ""]
            for paragraph in chapter.paragraphs:
                lines += [paragraph, ""]
        return "\n".join(lines)


def slugify(text: str, max_len: int = 60) -> str:
    """'The Mysteries of Bran House || Book 1' -> 'the-mysteries-of-bran-house-book-1'."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug[:max_len].rstrip("-") or "book"
