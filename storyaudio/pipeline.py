"""Service layer: the steps from book file to finished audiobook.

    extract ─▶ analyze ─▶ draft ─▶ (agent directs) ─▶ check ─▶ voices/studio ─▶ render
    book.json  analysis   script/                     issues   clips+approval   output/

The Casting Studio (studio/server.py) calls only the methods in the
"casting" section below; it never touches files or the TTS client itself.

Folders, for book file 'books/My Book.epub' (id = my-book):

    work/my-book/book.json         extracted chapters
    work/my-book/source.md         extracted text, readable
    work/my-book/analysis.json     numbers for the story-form proposal
    work/my-book/proposal.md       the agent's proposed forms (agent writes it)
    work/my-book/script/*.md       the performance script (edit this)
    work/my-book/voices/*.wav|txt  reference clip per cast member
    work/my-book/voices/approved.json  approved clip digests
    work/my-book/cache/*.wav       rendered lines (resume + re-use)
    output/my-book/                MP3 part(s), SRT per part, script, manifest
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from urllib.parse import urlparse

from storyaudio.audio import ffmpeg
from storyaudio.audio.assemble import Album, Clip, Cut, Part, assemble
from storyaudio.book import Book, slugify
from storyaudio.config import AudioCfg, Autostart, Config
from storyaudio.engine.manager import Engine
from storyaudio.ingest import load_book
from storyaudio.script import fmt, markup
from storyaudio.script.analysis import Analysis, analyze_book
from storyaudio.script.drafter import draft_script
from storyaudio.script.model import NARRATOR, CastMember, Kind, Script
from storyaudio.script.plan import Boundary, Job, plan_jobs
from storyaudio.script.validator import Issue, Severity, check_script, has_errors
from storyaudio.tts.drama3 import Drama3Client
from storyaudio.tts.synth import Progress, Request, Synthesizer
from storyaudio.tts.voices import Approval, Origin, Seed, VoiceBank, VoiceStatus

WORK_ROOT = Path("work")
OUTPUT_ROOT = Path("output")
GLOBAL_VOICES = Path("voices")
SCRIPT_SUFFIX = " - Script.md"
MANIFEST = "manifest.json"
ANALYSIS = "analysis.json"
PROPOSAL = "proposal.md"
PROOF_DIR = "proof"
SECONDS_PER_CHAR = 0.06   # estimate only: ~16 spoken chars/s at audiobook pace
MAX_PREVIEW_CHARS = 1000
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

_SPEAKER_ID = re.compile(r"^[A-Z][A-Z0-9_]{0,31}$")   # same rule as a script label
_TABLE_BREAKERS = ("|", "\n", "\r")

Notice = Callable[[str], None]


class Overwrite(Enum):
    KEEP = "keep"
    REPLACE = "replace"


class Gate(Enum):
    """Which voices render accepts."""

    APPROVED = "approved"   # every speaker's current clip approved by the user
    ANY = "any"             # dry runs and tests


class PipelineError(RuntimeError):
    pass


@dataclass(frozen=True)
class Workspace:
    id: str
    root: Path
    out: Path

    @property
    def book_json(self) -> Path:
        return self.root / "book.json"

    @property
    def source_md(self) -> Path:
        return self.root / "source.md"

    @property
    def analysis_json(self) -> Path:
        return self.root / ANALYSIS

    @property
    def proposal_md(self) -> Path:
        return self.root / PROPOSAL

    @property
    def script_dir(self) -> Path:
        return self.root / "script"

    @property
    def voices_dir(self) -> Path:
        return self.root / "voices"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"


@dataclass(frozen=True)
class Estimate:
    jobs: int
    chars: int
    speakers: list[str]
    seconds: float


@dataclass(frozen=True)
class CastView:
    """One cast member as the studio shows it."""

    member: CastMember
    lines: int
    words: int
    status: VoiceStatus
    transcript: str
    origin: Origin | None
    approved: bool


@dataclass(frozen=True)
class Result:
    parts: list[Part]
    script: Path
    manifest: Path


def workspace(book_path: Path, base: Path = Path(".")) -> Workspace:
    """Work and output folders for a book file (or an existing work folder)."""
    book_id = book_path.name if book_path.is_dir() else slugify(book_path.stem)
    return Workspace(book_id, base / WORK_ROOT / book_id, base / OUTPUT_ROOT / book_id)


class Pipeline:
    def __init__(self, cfg: Config, base: Path = Path("."), notice: Notice | None = None) -> None:
        self._cfg = cfg
        self._base = base
        self._notice = notice or (lambda message: None)

    # ------------------------------------------------------------ steps

    def extract(self, book_path: Path) -> tuple[Workspace, Book]:
        ws = workspace(book_path, self._base)
        book = load_book(book_path)
        ws.root.mkdir(parents=True, exist_ok=True)
        book.save(ws.book_json)
        ws.source_md.write_text(book.to_markdown(), encoding="utf-8")
        return ws, book

    def analyze(self, book_path: Path) -> tuple[Workspace, Analysis]:
        ws, book = self._book(book_path)
        analysis = analyze_book(book)
        ws.analysis_json.write_text(json.dumps(analysis.to_json(), indent=1, ensure_ascii=False), encoding="utf-8")
        return ws, analysis

    def draft(self, book_path: Path, overwrite: Overwrite) -> list[Path]:
        ws, book = self._book(book_path)
        if ws.script_dir.exists() and any(ws.script_dir.glob("*.md")):
            if overwrite is Overwrite.KEEP:
                raise PipelineError(f"{ws.script_dir} already has a script; pass --force to replace it")
            shutil.rmtree(ws.script_dir)
        return fmt.write_script(draft_script(book), ws.script_dir)

    def check(self, book_path: Path) -> tuple[list[Issue], Estimate]:
        ws, book = self._book(book_path)
        script = fmt.read_script(ws.script_dir)
        issues = check_script(script, book)
        return issues, self._estimate(script)

    def voices(self, book_path: Path, seed: Seed, only: list[str]) -> list[VoiceStatus]:
        """Report every cast voice; seed the missing (or `only`) ones on the server."""
        ws, _book = self._book(book_path)
        script = fmt.read_script(ws.script_dir)
        bank = self._bank(ws)
        wanted = [m for m in script.cast if not only or m.id in only]

        if any(bank.find(m.id) is None or seed is Seed.FORCE for m in wanted):
            client = self._client()
            try:
                for member in wanted:
                    bank.seed(client, member.id, member.voice, seed)
            finally:
                client.close()

        return [bank.status(m.id) for m in script.cast]

    def render(self, book_path: Path, progress: Progress | None = None, gate: Gate = Gate.APPROVED, chapters: list[int] | None = None) -> Result:
        """Render the book, or only `chapters` (a proof) into output/<id>/proof/.

        Proof lines land in the same cache, so the full render reuses them.
        """
        ffmpeg.require()
        ws, book = self._book(book_path)
        script = fmt.read_script(ws.script_dir)
        out = ws.out
        if chapters:
            missing = sorted(set(chapters) - {c.number for c in script.chapters})
            if missing:
                raise PipelineError(f"no chapter files numbered {missing}")
            for chapter in script.chapters:
                chapter.skipped = chapter.skipped or chapter.number not in chapters
            out = ws.out / PROOF_DIR

        issues = check_script(script, book)
        if has_errors(issues):
            raise PipelineError("script has errors; run `storyaudio check`:\n" + "\n".join(str(i) for i in issues if i.severity is Severity.ERROR))

        jobs = plan_jobs(script, self._cfg.script.max_segment_chars, self._cfg.script.tag_policy)
        bank = self._bank(ws)
        speakers = sorted({j.speaker for j in jobs})

        # The user signs off each voice; a changed clip needs a new sign-off.
        unapproved = [s for s in speakers if not bank.approved(s)]
        if gate is Gate.APPROVED and unapproved:
            raise PipelineError(
                f"voices not approved: {', '.join(unapproved)}. Approve them in `storyaudio studio`, "
                "or with `storyaudio voices <book> --approve ID ...`, or render with --unapproved for a test."
            )
        voices = {speaker: bank.load(speaker) for speaker in speakers}

        client = self._client()
        try:
            synth = Synthesizer(client, ws.cache_dir, self._cfg.sampling, self._cfg.server.workers, self._cfg.server.retries)
            wavs = synth.render([Request(j.index, j.tts_text, voices[j.speaker]) for j in jobs], progress)
        finally:
            client.close()

        clips = [_clip(job, wav, self._cfg.audio) for job, wav in zip(jobs, wavs)]
        album = Album(script.title or book.title, script.author or book.author, _file_stem(script.title or book.title))
        parts = assemble(clips, album, out, self._cfg.parts, self._cfg.audio.bitrate, self._cfg.audio.loudness_lufs)

        script_path = out / f"{album.stem}{SCRIPT_SUFFIX}"
        script_path.write_text(fmt.render_full(script), encoding="utf-8")
        manifest = self._manifest(out, book, script, parts, script_path)
        return Result(parts, script_path, manifest)

    # ------------------------------------------------------------ casting (studio)

    def cast_view(self, book_path: Path) -> list[CastView]:
        ws, _book = self._book(book_path)
        script = fmt.read_script(ws.script_dir)
        bank = self._bank(ws)
        lines, words = _speaker_counts(script)
        return [
            CastView(m, lines[m.id], words[m.id], bank.status(m.id), bank.transcript(m.id), bank.origin(m.id), bank.approved(m.id))
            for m in script.cast
        ]

    def save_member(self, book_path: Path, member: CastMember) -> None:
        """Add a cast member, or change the name and voice description of one."""
        if not _SPEAKER_ID.match(member.id):
            raise PipelineError(f"{member.id!r}: an ID is A-Z, 0-9 and _, starts with a letter, max 32 chars")
        if any(c in value for value in (member.name, member.voice) for c in _TABLE_BREAKERS):
            raise PipelineError("name and voice must not contain | or line breaks")
        if not member.name.strip():
            raise PipelineError("name is empty")

        ws, _book = self._book(book_path)
        script = fmt.read_script(ws.script_dir)
        existing = script.member(member.id)
        if existing is None:
            script.cast.append(member)
        else:
            existing.name, existing.voice = member.name, member.voice
        (ws.script_dir / fmt.CAST_FILE).write_text(fmt.render_cast(script), encoding="utf-8")

    def store_clip(self, book_path: Path, speaker: str, data: bytes, suffix: str, transcript: str) -> VoiceStatus:
        """Uploaded or recorded audio becomes the speaker's reference clip."""
        ws = self._cast_member(book_path, speaker)
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / f"upload{suffix.lower()}"
            source.write_bytes(data)
            return self._bank(ws).store(speaker, source, transcript)

    def set_transcript(self, book_path: Path, speaker: str, transcript: str) -> None:
        self._bank(self._cast_member(book_path, speaker)).set_transcript(speaker, transcript)

    def clip_wav(self, book_path: Path, speaker: str) -> bytes:
        return self._bank(self._cast_member(book_path, speaker)).clip_wav(speaker)

    def approve(self, book_path: Path, speakers: list[str], approval: Approval) -> None:
        for speaker in speakers:
            self._bank(self._cast_member(book_path, speaker)).approve(speaker, approval)

    def preview(self, book_path: Path, speaker: str, text: str) -> bytes:
        """Speak one line in the speaker's current voice. Not cached."""
        if not text.strip() or len(text) > MAX_PREVIEW_CHARS:
            raise PipelineError(f"preview text must be 1-{MAX_PREVIEW_CHARS} characters")

        voice = self._bank(self._cast_member(book_path, speaker)).load(speaker)
        client = self._client()
        try:
            return client.speak(markup.for_tts(text, self._cfg.script.tag_policy), voice)
        finally:
            client.close()

    def script_files(self, book_path: Path) -> list[str]:
        ws, _book = self._book(book_path)
        return sorted(p.name for p in ws.script_dir.glob("*.md"))

    def read_file(self, book_path: Path, name: str) -> str:
        return self._script_file(book_path, name).read_text(encoding="utf-8")

    def write_file(self, book_path: Path, name: str, text: str) -> None:
        """Save a script file. Unparsable text is refused so the script stays readable."""
        path = self._script_file(book_path, name)
        try:
            if name == fmt.CAST_FILE:
                fmt.parse_cast(text, name)
            else:
                fmt.parse_chapter(text, 0, name)
        except fmt.ScriptFormatError as error:
            raise PipelineError(str(error)) from error
        path.write_text(text, encoding="utf-8")

    def proposal(self, book_path: Path) -> str:
        ws, _book = self._book(book_path)
        return ws.proposal_md.read_text(encoding="utf-8") if ws.proposal_md.exists() else ""

    def server_problem(self) -> str | None:
        """None when the Drama 3 server answers. Never starts it."""
        client = Drama3Client(self._cfg.server.url, self._cfg.server.timeout_s, self._cfg.sampling)
        try:
            return client.health()
        finally:
            client.close()

    # ------------------------------------------------------------ helpers

    def _book(self, book_path: Path) -> tuple[Workspace, Book]:
        ws = workspace(book_path, self._base)
        if not ws.book_json.exists():
            if book_path.is_dir():
                raise PipelineError(f"{ws.book_json} missing; run `storyaudio extract <book file>`")
            return self.extract(book_path)
        return ws, Book.load(ws.book_json)

    def _bank(self, ws: Workspace) -> VoiceBank:
        return VoiceBank(ws.voices_dir, self._base / GLOBAL_VOICES)

    def _cast_member(self, book_path: Path, speaker: str) -> Workspace:
        """Workspace, after checking the speaker is in the cast."""
        ws, _book = self._book(book_path)
        if fmt.read_script(ws.script_dir).member(speaker) is None:
            raise PipelineError(f"{speaker}: not in the cast ({fmt.CAST_FILE})")
        return ws

    def _script_file(self, book_path: Path, name: str) -> Path:
        """Only an existing *.md file directly in script/: no paths, no new files."""
        ws, _book = self._book(book_path)
        path = ws.script_dir / name
        if Path(name).name != name or path.suffix != ".md" or not path.exists():
            raise PipelineError(f"{name!r}: not a script file")
        return path

    def _client(self) -> Drama3Client:
        server = self._cfg.server
        client = Drama3Client(server.url, server.timeout_s, self._cfg.sampling)
        problem = client.health()
        if problem and self._engine_starts():
            self._notice("Drama 3 server is down; starting the local engine")
            Engine(self._cfg, self._base).start(self._notice)
            problem = client.health()

        if problem:
            client.close()
            raise PipelineError(
                f"Drama 3 server not ready: {problem}\n"
                "Run `storyaudio server install`, then `storyaudio server start` (docs/SERVER.md), "
                "or `storyaudio mock-server` to test."
            )
        return client

    def _engine_starts(self) -> bool:
        """Autostart only an installed local engine that serves the configured URL."""
        if self._cfg.engine.autostart is not Autostart.ON:
            return False
        url = urlparse(self._cfg.server.url)
        if url.hostname not in LOCAL_HOSTS or url.port != self._cfg.engine.port:
            return False
        return Engine(self._cfg, self._base).installed() is not None

    def _estimate(self, script: Script) -> Estimate:
        jobs = plan_jobs(script, self._cfg.script.max_segment_chars, self._cfg.script.tag_policy)
        chars = sum(len(j.subtitle) for j in jobs)
        gaps = sum(_gap_ms(j.boundary, self._cfg.audio) for j in jobs) / 1000
        return Estimate(len(jobs), chars, sorted({j.speaker for j in jobs}), chars * SECONDS_PER_CHAR + gaps)

    def _manifest(self, out: Path, book: Book, script: Script, parts: list[Part], script_path: Path) -> Path:
        data = {
            "title": script.title or book.title,
            "author": script.author or book.author,
            "source": book.source,
            "mode": script.mode.value,
            "cast": [{"id": m.id, "name": m.name, "voice": m.voice} for m in script.cast],
            "script": script_path.name,
            "total_seconds": round(sum(p.seconds for p in parts), 2),
            "parts": [
                {"number": p.number, "mp3": p.mp3.name, "srt": p.srt.name, "seconds": round(p.seconds, 2), "chapters": p.chapters}
                for p in parts
            ],
        }
        path = out / MANIFEST
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return path


