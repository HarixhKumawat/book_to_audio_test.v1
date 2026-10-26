"""Low-level 16-bit PCM WAV I/O (stdlib wave + numpy)."""

from __future__ import annotations

import io
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SAMPLE_RATE = 44_100   # Drama 3 decodes to 44.1 kHz mono
CHANNELS = 1
SAMPLE_WIDTH = 2       # bytes, int16
INT16_FULL_SCALE = 32768.0


@dataclass(frozen=True)
class WavInfo:
    rate: int
    channels: int
    width: int
    frames: int

    @property
    def seconds(self) -> float:
        return self.frames / self.rate if self.rate else 0.0

    @property
    def canonical(self) -> bool:
        return (self.rate, self.channels, self.width) == (SAMPLE_RATE, CHANNELS, SAMPLE_WIDTH)


def info(source: Path | bytes) -> WavInfo:
    with _open(source) as wav:
        return WavInfo(wav.getframerate(), wav.getnchannels(), wav.getsampwidth(), wav.getnframes())


def read(path: Path) -> np.ndarray:
    """Canonical WAV -> int16 samples."""
    with _open(path) as wav:
        data = wav.readframes(wav.getnframes())
    return np.frombuffer(data, dtype=np.int16)


def write(path: Path, samples: np.ndarray) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(CHANNELS)
        wav.setsampwidth(SAMPLE_WIDTH)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(samples.astype(np.int16).tobytes())


class Writer:
    """Append-only canonical WAV writer that counts samples written."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._wav: wave.Wave_write | None = None
        self.samples = 0

    def __enter__(self) -> Writer:
        self._wav = wave.open(str(self._path), "wb")
        self._wav.setnchannels(CHANNELS)
        self._wav.setsampwidth(SAMPLE_WIDTH)
        self._wav.setframerate(SAMPLE_RATE)
        return self

    def add(self, samples: np.ndarray) -> None:
        self._wav.writeframes(samples.astype(np.int16).tobytes())
        self.samples += samples.size

    def __exit__(self, *exc: object) -> None:
        self._wav.close()


def silence(ms: int) -> np.ndarray:
    return np.zeros(int(SAMPLE_RATE * ms / 1000), dtype=np.int16)


def trim(samples: np.ndarray, threshold_db: float = -45.0, pad_ms: int = 40) -> np.ndarray:
    """Cut leading/trailing near-silence so gaps come only from the assembler.

    Works on 10 ms windows: the first and last window louder than threshold_db
    mark the speech; pad_ms is kept on both sides.
    """
    window = SAMPLE_RATE // 100
    if samples.size < window:
        return samples

    usable = samples[: samples.size - samples.size % window].astype(np.float32)
    peaks = np.abs(usable.reshape(-1, window)).max(axis=1) / INT16_FULL_SCALE
    loud = np.nonzero(peaks > 10 ** (threshold_db / 20))[0]
    if loud.size == 0:
        return samples

    pad = int(SAMPLE_RATE * pad_ms / 1000)
    start = max(0, loud[0] * window - pad)
    end = min(samples.size, (loud[-1] + 1) * window + pad)
    return samples[start:end]


def _open(source: Path | bytes) -> wave.Wave_read:
    if isinstance(source, bytes):
        return wave.open(io.BytesIO(source), "rb")
    return wave.open(str(source), "rb")
