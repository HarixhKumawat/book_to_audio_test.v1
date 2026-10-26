"""Drama 3 on Apple Silicon, served over the same HTTP contract as sglang-omni.

mlx-audio ports Drama 3 to Metal. This process loads the checkpoint once and
answers exactly like the CUDA server, so the CLI, the Casting Studio and the
line cache work unchanged:

    storyaudio mlx-server --model mlx-community/fish-audio-drama-3-3bit
    storyaudio render BOOK --server http://127.0.0.1:8000

Memory on a 32 GB Mac (numbers from the port's own documentation):

    3-bit experts   18.5 GB weights   peaks 22.5-24 GB   fits 32 GB
    4-bit experts   22 GB weights     peaks 26 GB        too tight
    bf16            67 GB weights     peaks 74 GB        no

MLX is not thread safe, so one take runs at a time. A second request waits in
its HTTP thread; it never shares a model call.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import numpy as np

from storyaudio.audio import wavio
from storyaudio.tts.drama3 import FINISH_HEADER, HEALTH_PATH, SPEECH_PATH
from storyaudio.tts.protocol import problem as _problem

DEFAULT_MODEL = "mlx-community/fish-audio-drama-3-3bit"
DEFAULT_HOST = "127.0.0.1"   # loopback only: books and clips stay on this Mac
INT16_PEAK = 32767.0
FINISH_STOP = "stop"
FINISH_LENGTH = "length"
EMPTY_AUDIO = "Drama 3 produced no audio for this input"


def _load(model_id: str) -> Any:
    """Load once. Imported here so Windows and Linux never need mlx-audio."""
    try:
        from mlx_audio.tts.utils import load_model
    except ImportError as error:
        raise RuntimeError("mlx-audio is not installed on this machine. On an Apple Silicon Mac: pip install mlx-audio") from error

    return load_model(model_id)


class MlxDrama3:
    """The MLX checkpoint behind the Drama 3 HTTP contract."""

    def __init__(self, model_id: str = DEFAULT_MODEL, port: int = 0, host: str = DEFAULT_HOST) -> None:
        self._model_id = model_id
        self._model = _load(model_id)
        self._check_rate()
        self._lock = threading.Lock()
        self._requests = 0
        self._server = ThreadingHTTPServer((host, port), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def model_id(self) -> str:
        return self._model_id

    def start(self) -> MlxDrama3:
        self._thread.start()
        return self

    def serve(self) -> None:
        self._server.serve_forever()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def speak(self, body: dict) -> tuple[bytes, str]:
        """One take. Returns canonical WAV bytes and the finish reason."""
        budget = body.get("max_new_tokens")
        reference = _reference(body)

        try:
            with self._lock:
                chunks, frames = self._generate(body, budget, reference)
                take = self._requests + 1
                self._requests = take
        finally:
            if reference is not None:
                reference.close()

        if not chunks:
            raise ValueError(EMPTY_AUDIO)

        truncated = budget is not None and frames >= budget
        print(f"{take:5}  {len(body['input']):4} chars  {frames:5} frames  "
              f"{'truncated' if truncated else 'ok'}", flush=True)
        return _encode(chunks), FINISH_LENGTH if truncated else FINISH_STOP

    def _generate(self, body: dict, budget: int | None, reference: _Reference | None) -> tuple[list[np.ndarray], int]:
        """Run the model over the whole text; concatenate every chunk it yields."""
        kwargs: dict[str, Any] = {"text": body["input"], "max_tokens": budget}
        _sampling(body, kwargs)
        if reference is not None:
            kwargs["ref_audio"] = reference.path
            kwargs["ref_text"] = reference.transcript

        chunks: list[np.ndarray] = []
        frames = 0
        for result in self._model.generate(**kwargs):
            chunks.append(np.asarray(result.audio, dtype=np.float32).reshape(-1))
            frames += int(getattr(result, "token_count", 0))
        return chunks, frames

    def _check_rate(self) -> None:
        """The assembler assumes 44.1 kHz; refuse a checkpoint that differs."""
        rate = int(getattr(self._model, "sample_rate", wavio.SAMPLE_RATE))
        if rate != wavio.SAMPLE_RATE:
            raise ValueError(f"{self._model_id} decodes at {rate} Hz; this tool needs {wavio.SAMPLE_RATE} Hz")

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:
                pass   # one progress line per take is enough

            def do_GET(self) -> None:
                if self.path != HEALTH_PATH:
                    self._reply(HTTPStatus.NOT_FOUND, b"not found")
                    return
                self._reply(HTTPStatus.OK, b"ok")

            def do_POST(self) -> None:
                if self.path != SPEECH_PATH:
                    self._reply(HTTPStatus.NOT_FOUND, b"not found")
                    return
                try:
                    body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                except (KeyError, TypeError, ValueError) as error:
                    self._reply_json(HTTPStatus.BAD_REQUEST, f"bad request: {error}")
                    return

                problem = _problem(body)
                if problem:
                    self._reply_json(HTTPStatus.BAD_REQUEST, problem)
                    return

                try:
                    wav, finish = bridge.speak(body)
                except ValueError as error:
                    self._reply_json(HTTPStatus.BAD_REQUEST, str(error))
                    return
                except Exception as error:   # model failure: the client retries 5xx
                    self._reply_json(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(error).__name__}: {error}")
                    return

                self._reply(HTTPStatus.OK, wav, {"Content-Type": "audio/wav", FINISH_HEADER: finish})

            def _reply_json(self, status: HTTPStatus, message: str) -> None:
                self._reply(status, json.dumps({"error": {"message": message}}).encode())

            def _reply(self, status: HTTPStatus, data: bytes, headers: dict | None = None) -> None:
                self.send_response(status)
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        return Handler


class _Reference:
    """A reference clip on disk for as long as the model needs it."""

    def __init__(self, data: bytes, transcript: str) -> None:
        handle, name = tempfile.mkstemp(suffix=".wav", prefix="drama3-ref-")
        os.close(handle)
        Path(name).write_bytes(data)
        self.path = name
        self.transcript = transcript

    def close(self) -> None:
        Path(self.path).unlink(missing_ok=True)


def _sampling(body: dict, kwargs: dict[str, Any]) -> None:
    """Copy only the sampling values the request actually set.

    A top_k cap on the main token makes Drama 3 fall silent, so an unset top_k
    must stay unset instead of inheriting a default.
    """
    if body.get("temperature") is not None:
        kwargs["temperature"] = body["temperature"]
    if body.get("top_p") is not None:
        kwargs["top_p"] = body["top_p"]
    if body.get("top_k") is not None:
        kwargs["top_k"] = body["top_k"]


def _reference(body: dict) -> _Reference | None:
    references = body.get("references") or []
    if not references:
        return None
    first = references[0]
    return _Reference(base64.b64decode(first["data"]), first.get("text") or "")


def _encode(chunks: list[np.ndarray]) -> bytes:
    """Float chunks in [-1, 1] -> one canonical mono 44.1 kHz WAV."""
    samples = np.concatenate(chunks)
    pcm = (np.clip(samples, -1.0, 1.0) * INT16_PEAK).astype(np.int16)

    handle, name = tempfile.mkstemp(suffix=".wav", prefix="drama3-out-")
    os.close(handle)
    path = Path(name)
    try:
        wavio.write(path, pcm)
        return path.read_bytes()
    finally:
        path.unlink(missing_ok=True)
