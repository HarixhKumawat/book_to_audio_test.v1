"""MLX bridge: the Drama 3 HTTP contract on Apple Silicon, with a fake model.

No MLX, no Metal, no GPU. The real driver talks to the bridge, so a passing
test here means `render` will talk to it the same way on a Mac.
"""

from __future__ import annotations

import io
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from storyaudio.audio import wavio
from storyaudio.config import SamplingCfg
from storyaudio.tts import mlx_server
from storyaudio.tts.drama3 import Drama3Client, TtsRejected, TtsTruncated, Voice
from storyaudio.tts.mlx_server import MlxDrama3
from storyaudio.tts.synth import Request, Synthesizer
SAMPLING = SamplingCfg()
SAMPLES_PER_FRAME = 1894   # 44100 Hz / 21.5 frames per second, about right
CHUNK = 0.3                # seconds of audio per generated chunk


class FakeModel:
    """Stands in for mlx-audio's Drama 3 model."""

    sample_rate = wavio.SAMPLE_RATE

    def __init__(self, chunks: int = 2, token_count: int = 5) -> None:
        self.calls: list[dict] = []
        self.chunks = chunks
        self.token_count = token_count
        self.reference_existed = False

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        reference = kwargs.get("ref_audio")
        self.reference_existed = reference is None or Path(reference).exists()
        for _ in range(self.chunks):
            audio = np.full(int(CHUNK * self.sample_rate), 0.2, dtype=np.float32)
            yield SimpleNamespace(audio=audio, token_count=self.token_count, sample_rate=self.sample_rate)


def _wav_bytes(seconds: float = 5.0) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(wavio.SAMPLE_RATE)
        wav.writeframes(np.zeros(int(seconds * wavio.SAMPLE_RATE), dtype=np.int16).tobytes())
    return buffer.getvalue()


def _voice(seconds: float = 5.0) -> Voice:
    return Voice.of("PEGGY", _wav_bytes(seconds), "A reference line.")


@pytest.fixture
def bridge(monkeypatch):
    """A running bridge with a fake model, plus the model for assertions."""
    fake = FakeModel()
    monkeypatch.setattr(mlx_server, "_load", lambda model_id: fake)
    server = MlxDrama3("fake/drama-3", port=0).start()
    yield server, fake
    server.stop()


def _client(server: MlxDrama3, sampling: SamplingCfg = SAMPLING) -> Drama3Client:
    return Drama3Client(server.url, 30, sampling)


# ---------------------------------------------------------------- contract


def test_health_ok(bridge):
    server, _ = bridge
    assert _client(server).health() is None


def test_speak_returns_canonical_wav(bridge):
    server, fake = bridge
    wav = _client(server).speak("[sighs] Fine.", _voice())

    assert wavio.info(wav).canonical
    assert wavio.info(wav).seconds == pytest.approx(2 * CHUNK, abs=0.01)
    assert fake.calls[0]["text"] == "[sighs] Fine."


def test_reference_reaches_the_model_then_is_removed(bridge):
    server, fake = bridge
    _client(server).speak("Hello.", _voice())

    call = fake.calls[0]
    assert fake.reference_existed
    assert call["ref_text"] == "A reference line."
    assert not Path(call["ref_audio"]).exists()   # temp clip cleaned up after the take


def test_no_reference_passes_no_reference(bridge):
    server, fake = bridge
    _client(server).speak("Hello.", None)

    assert "ref_audio" not in fake.calls[0]
    assert "ref_text" not in fake.calls[0]


def test_unset_top_k_stays_unset(bridge):
    server, fake = bridge
    _client(server).speak("Hello.", None)

    assert "top_k" not in fake.calls[0]
    assert fake.calls[0]["temperature"] == SAMPLING.temperature
    assert fake.calls[0]["top_p"] == SAMPLING.top_p


def test_top_k_is_forwarded_when_set(bridge):
    server, fake = bridge
    sampling = SamplingCfg(top_k=30)
    _client(server, sampling).speak("Hello.", None)

    assert fake.calls[0]["top_k"] == 30


def test_budget_is_forwarded_as_max_tokens(bridge):
    server, fake = bridge
    _client(server).speak("Hello.", None, max_new_tokens=512)

    assert fake.calls[0]["max_tokens"] == 512


# ---------------------------------------------------------------- failures


def test_truncation_raises_so_the_client_can_grow(monkeypatch):
    fake = FakeModel(chunks=1, token_count=2048)
    monkeypatch.setattr(mlx_server, "_load", lambda model_id: fake)
    server = MlxDrama3("fake/drama-3", port=0).start()
    try:
        with pytest.raises(TtsTruncated):
            _client(server).speak("Hello.", None)
    finally:
        server.stop()


def test_speaker_tags_are_rejected(bridge):
    server, _ = bridge
    with pytest.raises(TtsRejected, match="only speaker 0"):
        _client(server).speak("<|speaker:1|> hi", None)


def test_empty_input_is_rejected(bridge):
    server, _ = bridge
    with pytest.raises(TtsRejected, match="must not be empty"):
        _client(server).speak("   ", None)


def test_empty_audio_is_rejected(monkeypatch):
    fake = FakeModel(chunks=0)
    monkeypatch.setattr(mlx_server, "_load", lambda model_id: fake)
    server = MlxDrama3("fake/drama-3", port=0).start()
    try:
        with pytest.raises(TtsRejected, match="no audio"):
            _client(server).speak("Hello.", None)
    finally:
        server.stop()


def test_wrong_sample_rate_refuses_to_start(monkeypatch):
    class WrongRate(FakeModel):
        sample_rate = 24_000

    monkeypatch.setattr(mlx_server, "_load", lambda model_id: WrongRate())
    with pytest.raises(ValueError, match="24000 Hz"):
        MlxDrama3("fake/drama-3", port=0)


# ---------------------------------------------------------------- pipeline


def test_synthesizer_caches_through_the_bridge(bridge, tmp_path: Path):
    """The real render path: cache by text + voice, canonical WAV on disk."""
    server, _ = bridge
    synth = Synthesizer(_client(server), tmp_path, SAMPLING, workers=1, retries=0)
    requests = [Request(i, f"Line {i}.", _voice()) for i in range(3)]

    first = synth.render(requests)
    second = synth.render(requests)

    assert first == second
    assert all(wavio.info(path).canonical for path in first)
    assert len(list(tmp_path.glob("*.wav"))) == 3
