"""End to end: book file -> MP3 parts + SRT + script + manifest, via mock server."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from storyaudio.audio import ffmpeg
from storyaudio.cli import main
from storyaudio.config import Config, PartsCfg, ServerCfg, load_config
from storyaudio.pipeline import Gate, Overwrite, Pipeline, PipelineError, workspace
from storyaudio.script import fmt
from storyaudio.tts.voices import Approval, Seed


def _cfg(server, parts: PartsCfg = PartsCfg()) -> Config:
    return replace(Config(), server=ServerCfg(url=server.url, retries=0), parts=parts)


def test_full_pipeline(mock_server, epub_book: Path, tmp_path: Path):
    ffmpeg.require()
    pipeline = Pipeline(_cfg(mock_server, PartsCfg(target_s=8, hard_max_s=30)), base=tmp_path)

    ws, book = pipeline.extract(epub_book)
    pipeline.draft(epub_book, Overwrite.KEEP)
    issues, estimate = pipeline.check(epub_book)
    statuses = pipeline.voices(epub_book, Seed.MISSING, [])
    pipeline.approve(epub_book, ["NARRATOR", "OLIVER"], Approval.APPROVED)
    result = pipeline.render(epub_book)

    assert ws.source_md.exists()
    assert not [i for i in issues if i.severity.value == "error"]
    assert set(estimate.speakers) == {"NARRATOR", "OLIVER"}
    assert all(not s.error for s in statuses)

    manifest = json.loads(result.manifest.read_text(encoding="utf-8"))
    assert len(result.parts) == len(manifest["parts"]) == 2
    for part in result.parts:
        assert part.mp3.exists() and part.srt.exists()
        assert ffmpeg.duration(part.mp3) == pytest.approx(part.seconds, abs=0.2)
    assert "Morning, Peggy" in result.parts[1].srt.read_text(encoding="utf-8")
    assert "OLIVER:" in result.script.read_text(encoding="utf-8")


def test_render_refuses_script_with_errors(mock_server, epub_book: Path, tmp_path: Path):
    pipeline = Pipeline(_cfg(mock_server), base=tmp_path)
    pipeline.draft(epub_book, Overwrite.KEEP)
    ws = workspace(epub_book, tmp_path)
    chapter = next(p for p in ws.script_dir.glob("01-*.md"))
    chapter.write_text(chapter.read_text(encoding="utf-8").replace("rain", "snow"), encoding="utf-8")

    with pytest.raises(PipelineError, match="text changed"):
        pipeline.render(epub_book)


def test_rerender_after_edit_only_renders_changed_line(mock_server, epub_book: Path, tmp_path: Path):
    pipeline = Pipeline(_cfg(mock_server), base=tmp_path)
    pipeline.draft(epub_book, Overwrite.KEEP)
    pipeline.voices(epub_book, Seed.MISSING, [])
    pipeline.render(epub_book, gate=Gate.ANY)
    before = len(mock_server.requests)

    ws = workspace(epub_book, tmp_path)
    chapter = next(ws.script_dir.glob("02-*.md"))
    chapter.write_text(chapter.read_text(encoding="utf-8").replace("NARRATOR: She sighed.", "NARRATOR: She sighed. [sighs]"), encoding="utf-8")
    pipeline.render(epub_book, gate=Gate.ANY)

    assert len(mock_server.requests) == before + 1


def test_draft_does_not_overwrite_without_force(epub_book: Path, tmp_path: Path):
    pipeline = Pipeline(Config(), base=tmp_path)
    pipeline.draft(epub_book, Overwrite.KEEP)

    with pytest.raises(PipelineError, match="--force"):
        pipeline.draft(epub_book, Overwrite.KEEP)
    assert pipeline.draft(epub_book, Overwrite.REPLACE)


def test_server_down_is_a_clear_error(epub_book: Path, tmp_path: Path):
    cfg = replace(Config(), server=ServerCfg(url="http://127.0.0.1:9"))
    pipeline = Pipeline(cfg, base=tmp_path)
    pipeline.draft(epub_book, Overwrite.KEEP)

    with pytest.raises(PipelineError, match="not ready"):
        pipeline.voices(epub_book, Seed.MISSING, [])


def test_script_files_follow_naming(epub_book: Path, tmp_path: Path):
    paths = Pipeline(Config(), base=tmp_path).draft(epub_book, Overwrite.KEEP)

    assert [p.name for p in paths] == [fmt.CAST_FILE, "01-prologue.md", "02-the-visit.md"]


def test_cli_check_exit_codes(epub_book: Path, tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["draft", str(epub_book)]) == 0
    assert main(["check", str(epub_book)]) == 0
    assert "TTS requests" in capsys.readouterr().out

    chapter = next((tmp_path / "work" / "tiny-book" / "script").glob("01-*.md"))
    chapter.write_text(chapter.read_text(encoding="utf-8") + "\nGHOST: boo\n", encoding="utf-8")
    assert main(["check", str(epub_book)]) == 1


def test_config_rejects_unknown_keys(tmp_path: Path):
    path = tmp_path / "c.toml"
    path.write_text('[server]\nurll = "x"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="urll"):
        load_config(path)

    path.write_text('[script]\ntag_policy = "events"\n', encoding="utf-8")
    assert load_config(path).script.tag_policy.value == "events"


def test_render_requires_approved_voices(mock_server, epub_book: Path, tmp_path: Path):
    pipeline = Pipeline(_cfg(mock_server), base=tmp_path)
    pipeline.draft(epub_book, Overwrite.KEEP)
    pipeline.voices(epub_book, Seed.MISSING, [])

    with pytest.raises(PipelineError, match="not approved: NARRATOR, OLIVER"):
        pipeline.render(epub_book)

    pipeline.approve(epub_book, ["NARRATOR", "OLIVER"], Approval.APPROVED)
    pipeline.set_transcript(epub_book, "OLIVER", "Some other words.")   # a changed clip needs a new approval
    with pytest.raises(PipelineError, match="not approved: OLIVER"):
        pipeline.render(epub_book)


def test_cli_approve_all_and_analyze(mock_server, epub_book: Path, tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["draft", str(epub_book)]) == 0
    assert main(["voices", str(epub_book), "--server", mock_server.url]) == 0
    assert main(["voices", str(epub_book), "--approve", "all"]) == 0
    assert "approved: NARRATOR, OLIVER" in capsys.readouterr().out

    assert main(["analyze", str(epub_book)]) == 0
    out = capsys.readouterr().out
    assert "story forms" in out and "OLIVER" in out
    assert (tmp_path / "work" / "tiny-book" / "analysis.json").exists()


def test_autostart_only_for_installed_local_engine(epub_book: Path, tmp_path: Path, monkeypatch):
    started = []
    monkeypatch.setattr("storyaudio.pipeline.Engine.start", lambda self, notice: started.append(True))
    cfg = replace(Config(), server=ServerCfg(url="http://127.0.0.1:8000", retries=0))
    pipeline = Pipeline(cfg, base=tmp_path)
    pipeline.draft(epub_book, Overwrite.KEEP)

    with pytest.raises(PipelineError, match="server install"):
        pipeline.voices(epub_book, Seed.MISSING, [])
    assert started == []

    (tmp_path / "engine").mkdir()
    (tmp_path / "engine" / "state.json").write_text(
        '{"driver": "docker", "checkpoint": "fishaudio/drama-3-int4", "serve_args": [], "commit": "x", "port": 8000}', encoding="utf-8")
    with pytest.raises(PipelineError, match="not ready"):
        pipeline.voices(epub_book, Seed.MISSING, [])
    assert started == [True]


def test_proof_render_reuses_cache_for_full_render(mock_server, epub_book: Path, tmp_path: Path):
    pipeline = Pipeline(_cfg(mock_server), base=tmp_path)
    pipeline.draft(epub_book, Overwrite.KEEP)
    pipeline.voices(epub_book, Seed.MISSING, [])

    proof = pipeline.render(epub_book, gate=Gate.ANY, chapters=[2])
    proof_requests = len(mock_server.requests)
    assert proof.manifest.parent.name == "proof"
    assert [c for p in proof.parts for c in p.chapters] == ["The Visit"]

    pipeline.render(epub_book, gate=Gate.ANY)
    new_inputs = [r["input"] for r in mock_server.requests[proof_requests:]]
    assert len(new_inputs) == 3   # prologue: title, paragraph, closing line
    assert not any("Morning" in text for text in new_inputs)   # chapter 2 came from the cache

    with pytest.raises(PipelineError, match=r"\[9\]"):
        pipeline.render(epub_book, gate=Gate.ANY, chapters=[9])
