"""Casting Studio API through httpx, voice-bank uploads and approvals, book analysis."""

import base64
import subprocess
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from storyaudio.book import Book, Chapter
from storyaudio.config import Config, ServerCfg
from storyaudio.pipeline import Overwrite, Pipeline, workspace
from storyaudio.script.analysis import Fit, Person, analyze_book
from storyaudio.studio.server import TOKEN_HEADER, Studio
from storyaudio.tts.voices import Approval, VoiceBank, VoiceError


def _tone(path: Path, seconds: float) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=220:duration={seconds}", str(path)],
        check=True,
    )
    return path


@pytest.fixture
def studio(mock_server, epub_book: Path, tmp_path: Path):
    cfg = replace(Config(), server=ServerCfg(url=mock_server.url, retries=0))
    pipeline = Pipeline(cfg, base=tmp_path)
    pipeline.draft(epub_book, Overwrite.KEEP)
    server = Studio(pipeline, epub_book).start()
    client = httpx.Client(base_url=server.base_url, headers={TOKEN_HEADER: server.token}, timeout=30)
    yield server, client, workspace(epub_book, tmp_path)
    client.close()
    server.stop()


# ---------------------------------------------------------------- studio API


def test_page_and_api_need_the_token(studio):
    server, client, _ws = studio

    assert httpx.get(server.base_url + "/").status_code == 403
    page = httpx.get(server.url)
    assert page.status_code == 200 and server.token in page.text and "__STUDIO_TOKEN__" not in page.text

    assert httpx.get(server.base_url + "/api/state").status_code == 403
    assert client.get("/api/state").status_code == 200


def test_foreign_host_header_is_refused(studio):
    server, client, _ws = studio

    response = client.get("/api/state", headers={"Host": "evil.example:80"})
    assert response.status_code == 403


def test_state_lists_cast_with_line_counts(studio):
    _server, client, _ws = studio
    state = client.get("/api/state").json()

    cast = {m["id"]: m for m in state["cast"]}
    assert set(cast) == {"NARRATOR", "OLIVER"}
    assert cast["OLIVER"]["lines"] == 1 and not cast["OLIVER"]["clip"]["exists"]
    assert state["server"]["problem"] is None
    assert state["ready"] is False
    assert "00-cast.md" in state["files"]


def test_upload_play_preview_approve(studio, tmp_path: Path):
    _server, client, ws = studio
    clip = _tone(tmp_path / "oliver.mp3", 4)
    body = {"data": "data:audio/mpeg;base64," + base64.b64encode(clip.read_bytes()).decode(), "filename": "oliver.mp3", "transcript": "  Morning,\n Peggy.  "}

    state = client.post("/api/voice/OLIVER/clip", json=body).json()
    oliver = next(m for m in state["cast"] if m["id"] == "OLIVER")
    assert oliver["clip"]["exists"] and oliver["clip"]["seconds"] == pytest.approx(4, abs=0.1)
    assert oliver["clip"]["transcript"] == "Morning, Peggy."
    assert (ws.voices_dir / "OLIVER.wav").exists()

    assert client.get("/api/voice/OLIVER/clip").content.startswith(b"RIFF")
    assert client.post("/api/voice/OLIVER/preview", json={"text": "[sighs] Hello."}).content.startswith(b"RIFF")

    state = client.post("/api/voice/OLIVER/approve", json={"approval": "approved"}).json()
    assert next(m for m in state["cast"] if m["id"] == "OLIVER")["approved"]

    state = client.post("/api/voice/OLIVER/transcript", json={"text": "Good morning, Peggy."}).json()
    assert not next(m for m in state["cast"] if m["id"] == "OLIVER")["approved"]


def test_seed_then_ready_after_all_approved(studio):
    _server, client, _ws = studio
    for speaker in ("NARRATOR", "OLIVER"):
        client.post(f"/api/voice/{speaker}/seed", json={})
        state = client.post(f"/api/voice/{speaker}/approve", json={"approval": "approved"}).json()

    assert state["ready"] is True


def test_long_upload_is_refused_with_reason(studio, tmp_path: Path):
    _server, client, _ws = studio
    clip = _tone(tmp_path / "long.wav", 61)
    body = {"data": base64.b64encode(clip.read_bytes()).decode(), "filename": "long.wav", "transcript": "words"}

    response = client.post("/api/voice/OLIVER/clip", json=body)
    assert response.status_code == 400
    assert "60 s" in response.json()["error"]


