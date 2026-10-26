"""Voice bank: one reference clip per cast member.

    work/<book>/voices/PEGGY.wav  + PEGGY.txt (exact transcript of the clip)
    voices/NARRATOR.wav           + NARRATOR.txt (global fallback, any book)

A clip you provide (.wav/.mp3/.flac/.m4a/.ogg) always wins. Clips stored
through `store` (Casting Studio uploads and recordings) become canonical WAV.

Approval: work/<book>/voices/approved.json maps speaker -> digest of the clip
and transcript the user approved. Any change to either drops the approval. Missing voices are
seeded: Drama 3 speaks SEED_LINE with the cast member's description as a mood
tag and no reference; that take then fixes the voice for every later request.
Without a fixed reference each request would get a different random voice.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from storyaudio.audio import ffmpeg, wavio
from storyaudio.tts.drama3 import Drama3Client, Voice

AUDIO_EXTENSIONS = (".wav", ".mp3", ".flac", ".m4a", ".ogg")
UPLOAD_EXTENSIONS = (*AUDIO_EXTENSIONS, ".webm", ".mp4", ".opus", ".aac")
TRANSCRIPT_EXT = ".txt"
APPROVALS_FILE = "approved.json"
MAX_REFERENCE_S = 60.0   # server limit per reference
MIN_REFERENCE_S = 3.0    # shorter clips clone poorly
SEED_LINE = (
    "I have always believed that every old house keeps a few secrets. "
    "Some of them are worth keeping, and some are simply waiting for the right person to listen."
)


class Seed(Enum):
    MISSING = "missing"   # only cast members without a clip
    FORCE = "force"       # regenerate even if a clip exists


class Approval(Enum):
    APPROVED = "approved"
    REVOKED = "revoked"


class Origin(Enum):
    BOOK = "book"       # work/<book>/voices/
    GLOBAL = "global"   # voices/ (shared by every book)


class VoiceError(ValueError):
    pass


@dataclass(frozen=True)
class VoiceStatus:
    speaker: str
    path: Path | None
    seconds: float
    error: str = ""     # clip cannot be used
    warning: str = ""   # clip works but may clone poorly


class VoiceBank:
    def __init__(self, book_dir: Path, global_dir: Path) -> None:
        self._book_dir = book_dir
        self._dirs = (book_dir, global_dir)

    def find(self, speaker: str) -> Path | None:
        for folder in self._dirs:
            for ext in AUDIO_EXTENSIONS:
                path = folder / f"{speaker}{ext}"
                if path.exists():
                    return path
        return None

    def origin(self, speaker: str) -> Origin | None:
        path = self.find(speaker)
        if path is None:
            return None
        return Origin.BOOK if path.parent == self._book_dir else Origin.GLOBAL

    def transcript(self, speaker: str) -> str:
        path = self.find(speaker)
        if path is None or not path.with_suffix(TRANSCRIPT_EXT).exists():
            return ""
        return path.with_suffix(TRANSCRIPT_EXT).read_text(encoding="utf-8").strip()

    def status(self, speaker: str) -> VoiceStatus:
        path = self.find(speaker)
        if path is None:
            return VoiceStatus(speaker, None, 0.0, error="no clip")
        if not path.with_suffix(TRANSCRIPT_EXT).exists():
            return VoiceStatus(speaker, path, 0.0, error=f"missing transcript {path.with_suffix(TRANSCRIPT_EXT).name}")

        seconds = wavio.info(_canonical_bytes(path)).seconds
        if seconds > MAX_REFERENCE_S:
            return VoiceStatus(speaker, path, seconds, error=f"{seconds:.1f}s > {MAX_REFERENCE_S:.0f}s server limit")
        if seconds < MIN_REFERENCE_S:
            return VoiceStatus(speaker, path, seconds, warning=f"{seconds:.1f}s; use {MIN_REFERENCE_S:.0f}-30s for stable cloning")
        return VoiceStatus(speaker, path, seconds)

    def load(self, speaker: str) -> Voice:
        status = self.status(speaker)
        if status.error:
            raise VoiceError(f"{speaker}: {status.error}; run `storyaudio voices` or add a clip")

        transcript = status.path.with_suffix(TRANSCRIPT_EXT).read_text(encoding="utf-8").strip()
        return Voice.of(speaker, _canonical_bytes(status.path), transcript)

    def clip_wav(self, speaker: str) -> bytes:
        """The speaker's clip as canonical WAV bytes (for playback)."""
        path = self.find(speaker)
        if path is None:
            raise VoiceError(f"{speaker}: no clip")
        return _canonical_bytes(path)

    def store(self, speaker: str, source: Path, transcript: str) -> VoiceStatus:
        """Make `source` (any ffmpeg format) the speaker's clip for this book."""
        transcript = _clean_transcript(transcript)
        if source.suffix.lower() not in UPLOAD_EXTENSIONS:
            raise VoiceError(f"{source.suffix or 'no extension'}: use one of {', '.join(UPLOAD_EXTENSIONS)}")

        with tempfile.TemporaryDirectory() as tmp:
            canonical = Path(tmp) / "clip.wav"
            ffmpeg.to_canonical(source, canonical)
            data = canonical.read_bytes()

        seconds = wavio.info(data).seconds
        if seconds > MAX_REFERENCE_S:
            raise VoiceError(f"clip is {seconds:.1f} s; the server limit is {MAX_REFERENCE_S:.0f} s. Trim it to 5-30 s.")

        self._clear_book_clip(speaker)
        target = self._book_dir / f"{speaker}.wav"
        target.write_bytes(data)
        target.with_suffix(TRANSCRIPT_EXT).write_text(transcript + "\n", encoding="utf-8")
        return self.status(speaker)

    def set_transcript(self, speaker: str, transcript: str) -> None:
        """Change the transcript. A global clip is first copied into this book."""
        transcript = _clean_transcript(transcript)
        path = self.find(speaker)
        if path is None:
            raise VoiceError(f"{speaker}: no clip; upload or seed one first")

        if self.origin(speaker) is Origin.GLOBAL:
            self._book_dir.mkdir(parents=True, exist_ok=True)
            copy = self._book_dir / f"{speaker}.wav"
            copy.write_bytes(_canonical_bytes(path))
            path = copy
        path.with_suffix(TRANSCRIPT_EXT).write_text(transcript + "\n", encoding="utf-8")

    def approve(self, speaker: str, approval: Approval) -> None:
        approvals = self._approvals()
        approvals.pop(speaker, None)
        if approval is Approval.APPROVED:
            approvals[speaker] = self.load(speaker).digest

        self._book_dir.mkdir(parents=True, exist_ok=True)
        (self._book_dir / APPROVALS_FILE).write_text(json.dumps(approvals, indent=2, sort_keys=True), encoding="utf-8")

    def approved(self, speaker: str) -> bool:
        """True only when the clip and transcript are the ones approved."""
        digest = self._approvals().get(speaker)
        if digest is None or self.status(speaker).error:
            return False
        return self.load(speaker).digest == digest

    def _approvals(self) -> dict[str, str]:
        path = self._book_dir / APPROVALS_FILE
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def _clear_book_clip(self, speaker: str) -> None:
        self._book_dir.mkdir(parents=True, exist_ok=True)
        for ext in (*AUDIO_EXTENSIONS, TRANSCRIPT_EXT):
            (self._book_dir / f"{speaker}{ext}").unlink(missing_ok=True)

    def seed(self, client: Drama3Client, speaker: str, description: str, mode: Seed) -> Path:
        """Create work/<book>/voices/<speaker>.wav from a text description."""
        existing = self.find(speaker)
        if existing is not None and mode is Seed.MISSING:
            return existing

        wav = client.speak(f"[{description}] {SEED_LINE}", None)
        self._clear_book_clip(speaker)
        target = self._book_dir / f"{speaker}.wav"
        target.write_bytes(wav)
        target.with_suffix(TRANSCRIPT_EXT).write_text(SEED_LINE + "\n", encoding="utf-8")
        return target


def _clean_transcript(text: str) -> str:
    """One line, no control tokens. It must match the clip word for word."""
    text = " ".join(text.split())
    if not text:
        raise VoiceError("the transcript is empty; type exactly what the clip says")
    if "<|" in text:
        raise VoiceError("the transcript must not contain <| tokens")
    return text


def _canonical_bytes(path: Path) -> bytes:
    """Clip as canonical WAV bytes; converts other formats through ffmpeg."""
    if path.suffix.lower() == ".wav":
        data = path.read_bytes()
        if wavio.info(data).canonical:
            return data

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "ref.wav"
        ffmpeg.to_canonical(path, target)
        return target.read_bytes()
