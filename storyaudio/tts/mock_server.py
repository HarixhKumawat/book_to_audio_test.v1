"""A stand-in Drama 3 server for machines without the GPU.

Same endpoints, same request checks, same 44.1 kHz mono WAV output - but the
"speech" is a tone (pitch fixed per reference voice) whose length follows the
text. Use it to test the whole pipeline end to end:

    storyaudio mock-server --port 8000
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import threading
import wave
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

from storyaudio.audio.wavio import SAMPLE_RATE
from storyaudio.tts.drama3 import FINISH_HEADER, HEALTH_PATH, SPEECH_PATH
from storyaudio.tts.protocol import problem as _problem

SECONDS_PER_CHAR = 0.06   # ~16 chars/s, close to audiobook pace
EDGE_SILENCE_S = 0.15     # real takes start/end with a little silence
_TAG = re.compile(r"\[[^\]]*\]|</?[a-z ]+>")


class MockDrama3:
    """Runs the mock in a background thread. fail_first > 0 answers 503 first."""

    def __init__(self, port: int = 0, fail_first: int = 0) -> None:
        self.requests: list[dict] = []
        self._fail_left = fail_first
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", port), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> MockDrama3:
        self._thread.start()
        return self

    def serve(self) -> None:
        self._server.serve_forever()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def _should_fail(self) -> bool:
        with self._lock:
            if self._fail_left <= 0:
                return False
            self._fail_left -= 1
            return True

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:
                pass

            def do_GET(self) -> None:
                if self.path != HEALTH_PATH:
                    self._reply(HTTPStatus.NOT_FOUND, b"not found")
                    return
                self._reply(HTTPStatus.OK, b"ok")

            def do_POST(self) -> None:
                if self.path != SPEECH_PATH:
                    self._reply(HTTPStatus.NOT_FOUND, b"not found")
                    return
                if mock._should_fail():
                    self._reply(HTTPStatus.SERVICE_UNAVAILABLE, b"warming up")
                    return

                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                with mock._lock:
                    mock.requests.append(body)

                problem = _problem(body)
                if problem:
                    self._reply(HTTPStatus.BAD_REQUEST, json.dumps({"error": {"message": problem}}).encode())
                    return

                wav = _tone(body)
                self._reply(HTTPStatus.OK, wav, {"Content-Type": "audio/wav", FINISH_HEADER: "stop"})

            def _reply(self, status: HTTPStatus, data: bytes, headers: dict | None = None) -> None:
                self.send_response(status)
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        return Handler


def _tone(body: dict) -> bytes:
    """A syllable-pulsed tone; pitch from the voice so speakers sound different."""
    spoken = _TAG.sub("", body["input"]).strip()
    seconds = max(0.4, len(spoken) * SECONDS_PER_CHAR)
    references = body.get("references") or []
    seed = references[0]["data"][:4096] if references else body["input"]
    pitch = 140 + int(hashlib.md5(seed.encode()).hexdigest(), 16) % 160

    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    envelope = 0.5 + 0.5 * np.sin(2 * np.pi * 4 * t)   # ~4 syllables/s
    voice = (0.25 * envelope * np.sin(2 * np.pi * pitch * t) * 32767).astype(np.int16)
    edge = np.zeros(int(EDGE_SILENCE_S * SAMPLE_RATE), dtype=np.int16)
    samples = np.concatenate([edge, voice, edge])

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(samples.tobytes())
    return buffer.getvalue()