def test_cast_edit_and_add(studio):
    _server, client, ws = studio
    client.post("/api/cast", json={"id": "OLIVER", "name": "Oliver Holyoake", "voice": "young vicar, kind, nervous"})
    state = client.post("/api/cast", json={"id": "mrs_hudson", "name": "Mrs Hudson", "voice": "elderly, dry"}).json()

    cast = {m["id"]: m for m in state["cast"]}
    assert cast["OLIVER"]["voice"] == "young vicar, kind, nervous"
    assert "MRS_HUDSON" in cast
    assert "| MRS_HUDSON | Mrs Hudson | elderly, dry |" in (ws.script_dir / "00-cast.md").read_text(encoding="utf-8")

    bad = client.post("/api/cast", json={"id": "X", "name": "a|b", "voice": "v"})
    assert bad.status_code == 400 and "|" in bad.json()["error"]


def test_script_read_write_check(studio):
    _server, client, ws = studio
    name = "02-the-visit.md"
    text = client.get(f"/api/script/{name}").json()["text"]

    changed = text.replace("NARRATOR: She sighed.", "OLIVER: She sighed.")
    assert client.put(f"/api/script/{name}", json={"text": changed}).json()["saved"]
    assert "OLIVER: She sighed." in (ws.script_dir / name).read_text(encoding="utf-8")

    client.put(f"/api/script/{name}", json={"text": changed + "\nGHOST: boo\n"})
    result = client.post("/api/check", json={}).json()
    assert any(i["severity"] == "error" and "GHOST" in i["message"] for i in result["issues"])
    assert result["estimate"]["requests"] > 0


def test_script_paths_cannot_escape(studio):
    _server, client, _ws = studio

    for name in ("..%2Fbook.json", "new.md", "book.json"):
        assert client.get(f"/api/script/{name}").status_code in (400, 404)


def test_finish_sets_event(studio):
    server, client, _ws = studio
    client.post("/api/finish", json={})

    assert server.finished.is_set()


# ---------------------------------------------------------------- voice bank


def test_store_converts_and_drops_other_formats(tmp_path: Path):
    bank = VoiceBank(tmp_path / "book", tmp_path / "global")
    (tmp_path / "book").mkdir()
    (tmp_path / "book" / "PEGGY.mp3").write_bytes(b"old")

    status = bank.store("PEGGY", _tone(tmp_path / "in.ogg", 5), "Hello there.")

    assert status.path == tmp_path / "book" / "PEGGY.wav" and not status.error
    assert not (tmp_path / "book" / "PEGGY.mp3").exists()
    with pytest.raises(VoiceError, match="transcript is empty"):
        bank.store("PEGGY", tmp_path / "in.ogg", "   ")
    with pytest.raises(VoiceError, match="use one of"):
        bank.store("PEGGY", tmp_path / "in.txt", "x")


def test_global_clip_transcript_edit_stays_in_book(tmp_path: Path):
    bank = VoiceBank(tmp_path / "book", tmp_path / "global")
    (tmp_path / "global").mkdir()
    _tone(tmp_path / "global" / "NARRATOR.wav", 5)
    (tmp_path / "global" / "NARRATOR.txt").write_text("Original words.", encoding="utf-8")

    bank.set_transcript("NARRATOR", "Better words.")

    assert (tmp_path / "global" / "NARRATOR.txt").read_text(encoding="utf-8") == "Original words."
    assert bank.transcript("NARRATOR") == "Better words."
    bank.approve("NARRATOR", Approval.APPROVED)
    assert bank.approved("NARRATOR")
    bank.approve("NARRATOR", Approval.REVOKED)
    assert not bank.approved("NARRATOR")


# ---------------------------------------------------------------- analysis


def _book(paragraphs: list[str]) -> Book:
    return Book("T", "A", "en", "t.txt", [Chapter("One", paragraphs), Chapter("Contents", ["One", "Two"])])


def test_analysis_finds_speakers_dialogue_and_front_matter():
    talk = ['"Run now, before the tide comes in," said Peggy.', '"Why would the tide matter to us?" asked Oliver.', 'Peggy pointed at the door.'] * 9
    a = analyze_book(_book(talk))

    assert [s.id for s in a.speakers] == ["PEGGY", "OLIVER"]
    assert a.speakers[0].lines == 9
    assert a.dialogue_share > 0.25
    assert a.front_matter == [2]
    assert a.person is Person.THIRD
    assert a.forms[0].id == "full-cast" and a.forms[0].fit is Fit.RECOMMENDED


def test_analysis_first_person_puts_protagonist_first():
    text = ["I walked to my door and I opened it. My hands shook.", "We had never been here, and I was afraid."] * 20
    a = analyze_book(_book(text))

    assert a.person is Person.FIRST
    assert a.forms[0].id == "protagonist-narrates"
    assert {f.id for f in a.forms} >= {"single-narrator", "conversation"}
