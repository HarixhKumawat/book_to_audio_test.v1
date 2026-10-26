"""Render TTS jobs to WAV files: parallel, cached, resumable, retried.

Cache key = hash(text, voice clip, sampling). Re-running after a crash, or
after editing one line of the script, only renders what changed.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path

from storyaudio.audio import ffmpeg, wavio
from storyaudio.config import SamplingCfg
from storyaudio.tts.drama3 import Drama3Client, TtsRejected, TtsTruncated, TtsUnavailable, Voice

BACKOFF_BASE_S = 2.0
TRUNCATION_GROWTH = 2   # retry a cut-short take with this many times the token budget


@dataclass(frozen=True)
class Request:
    """One line to speak with one voice."""

    index: int
    text: str
    voice: Voice


@dataclass(frozen=True)
class Failure:
    index: int
    text: str
    reason: str


class SynthesisError(RuntimeError):
    def __init__(self, failures: list[Failure]) -> None:
        self.failures = failures
        lines = [f"  job {f.index}: {f.reason} | {f.text[:80]!r}" for f in failures[:10]]
        super().__init__(f"{len(failures)} job(s) failed:\n" + "\n".join(lines))


Progress = Callable[[int, int, bool], None]   # (done, total, from_cache)


class Synthesizer:
    def __init__(self, client: Drama3Client, cache_dir: Path, sampling: SamplingCfg, workers: int, retries: int) -> None:
        self._client = client
        self._cache = cache_dir
        self._sampling = sampling
        self._workers = max(1, workers)
        self._retries = max(0, retries)

    def cached_path(self, request: Request) -> Path:
        key = json.dumps([request.text, request.voice.digest, asdict(self._sampling)], sort_keys=True)
        return self._cache / f"{hashlib.sha256(key.encode()).hexdigest()[:24]}.wav"

    def render(self, requests: list[Request], progress: Progress | None = None) -> list[Path]:
        """WAV path per request, same order. Raises SynthesisError if any failed."""
        self._cache.mkdir(parents=True, exist_ok=True)
        paths = [self.cached_path(r) for r in requests]
        todo = {p: r for r, p in zip(requests, paths) if not p.exists()}  # same line twice renders once

        counts = Counter(paths)
        done = len(requests) - sum(counts[p] for p in todo)
        if progress and done:
            progress(done, len(requests), True)

        failures: list[Failure] = []
        with ThreadPoolExecutor(max_workers=self._workers) as pool:
            futures = {pool.submit(self._render_one, r, p): p for p, r in todo.items()}
            for future in as_completed(futures):
                path = futures[future]
                error = future.exception()
                if error is not None:
                    request = todo[path]
                    failures.append(Failure(request.index, request.text, f"{type(error).__name__}: {error}"))
                    continue
                done += counts[path]
                if progress:
                    progress(done, len(requests), False)

        if failures:
            raise SynthesisError(sorted(failures, key=lambda f: f.index))
        return paths

    def _render_one(self, request: Request, target: Path) -> None:
        wav = self._speak(request)
        self._store(wav, target)

    def _speak(self, request: Request) -> bytes:
        """Retry transient failures with backoff; grow the budget once on truncation."""
        budget = self._sampling.max_new_tokens
        grown = False
        attempt = 0

        while True:
            try:
                return self._client.speak(request.text, request.voice, budget)
            except TtsRejected:
                raise
            except TtsTruncated:
                if grown:
                    raise
                budget, grown = budget * TRUNCATION_GROWTH, True
            except TtsUnavailable:
                if attempt >= self._retries:
                    raise
                time.sleep(BACKOFF_BASE_S ** attempt)
                attempt += 1

    def _store(self, wav: bytes, target: Path) -> None:
        """Write atomically as canonical WAV, so a crash never leaves a bad cache entry."""
        fd, tmp_name = tempfile.mkstemp(suffix=".part.wav", dir=self._cache)
        os.close(fd)
        tmp = Path(tmp_name)
        canon = tmp.with_suffix(".canon.wav")
        try:
            tmp.write_bytes(wav)
            if not wavio.info(tmp).canonical:
                ffmpeg.to_canonical(tmp, canon)
                canon.replace(tmp)
            tmp.replace(target)
        finally:
            tmp.unlink(missing_ok=True)
            canon.unlink(missing_ok=True)
