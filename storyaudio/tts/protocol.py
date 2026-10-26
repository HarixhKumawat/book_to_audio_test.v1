"""The Drama 3 HTTP request contract, shared by every server that speaks it.

sglang-omni's Drama 3 preprocessor accepts one voice per request, refuses the
structural tokens its prompt builder writes, and caps a reference clip at 60 s.
The CUDA server, the MLX bridge and the mock server all check the same rules
here, so a request that works against one works against the others.
"""

from __future__ import annotations

import base64
import io
import re
import wave

MAX_REFERENCE_S = 60.0
STRUCTURAL_TOKENS = ("<|im_start|>", "<|im_end|>", "<|voice|>", "<|text|>", "<|interleave|>", "<|semantic:")
_SPEAKER = re.compile(r"<\|speaker:(\d+)\|>")


def problem(body: dict) -> str | None:
    """None when the request is valid, else the server's own error message."""
    text = body.get("input")
    if not isinstance(text, str) or not text.strip():
        return "Drama 3 request: input: text must not be empty"

    for token in STRUCTURAL_TOKENS:
        if token in text:
            return f"Drama 3 request: input: {token} is written by the prompt builder"

    speakers = sorted({int(n) for n in _SPEAKER.findall(text)} - {0})
    if speakers:
        return f"Drama 3 request: only speaker 0 is supported; got speaker tags {speakers}"

    for index, reference in enumerate(body.get("references") or []):
        try:
            seconds = wav_seconds(base64.b64decode(reference["data"]))
        except Exception as error:
            return f"Drama 3 request: references.{index}: {error}"
        if seconds > MAX_REFERENCE_S:
            return f"Drama 3 request: references.{index}: reference audio lasts {seconds:.1f} s, longer than the {MAX_REFERENCE_S:.0f} s limit"
    return None


def wav_seconds(data: bytes) -> float:
    with wave.open(io.BytesIO(data), "rb") as wav:
        return wav.getnframes() / wav.getframerate()
