"""Script checks run before any audio is made. ERROR blocks rendering."""

from __future__ import annotations

import difflib
import re
from collections import Counter
from dataclasses import dataclass
from enum import Enum

from storyaudio.book import Book
from storyaudio.ingest.text import SCENE_BREAK
from storyaudio.script import markup
from storyaudio.script.drafter import TODO_VOICE
from storyaudio.script.model import Kind, Mode, Script, ScriptChapter, Segment

MAX_EVENTS = 2
MAX_SPANS = 2
MAX_DIFFS_SHOWN = 8
DIFF_CONTEXT_WORDS = 4
_STRUCTURAL = re.compile(r"<\|")


class Severity(Enum):
    ERROR = "error"
    WARN = "warn"


@dataclass(frozen=True)
class Issue:
    severity: Severity
    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.severity.value.upper():5} {self.where}: {self.message}"


def check_script(script: Script, book: Book | None) -> list[Issue]:
    issues: list[Issue] = []
    issues += _check_cast(script)
    issues += _check_chapter_set(script, book)

    for chapter in script.chapters:
        if chapter.skipped:
            issues.append(Issue(Severity.WARN, chapter.path or f"chapter {chapter.number:02d}", "skipped; not narrated"))
            continue

        for segment in chapter.segments:
            issues += _check_segment(script, chapter, segment)

        source = _source_chapter(book, chapter.number)
        if script.mode is Mode.FAITHFUL and source is not None:
            issues += _check_words(chapter, source)

    return issues


def has_errors(issues: list[Issue]) -> bool:
    return any(i.severity is Severity.ERROR for i in issues)


# ---------------------------------------------------------------- cast


def _check_cast(script: Script) -> list[Issue]:
    issues: list[Issue] = []
    counts = Counter(m.id for m in script.cast)
    used = {s.speaker for c in script.chapters for s in c.segments if s.kind is Kind.LINE}

    for member_id, count in counts.items():
        if count > 1:
            issues.append(Issue(Severity.ERROR, "cast", f"{member_id} listed {count} times"))

    for member in script.cast:
        if member.id not in used:
            issues.append(Issue(Severity.WARN, "cast", f"{member.id} never speaks"))
        if not member.voice or member.voice == TODO_VOICE:
            issues.append(Issue(Severity.WARN, "cast", f"{member.id} has no voice description"))

    return issues


def _check_chapter_set(script: Script, book: Book | None) -> list[Issue]:
    issues: list[Issue] = []
    numbers = Counter(c.number for c in script.chapters)
    for number, count in numbers.items():
        if count > 1:
            issues.append(Issue(Severity.ERROR, "chapters", f"chapter {number:02d} has {count} files"))

    if not script.chapters:
        issues.append(Issue(Severity.ERROR, "chapters", "no chapter files"))
    if book is None:
        return issues

    total = len(book.chapters)
    for number in numbers:
        if not 1 <= number <= total:
            issues.append(Issue(Severity.ERROR, "chapters", f"chapter {number:02d} not in book (1..{total})"))

    # Adapted scripts may merge or drop chapters; faithful ones may not.
    severity = Severity.ERROR if script.mode is Mode.FAITHFUL else Severity.WARN
    for number in range(1, total + 1):
        if number not in numbers:
            issues.append(Issue(severity, "chapters", f"source chapter {number:02d} has no script file"))

    return issues


# ---------------------------------------------------------------- segments


def _check_segment(script: Script, chapter: ScriptChapter, segment: Segment) -> list[Issue]:
    where = f"{chapter.path or chapter.number}:{segment.line_no}"
    if segment.kind is Kind.SCENE:
        return []

    issues: list[Issue] = []
    if segment.kind is Kind.TITLE and markup.brackets(segment.text):
        issues.append(Issue(Severity.WARN, where, "keep headings plain (no tags)"))

    if segment.kind is Kind.LINE and script.member(segment.speaker) is None:
        issues.append(Issue(Severity.ERROR, where, f"speaker {segment.speaker} not in cast"))

    if not markup.spoken(segment.text):
        issues.append(Issue(Severity.ERROR, where, "segment has no spoken words"))

    if _STRUCTURAL.search(segment.text):
        issues.append(Issue(Severity.ERROR, where, "'<|' tokens are reserved by Drama 3"))

    events = [b for b in markup.brackets(segment.text) if b.kind is markup.TagKind.EVENT]
    if len(events) > MAX_EVENTS:
        issues.append(Issue(Severity.WARN, where, f"{len(events)} event tags (max {MAX_EVENTS})"))

    issues += _check_spans(segment.text, where)
    return issues


def _check_spans(text: str, where: str) -> list[Issue]:
    issues: list[Issue] = []
    open_name: str | None = None
    open_at = 0
    count = 0

    for match in markup.SPAN_TAG.finditer(text):
        name = match.group(1).strip().lower()
        closing = match.group(0).startswith("</")

        if name not in markup.SPANS:
            issues.append(Issue(Severity.ERROR, where, f"unknown span <{name}> (use {', '.join(sorted(markup.SPANS))})"))
            continue

        if not closing:
            if open_name is not None:
                issues.append(Issue(Severity.ERROR, where, f"<{name}> nested inside <{open_name}>"))
            open_name, open_at = name, match.end()
            count += 1
            continue

        if open_name != name:
            issues.append(Issue(Severity.ERROR, where, f"</{name}> without matching <{name}>"))
            continue

        span_words = len(markup.spoken(text[open_at:match.start()]).split())
        if span_words > markup.MAX_SPAN_WORDS:
            issues.append(Issue(Severity.WARN, where, f"<{name}> covers {span_words} words (max {markup.MAX_SPAN_WORDS})"))
        open_name = None

    if open_name is not None:
        issues.append(Issue(Severity.ERROR, where, f"<{open_name}> never closed"))
    if count > MAX_SPANS:
        issues.append(Issue(Severity.WARN, where, f"{count} span tags (max {MAX_SPANS})"))
    return issues


# ---------------------------------------------------------------- words


def _source_chapter(book: Book | None, number: int) -> list[str] | None:
    if book is None or not 1 <= number <= len(book.chapters):
        return None
    chapter = book.chapters[number - 1]
    return [chapter.title, *[p for p in chapter.paragraphs if p != SCENE_BREAK]]


def _check_words(chapter: ScriptChapter, source: list[str]) -> list[Issue]:
    """Every source word, in order, exactly once. Tags and punctuation ignored."""
    expected = [w for text in source for w in markup.words(text)]
    actual = [w for s in chapter.segments for w in markup.words(s.text)]
    if expected == actual:
        return []

    where = chapter.path or f"chapter {chapter.number:02d}"
    matcher = difflib.SequenceMatcher(a=expected, b=actual, autojunk=False)
    hunks = [op for op in matcher.get_opcodes() if op[0] != "equal"]

    issues = []
    for tag, a0, a1, b0, b1 in hunks[:MAX_DIFFS_SHOWN]:
        context = " ".join(expected[max(0, a0 - DIFF_CONTEXT_WORDS):a0])
        source_words = " ".join(expected[a0:a1]) or "(nothing)"
        script_words = " ".join(actual[b0:b1]) or "(nothing)"
        issues.append(Issue(Severity.ERROR, where, f"text changed after '...{context}': source '{source_words}' -> script '{script_words}'"))

    if len(hunks) > MAX_DIFFS_SHOWN:
        issues.append(Issue(Severity.ERROR, where, f"{len(hunks) - MAX_DIFFS_SHOWN} more text changes"))
    return issues
