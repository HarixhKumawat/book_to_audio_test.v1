"""Casting Studio: a local web page to set voices and edit the script.

    browser ──HTTP+token──▶ Studio (this file, UI layer) ──▶ Pipeline (service)

Private by design: binds 127.0.0.1 only, every API call carries the session
token (header X-Studio-Token), and the Host header must be this server, so
other web pages cannot drive it (CSRF, DNS rebinding).

    GET  /?t=<token>                       the page
    GET  /api/state                        book, cast, voices, files, server
    POST /api/cast                         {id, name, voice}: add or edit
    GET  /api/voice/<ID>/clip              clip as WAV
    POST /api/voice/<ID>/clip              {data: base64, filename, transcript}
    POST /api/voice/<ID>/transcript        {text}
    POST /api/voice/<ID>/seed              new clip from the voice description
    POST /api/voice/<ID>/preview           {text} -> WAV
    POST /api/voice/<ID>/approve           {approval: approved | revoked}
    GET  /api/script/<file>                {name, text}
    PUT  /api/script/<file>                {text}
    POST /api/check                        issues + estimate
    POST /api/finish                       close the studio
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import secrets
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from storyaudio.pipeline import CastView, Pipeline
from storyaudio.script.model import CastMember
from storyaudio.tts.voices import Approval, Seed

HOST = "127.0.0.1"
TOKEN_HEADER = "X-Studio-Token"
MAX_BODY_BYTES = 80 * 1024 * 1024    # a few minutes of uncompressed WAV
JSON_TYPE = "application/json; charset=utf-8"
WAV_TYPE = "audio/wav"
HTML_TYPE = "text/html; charset=utf-8"
TOKEN_SLOT = "__STUDIO_TOKEN__"

_ROUTE = re.compile(r"^/api/(?P<area>voice|script)/(?P<key>[^/]+)(?:/(?P<action>[a-z]+))?$")


class StudioError(ValueError):
    """A request the user can fix: shown in the page, status 400."""


@dataclass(frozen=True)
class Reply:
    status: int
    body: bytes
    content_type: str



class Studio:
    def __init__(self, pipeline: Pipeline, book: Path, port: int = 0) -> None:
        self.token = secrets.token_urlsafe(18)
        self.finished = threading.Event()
        self._pipeline = pipeline
        self._book = book
        self._page = resources.files("storyaudio.studio").joinpath("page.html").read_text(encoding="utf-8")
        self._httpd = ThreadingHTTPServer((HOST, port), _handler_class(self))
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://{HOST}:{self._httpd.server_address[1]}"

    @property
    def url(self) -> str:
        return f"{self.base_url}/?t={self.token}"

    def start(self) -> Studio:
        self._thread.start()
        return self

    def wait(self) -> None:
        """Block until the user presses Finish (or Ctrl+C in the terminal)."""
        while not self.finished.wait(0.5):
            pass

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    # ------------------------------------------------------------ routing

    def handle(self, method: str, path: str, query: dict, body: bytes) -> Reply:
        if path == "/" and method == "GET":
            if query.get("t", [""])[0] != self.token:
                return _text(HTTPStatus.FORBIDDEN, "Open the studio with the link printed in the terminal.")
            return Reply(HTTPStatus.OK, self._page.replace(TOKEN_SLOT, self.token).encode(), HTML_TYPE)

        payload = _json_body(body) if body else {}
        if path == "/api/state" and method == "GET":
            return _json(self._state())
        if path == "/api/cast" and method == "POST":
            return self._save_member(payload)
        if path == "/api/check" and method == "POST":
            return self._check()
        if path == "/api/finish" and method == "POST":
            self.finished.set()
            return _json({"ok": True})

        match = _ROUTE.match(path)
        if match is None:
            return _text(HTTPStatus.NOT_FOUND, "no such endpoint")
        if match["area"] == "script":
            return self._script(method, match["key"], payload)
        return self._voice(method, match["key"], match["action"] or "", payload)

    def _voice(self, method: str, speaker: str, action: str, payload: dict) -> Reply:
        book = self._book
        pipeline = self._pipeline
        if (method, action) == ("GET", "clip"):
            return Reply(HTTPStatus.OK, pipeline.clip_wav(book, speaker), WAV_TYPE)
        if method != "POST":
            return _text(HTTPStatus.METHOD_NOT_ALLOWED, "use POST")

        if action == "clip":
            data = _base64(payload.get("data", ""))
            suffix = Path(str(payload.get("filename", ""))).suffix or ".webm"
            pipeline.store_clip(book, speaker, data, suffix, str(payload.get("transcript", "")))
        elif action == "transcript":
            pipeline.set_transcript(book, speaker, str(payload.get("text", "")))
        elif action == "seed":
            pipeline.voices(book, Seed.FORCE, [speaker])
        elif action == "preview":
            return Reply(HTTPStatus.OK, pipeline.preview(book, speaker, str(payload.get("text", ""))), WAV_TYPE)
        elif action == "approve":
            pipeline.approve(book, [speaker], Approval(payload.get("approval", Approval.APPROVED.value)))
        else:
            return _text(HTTPStatus.NOT_FOUND, f"unknown voice action {action!r}")
        return _json(self._state())

    def _script(self, method: str, name: str, payload: dict) -> Reply:
        if method == "GET":
            return _json({"name": name, "text": self._pipeline.read_file(self._book, name)})
        if method == "PUT":
            self._pipeline.write_file(self._book, name, str(payload.get("text", "")))
            return _json({"name": name, "saved": True})
        return _text(HTTPStatus.METHOD_NOT_ALLOWED, "use GET or PUT")

    def _save_member(self, payload: dict) -> Reply:
        member = CastMember(str(payload.get("id", "")).strip().upper(), str(payload.get("name", "")).strip(), str(payload.get("voice", "")).strip())
        self._pipeline.save_member(self._book, member)
        return _json(self._state())

    def _check(self) -> Reply:
        issues, estimate = self._pipeline.check(self._book)
        return _json({
            "issues": [{"severity": i.severity.value, "where": i.where, "message": i.message} for i in issues],
            "estimate": {"requests": estimate.jobs, "chars": estimate.chars, "hours": round(estimate.seconds / 3600, 2), "speakers": estimate.speakers},
        })

    def _state(self) -> dict:
        cast = self._pipeline.cast_view(self._book)
        files = self._pipeline.script_files(self._book)
        return {
            "book": str(self._book),
            "cast": [_cast_json(view) for view in cast],
            "files": files,
            "proposal": self._pipeline.proposal(self._book),
            "server": {"problem": self._pipeline.server_problem()},
            "ready": all(view.approved for view in cast if view.lines),
        }


def _cast_json(view: CastView) -> dict:
    status = view.status
    return {
        "id": view.member.id,
        "name": view.member.name,
        "voice": view.member.voice,
        "lines": view.lines,
        "words": view.words,
        "clip": {
            "exists": status.path is not None,
            "seconds": round(status.seconds, 1),
            "error": status.error,
            "warning": status.warning,
            "origin": view.origin.value if view.origin else None,
            "transcript": view.transcript,
        },
        "approved": view.approved,
    }


# ---------------------------------------------------------------- HTTP plumbing


def _handler_class(studio: Studio) -> type[BaseHTTPRequestHandler]:
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self._serve("GET")

        def do_POST(self) -> None:
            self._serve("POST")

        def do_PUT(self) -> None:
            self._serve("PUT")

        def log_message(self, format: str, *args: object) -> None:
            pass   # only the CLI prints

        def _serve(self, method: str) -> None:
            url = urlparse(self.path)
            reply = self._guard(url.path)
            if reply is None:
                reply = self._dispatch(method, url.path, parse_qs(url.query))
            self.send_response(reply.status)
            self.send_header("Content-Type", reply.content_type)
            self.send_header("Content-Length", str(len(reply.body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(reply.body)

        def _guard(self, path: str) -> Reply | None:
            """Host check on every request; token check on every API call."""
            port = self.server.server_address[1]
            if self.headers.get("Host") not in {f"{HOST}:{port}", f"localhost:{port}"}:
                return _text(HTTPStatus.FORBIDDEN, "wrong host")
            if path.startswith("/api/") and not secrets.compare_digest(self.headers.get(TOKEN_HEADER, ""), studio.token):
                return _text(HTTPStatus.FORBIDDEN, "missing or wrong studio token")
            return None

        def _dispatch(self, method: str, path: str, query: dict) -> Reply:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                return _error(f"upload over {MAX_BODY_BYTES // (1024 * 1024)} MB")
            body = self.rfile.read(length) if length else b""
            try:
                return studio.handle(method, path, query, body)
            except Exception as error:   # shown in the page; the studio keeps running
                return _error(str(error))

    return _Handler


def _json_body(body: bytes) -> dict:
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise StudioError(f"bad JSON: {error}") from error
    if not isinstance(payload, dict):
        raise StudioError("JSON body must be an object")
    return payload


def _base64(text: str) -> bytes:
    # Data URLs from the browser carry a "data:audio/webm;base64," prefix.
    try:
        return base64.b64decode(text.split(",", 1)[-1], validate=True)
    except (binascii.Error, ValueError) as error:
        raise StudioError("clip data is not base64") from error


def _json(data: object) -> Reply:
    return Reply(HTTPStatus.OK, json.dumps(data, ensure_ascii=False).encode(), JSON_TYPE)


def _error(message: str) -> Reply:
    return Reply(HTTPStatus.BAD_REQUEST, json.dumps({"error": message}, ensure_ascii=False).encode(), JSON_TYPE)


def _text(status: int, message: str) -> Reply:
    return Reply(status, json.dumps({"error": message}).encode(), JSON_TYPE)