def _speaker_counts(script: Script) -> tuple[Counter[str], Counter[str]]:
    """Spoken lines and words per speaker, skipped chapters excluded."""
    lines: Counter[str] = Counter()
    words: Counter[str] = Counter()
    for chapter in script.chapters:
        if chapter.skipped:
            continue
        for segment in chapter.segments:
            if segment.kind is Kind.SCENE:
                continue
            speaker = segment.speaker or NARRATOR
            lines[speaker] += 1
            words[speaker] += len(markup.spoken(segment.text).split())
    return lines, words


def _gap_ms(boundary: Boundary, audio: AudioCfg) -> int:
    return {
        Boundary.START: 0,
        Boundary.PIECE: audio.gap_piece_ms,
        Boundary.SEGMENT: audio.gap_segment_ms,
        Boundary.SPEAKER: audio.gap_speaker_ms,
        Boundary.SCENE: audio.gap_scene_ms,
        Boundary.CHAPTER: audio.gap_chapter_ms,
    }[boundary]


_CUTS = {Boundary.PIECE: Cut.NEVER, Boundary.SCENE: Cut.GOOD}


def _clip(job: Job, wav: Path, audio: AudioCfg) -> Clip:
    return Clip(wav, job.subtitle, job.chapter, job.chapter_title, _gap_ms(job.boundary, audio), _CUTS.get(job.boundary, Cut.OK))


def _file_stem(title: str) -> str:
    """Title safe for Windows/macOS/Linux file names."""
    stem = "".join("_" if c in '<>:"/\\|?*' else c for c in title).strip(" .")
    return stem[:120] or "audiobook"
