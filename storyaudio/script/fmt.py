"""Read and write the Markdown script format.

work/<book>/script/
    00-cast.md          title, author, mode, cast table
    01-prologue.md      one file per source chapter; NN = source chapter number
    02-...

Chapter file:

    # We Can Go Back Again            <- chapter title, read by NARRATOR

    NARRATOR: [warm, wry] Four years later...

    PEGGY: [bright] "Morning."        <- SPEAKER_ID: text (one segment)
    still Peggy's segment             <- unlabelled lines continue it

    ---                               <- scene break
    <!-- notes are ignored -->

A file containing <!-- skip: reason --> is not narrated (table of contents,
copyright page). The word check accepts it.
"""

from __future__ import annotations

import re
from pathlib import Path

from storyaudio.script.model import NARRATOR, CastMember, Kind, Mode, Script, ScriptChapter, Segment

CAST_FILE = "00-cast.md"
SCENE_MARK = "---"

_LABEL = re.compile(r"^([A-Z][A-Z0-9_]{0,31}):\s*(.*)$")
_HEADING = re.compile(r"^#{1,3}\s+(.+?)\s*#*$")
_SCENE = re.compile(r"^\s*(?:-{3,}|\*{3,}|(?:\*\s*){3})\s*$")
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_SKIP = re.compile(r"<!--\s*skip\b", re.IGNORECASE)
_META = re.compile(r"^(Title|Author|Mode):\s*(.*)$", re.IGNORECASE)
_ROW = re.compile(r"^\|(.+)\|\s*$")
_CHAPTER_FILE = re.compile(r"^(\d{2,4})-.*\.md$")


class ScriptFormatError(ValueError):
    pass


# ---------------------------------------------------------------- reading


def read_script(folder: Path) -> Script:
    cast_path = folder / CAST_FILE
    if not cast_path.exists():
        raise ScriptFormatError(f"{cast_path}: missing cast file")

    script = parse_cast(cast_path.read_text(encoding="utf-8"), str(cast_path))
    for path in sorted(folder.glob("*.md")):
        match = _CHAPTER_FILE.match(path.name)
        if match is None or path.name == CAST_FILE:
            continue
        chapter = parse_chapter(path.read_text(encoding="utf-8"), int(match.group(1)), str(path))
        script.chapters.append(chapter)

    return script


def parse_cast(text: str, where: str = "cast") -> Script:
    meta = {"title": "", "author": "", "mode": Mode.FAITHFUL.value}
    cast: list[CastMember] = []

    for line in _COMMENT.sub("", text).splitlines():
        line = line.strip()
        meta_match = _META.match(line)
        if meta_match:
            meta[meta_match.group(1).lower()] = meta_match.group(2).strip()
            continue

        row = _ROW.match(line)
        if row is None:
            continue
        cells = [c.strip() for c in row.group(1).split("|")]
        if len(cells) < 3 or set(cells[0]) <= {"-", ":", " "} or cells[0].upper() == "ID":
            continue  # separator or header row
        cast.append(CastMember(id=cells[0].upper(), name=cells[1], voice=" | ".join(cells[2:])))

    try:
        mode = Mode(meta["mode"].lower())
    except ValueError:
        raise ScriptFormatError(f"{where}: Mode must be faithful or adapted, got {meta['mode']!r}") from None

    if not any(m.id == NARRATOR for m in cast):
        raise ScriptFormatError(f"{where}: cast table needs a {NARRATOR} row")

    return Script(title=meta["title"], author=meta["author"], mode=mode, cast=cast)


def parse_chapter(text: str, number: int, where: str = "") -> ScriptChapter:
    chapter = ScriptChapter(number=number, title="", path=where, skipped=bool(_SKIP.search(text)))
    current: Segment | None = None

    # Blank out comments but keep line numbers stable for error messages.
    text = _COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), text)

    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            current = None
            continue

        heading = _HEADING.match(line)
        if heading:
            current = None
            chapter.segments.append(Segment(Kind.TITLE, NARRATOR, heading.group(1), line_no))
            chapter.title = chapter.title or heading.group(1)
            continue

        if _SCENE.match(line):
            current = None
            chapter.segments.append(Segment(Kind.SCENE, line_no=line_no))
            continue

        label = _LABEL.match(line)
        if label:
            current = Segment(Kind.LINE, label.group(1), label.group(2).strip(), line_no)
            chapter.segments.append(current)
            continue

        if current is None:
            raise ScriptFormatError(f"{where}:{line_no}: line has no SPEAKER: label: {line[:60]!r}")
        current.text = f"{current.text} {line}".strip()

    return chapter


# ---------------------------------------------------------------- writing


def write_script(script: Script, folder: Path) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    paths = [folder / CAST_FILE]
    paths[0].write_text(render_cast(script), encoding="utf-8")

    for chapter in script.chapters:
        path = folder / chapter_file_name(chapter)
        path.write_text(render_chapter(chapter), encoding="utf-8")
        paths.append(path)

    return paths


def chapter_file_name(chapter: ScriptChapter) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", chapter.title.lower()).strip("-")[:40].rstrip("-")
    return f"{chapter.number:02d}-{slug or 'chapter'}.md"


def render_cast(script: Script) -> str:
    lines = [
        "# Cast",
        "",
        f"Title: {script.title}",
        f"Author: {script.author}",
        f"Mode: {script.mode.value}",
        "",
        "| ID | Name | Voice |",
        "| --- | --- | --- |",
    ]
    lines += [f"| {m.id} | {m.name} | {m.voice} |" for m in script.cast]
    return "\n".join(lines) + "\n"


def render_chapter(chapter: ScriptChapter) -> str:
    lines: list[str] = ["<!-- skip -->", ""] if chapter.skipped else []
    for segment in chapter.segments:
        if segment.kind is Kind.TITLE:
            lines += [f"# {segment.text}", ""]
        elif segment.kind is Kind.SCENE:
            lines += [SCENE_MARK, ""]
        else:
            lines += [f"{segment.speaker}: {segment.text}", ""]
    return "\n".join(lines)


def render_full(script: Script) -> str:
    """One Markdown document: cast, then every chapter. The shipped script."""
    parts = [f"# {script.title}", "", f"By {script.author}", "", render_cast(script).replace("# Cast", "## Cast", 1)]
    for chapter in (c for c in script.chapters if not c.skipped):
        body = render_chapter(chapter)
        parts += [re.sub(r"^# ", "## ", body, count=1, flags=re.MULTILINE)]
    return "\n".join(parts)
