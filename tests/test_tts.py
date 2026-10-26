"""Drama 3 driver, synthesizer cache/retry, voice bank - against the mock server."""

import base64
from pathlib import Path

import numpy as np
import pytest

from storyaudio.audio import wavio
from storyaudio.config import SamplingCfg
from storyaudio.tts.drama3 import Drama3Client, TtsRejected, Voice
from storyaudio.tts.mock_server import MockDrama3
from storyaudio.tts.synth import Request, SynthesisError, Synthesizer
from storyaudio.tts.voices import SEED_LINE, Seed, VoiceBank, VoiceError

SAMPLING = SamplingCfg()


def _client(server) -> Drama3Client:
    return Drama3Client(server.url, 30, SAMPLING)


def _voice(seconds: float = 5.0) -> Voice:
    import io
    import wave

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(wavio.SAMPLE_RATE)
        wav.writeframes(np.zeros(int(seconds * wavio.SAMPLE_RATE), dtype=np.int16).tobytes())
    return Voice.of("PEGGY", buffer.getvalue(), "A reference line.")


# ---------------------------------------------------------------- driver


def test_payload_matches_server_schema(mock_server):
    client = _client(mock_server)
    wav = client.speak("[sighs] Fine.", _voice())

    body = mock_server.requests[-1]
    assert body["input"] == "[sighs] Fine."
    assert body["response_format"] == "wav"
    assert body["references"][0]["media_type"] == "audio/wav"
    assert body["references"][0]["text"] == "A reference line."
    assert base64.b64decode(body["references"][0]["data"]).startswith(b"RIFF")
    assert "top_k" not in body
    assert wavio.info(wav).canonical


def test_rejected_request_raises(mock_server):
    with pytest.raises(TtsRejected, match="only speaker 0"):
        _client(mock_server).speak("<|speaker:1|> hi", None)


def test_reference_over_60s_rejected(mock_server):
    with pytest.raises(TtsRejected, match="60 s limit"):
        _client(mock_server).speak("hi", _voice(61))


def test_health(mock_server):
    assert _client(mock_server).health() is None
    assert Drama3Client("http://127.0.0.1:9", 1, SAMPLING).health() is not None


# ---------------------------------------------------------------- synthesizer


def test_cache_makes_rerun_free(mock_server, tmp_path: Path):
    synth = Synthesizer(_client(mock_server), tmp_path, SAMPLING, workers=4, retries=0)
    requests = [Request(i, f"Line {i}.", _voice()) for i in range(6)]

    first = synth.render(requests)
    count = len(mock_server.requests)
    second = synth.render(requests)

    assert first == second
    assert count == 6
    assert len(mock_server.requests) == 6


def test_duplicate_lines_render_once(mock_server, tmp_path: Path):
    synth = Synthesizer(_client(mock_server), tmp_path, SAMPLING, workers=2, retries=0)
    paths = synth.render([Request(0, "Yes.", _voice()), Request(1, "Yes.", _voice())])

    assert paths[0] == paths[1]
    assert len(mock_server.requests) == 1


def test_transient_errors_are_retried(tmp_path: Path):
    server = MockDrama3(fail_first=2).start()
    try:
        synth = Synthesizer(_client(server), tmp_path, SAMPLING, workers=1, retries=3)
        paths = synth.render([Request(0, "Hello.", _voice())])
    finally:
        server.stop()

    assert paths[0].exists()


def test_failures_are_reported_together(mock_server, tmp_path: Path):
    synth = Synthesizer(_client(mock_server), tmp_path, SAMPLING, workers=2, retries=0)
    requests = [Request(0, "Fine.", _voice()), Request(1, "<|speaker:2|> bad", _voice())]

    with pytest.raises(SynthesisError) as error:
        synth.render(requests)

    assert [f.index for f in error.value.failures] == [1]
    assert not list(tmp_path.glob("*.part.wav"))


# ---------------------------------------------------------------- voices


def test_seed_creates_clip_and_transcript(mock_server, tmp_path: Path):
    bank = VoiceBank(tmp_path / "book", tmp_path / "global")
    path = bank.seed(_client(mock_server), "PEGGY", "bright young woman", Seed.MISSING)

    assert path.read_bytes().startswith(b"RIFF")
    assert path.with_suffix(".txt").read_text(encoding="utf-8").strip() == SEED_LINE
    assert mock_server.requests[-1]["input"].startswith("[bright young woman] ")
    assert "references" not in mock_server.requests[-1]
    assert bank.load("PEGGY").transcript == SEED_LINE


def test_seed_keeps_existing_unless_forced(mock_server, tmp_path: Path):
    bank = VoiceBank(tmp_path / "book", tmp_path / "global")
    bank.seed(_client(mock_server), "PEGGY", "x", Seed.MISSING)
    bank.seed(_client(mock_server), "PEGGY", "x", Seed.MISSING)
    assert len(mock_server.requests) == 1

    bank.seed(_client(mock_server), "PEGGY", "x", Seed.FORCE)
    assert len(mock_server.requests) == 2


def test_global_voice_fallback_and_problems(tmp_path: Path):
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    (global_dir / "NARRATOR.wav").write_bytes(_voice(5).wav)
    (global_dir / "NARRATOR.txt").write_text("Hello.", encoding="utf-8")
    (global_dir / "LONG.wav").write_bytes(_voice(61).wav)
    (global_dir / "LONG.txt").write_text("x", encoding="utf-8")
    (global_dir / "NOTEXT.wav").write_bytes(_voice(5).wav)
    bank = VoiceBank(tmp_path / "book", global_dir)

    assert bank.load("NARRATOR").transcript == "Hello."
    assert "60s server limit" in bank.status("LONG").error
    assert "transcript" in bank.status("NOTEXT").error
    with pytest.raises(VoiceError):
        bank.load("NOBODY")
