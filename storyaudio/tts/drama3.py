"""HTTP driver for Fish Audio Drama 3 on sglang-omni (POST /v1/audio/speech).

Request shape (verified against sglang_omni/serve/protocol.py and
models/fishaudio_drama3/speech_request.py):

    {"input": "[sighs] Fine.", "response_format": "wav",
     "references": [{"data": <base64 wav>, "media_type": "audio/wav",
                     "text": <transcript>}],
     "temperature": 1.0, "top_p": 0.9, "max_new_tokens": 2048}

Server facts this driver relies on:
  - only <|speaker:0|> is accepted; one voice per request
  - each reference <= 60 s; prompt <= 8192 rows (~21.5 rows per audio second)
  - response is 44.1 kHz mono WAV; header X-Finish-Reason is "length" when
    max_new_tokens cut the audio short
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass, field

import httpx

from storyaudio.config import SamplingCfg

SPEECH_PATH = "/v1/audio/speech"
HEALTH_PATH = "/health"
FINISH_HEADER = "X-Finish-Reason"
FINISH_LENGTH = "length"
WAV_MEDIA = "audio/wav"
HTTP_CLIENT_ERROR = 400
HTTP_SERVER_ERROR = 500


class TtsError(RuntimeError):
    pass


class TtsRejected(TtsError):
    """4xx: the request itself is wrong. Retrying will not help."""


class TtsUnavailable(TtsError):
    """Connection failure, timeout or 5xx. Retry later."""


class TtsTruncated(TtsError):
    """Audio stopped at max_new_tokens."""


@dataclass(frozen=True)
class Voice:
    """A reference clip that fixes a speaker's voice."""

    speaker: str
    wav: bytes
    transcript: str
    digest: str = field(default="", compare=False)

    @staticmethod
    def of(speaker: str, wav: bytes, transcript: str) -> Voice:
        digest = hashlib.sha256(wav + transcript.encode()).hexdigest()[:16]
        return Voice(speaker, wav, transcript, digest)


class Drama3Client:
    def __init__(self, url: str, timeout_s: float, sampling: SamplingCfg) -> None:
        self._url = url.rstrip("/")
        self._sampling = sampling
        self._http = httpx.Client(timeout=timeout_s)

    @property
    def url(self) -> str:
        return self._url

    def health(self) -> str | None:
        """None when healthy, else a short reason."""
        try:
            response = self._http.get(self._url + HEALTH_PATH, timeout=10)
        except httpx.HTTPError as error:
            return f"cannot reach {self._url}: {error}"
        if response.status_code >= HTTP_CLIENT_ERROR:
            return f"{self._url}{HEALTH_PATH} returned {response.status_code}"
        return None

    def speak(self, text: str, voice: Voice | None, max_new_tokens: int | None = None) -> bytes:
        """Synthesize text, cloning voice when given. Returns WAV bytes."""
        payload = self._payload(text, voice, max_new_tokens)
        try:
            response = self._http.post(self._url + SPEECH_PATH, json=payload)
        except httpx.HTTPError as error:
            raise TtsUnavailable(f"{type(error).__name__}: {error}") from error

        if response.status_code >= HTTP_SERVER_ERROR:
            raise TtsUnavailable(f"HTTP {response.status_code}: {response.text[:300]}")
        if response.status_code >= HTTP_CLIENT_ERROR:
            raise TtsRejected(f"HTTP {response.status_code}: {response.text[:500]}")
        if response.headers.get(FINISH_HEADER) == FINISH_LENGTH:
            raise TtsTruncated(f"audio hit max_new_tokens={payload['max_new_tokens']}")
        return response.content

    def close(self) -> None:
        self._http.close()

    def _payload(self, text: str, voice: Voice | None, max_new_tokens: int | None) -> dict:
        payload: dict = {
            "input": text,
            "response_format": "wav",
            "temperature": self._sampling.temperature,
            "top_p": self._sampling.top_p,
            "max_new_tokens": max_new_tokens or self._sampling.max_new_tokens,
        }
        if self._sampling.top_k is not None:
            payload["top_k"] = self._sampling.top_k
        if voice is not None:
            payload["references"] = [{
                "data": base64.b64encode(voice.wav).decode("ascii"),
                "media_type": WAV_MEDIA,
                "text": voice.transcript,
            }]
        return payload
