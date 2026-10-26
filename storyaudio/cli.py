"""Command line: `storyaudio <command> <book>` (or `python -m storyaudio`)."""

from __future__ import annotations

import argparse
import importlib.util
import sys
import threading
import webbrowser
from dataclasses import replace
from enum import Enum
from pathlib import Path

from storyaudio.audio import ffmpeg
from storyaudio.config import Config, load_config
from storyaudio.engine.hardware import HardwareError
from storyaudio.engine.manager import Engine
from storyaudio.ingest import SUPPORTED
from storyaudio.pipeline import Gate, Overwrite, Pipeline, workspace
from storyaudio.script.validator import Severity, has_errors
from storyaudio.tts.mlx_server import DEFAULT_HOST, DEFAULT_MODEL, MlxDrama3
from storyaudio.tts.voices import Approval, Seed

EXIT_OK = 0
EXIT_FAIL = 1
ALL = "ALL"
SERVER_ACTIONS = ("plan", "install", "start", "stop", "status", "logs")


class Target(Enum):
    """Whether a command takes a book argument."""

    BOOK = "book"
    NONE = "none"


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")

    args = _parser().parse_args(argv)
    cfg = load_config(args.config)
    if getattr(args, "server", None):
        cfg = replace(cfg, server=replace(cfg.server, url=args.server))

    try:
        return args.run(args, cfg)
    except KeyboardInterrupt:
        print("\nstopped; cached lines are kept, re-run to resume", file=sys.stderr)
        return EXIT_FAIL
    except Exception as error:  # top level: one readable line, not a traceback
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAIL


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="storyaudio", description="Book (EPUB/PDF/TXT) to narrated MP3 with Fish Audio Drama 3.")
    parser.add_argument("--config", type=Path, default=None, help="settings file (default: ./storyaudio.toml)")
    sub = parser.add_subparsers(required=True, metavar="command")

    def command(name: str, run, help_text: str, target: Target = Target.BOOK) -> argparse.ArgumentParser:
        cmd = sub.add_parser(name, help=help_text, description=help_text)
        if target is Target.BOOK:
            cmd.add_argument("book", type=Path, help=f"book file ({', '.join(SUPPORTED)}) or work/<id> folder")
        cmd.add_argument("--server", help="Drama 3 server URL (overrides config)")
        cmd.set_defaults(run=run)
        return cmd

    command("extract", _extract, "read the book into work/<id>/book.json and source.md")
    command("analyze", _analyze, "measure the book and rank story forms -> work/<id>/analysis.json")
    command("draft", _draft, "write a baseline script to work/<id>/script/").add_argument("--force", action="store_true", help="replace an existing script")
    command("check", _check, "validate the script and estimate length")

    voices = command("voices", _voices, "list voices; seed missing ones from cast descriptions")
    voices.add_argument("--regen", nargs="+", metavar="ID", default=[], help="regenerate these voices")
    voices.add_argument("--approve", nargs="+", metavar="ID", default=[], help=f"approve these clips ({ALL} = every clip)")
    voices.add_argument("--revoke", nargs="+", metavar="ID", default=[], help="withdraw approval")

    studio = command("studio", _studio, "open the Casting Studio in the browser; blocks until Finish")
    studio.add_argument("--port", type=int, default=0, help="port on 127.0.0.1 (default: any free port)")
    studio.add_argument("--no-browser", action="store_true", help="print the link only")

    render = command("render", _render, "synthesize and build MP3 + SRT + script into output/<id>/")
    render.add_argument("--unapproved", action="store_true", help="allow voices nobody approved (test runs)")
    render.add_argument("--chapters", nargs="+", type=int, metavar="N", default=[], help="proof: render only these chapter numbers into output/<id>/proof/")
    make = command("make", _make, "extract, draft if needed, check, voices, render")
    make.add_argument("--unapproved", action="store_true", help="allow voices nobody approved (test runs)")

    server = command("server", _server, "local Drama 3 engine: " + " | ".join(SERVER_ACTIONS), Target.NONE)
    server.add_argument("action", choices=SERVER_ACTIONS)
    server.add_argument("--lines", type=int, default=60, help="log lines to show (logs)")

    command("doctor", _doctor, "check ffmpeg, GPU, engine and the Drama 3 server", Target.NONE)
    mock = command("mock-server", _mock, "run a fake Drama 3 server (tones) for testing", Target.NONE)
    mock.add_argument("--port", type=int, default=8000)
    mlx = command("mlx-server", _mlx, "run Drama 3 through MLX on an Apple Silicon Mac", Target.NONE)
    mlx.add_argument("--model", default=DEFAULT_MODEL, help=f"MLX checkpoint (default: {DEFAULT_MODEL})")
    mlx.add_argument("--host", default=DEFAULT_HOST, help=f"bind address (default: {DEFAULT_HOST})")
    mlx.add_argument("--port", type=int, default=8000)
    return parser


# ---------------------------------------------------------------- commands


def _extract(args: argparse.Namespace, cfg: Config) -> int:
    ws, book = Pipeline(cfg).extract(args.book)
    print(f"{book.title} | {book.author} | {len(book.chapters)} chapters | {book.word_count():,} words")
    for index, chapter in enumerate(book.chapters, start=1):
        words = sum(len(p.split()) for p in chapter.paragraphs)
        print(f"  {index:02d}  {words:6,}  {chapter.title}")
    print(f"-> {ws.book_json}\n-> {ws.source_md}")
    return EXIT_OK


def _analyze(args: argparse.Namespace, cfg: Config) -> int:
    ws, a = Pipeline(cfg).analyze(args.book)
    print(f"{a.title} | {a.author} | {a.words:,} words | ~{a.hours:.1f} h | {len(a.chapters)} chapters")
    print(f"dialogue {a.dialogue_share:.0%} | narration {a.person.value} person ({a.first_person_share:.1%} I/me/my)")
    if a.front_matter:
        print(f"short chapters (front/back matter?): {', '.join(str(n) for n in a.front_matter)}")

    print("named speakers (lines, words):")
    for speaker in a.speakers[:15]:
        print(f"  {speaker.id:24} {speaker.lines:4} {speaker.words:6,}  {speaker.name}")
    if not a.speakers:
        print("  none found by 'said X' patterns; read the text for who speaks")

    print("story forms, best fit first:")
    for form in a.forms:
        print(f"  [{form.fit.value:11}] {form.name} ({form.voices} voice{'s' if form.voices != 1 else ''}, {form.mode}): {form.why}")
    print(f"-> {ws.analysis_json}")
    return EXIT_OK


def _draft(args: argparse.Namespace, cfg: Config) -> int:
    paths = Pipeline(cfg).draft(args.book, Overwrite.REPLACE if args.force else Overwrite.KEEP)
    print(f"wrote {len(paths)} files to {paths[0].parent}")
    return EXIT_OK


def _check(args: argparse.Namespace, cfg: Config) -> int:
    issues, estimate = Pipeline(cfg).check(args.book)
    for issue in sorted(issues, key=lambda i: i.severity is not Severity.ERROR):
        print(issue)

    errors = sum(i.severity is Severity.ERROR for i in issues)
    print(f"{errors} errors, {len(issues) - errors} warnings")
    print(f"{estimate.jobs} TTS requests | {estimate.chars:,} chars | ~{estimate.seconds / 3600:.1f} h | speakers: {', '.join(estimate.speakers)}")
    return EXIT_FAIL if has_errors(issues) else EXIT_OK


def _voices(args: argparse.Namespace, cfg: Config) -> int:
    pipeline = Pipeline(cfg, notice=_say)
    if args.approve or args.revoke:
        return _approvals(pipeline, args)

    seed = Seed.FORCE if args.regen else Seed.MISSING
    statuses = pipeline.voices(args.book, seed, [r.upper() for r in args.regen])
    approved = {view.member.id for view in pipeline.cast_view(args.book) if view.approved}
    bad = 0
    for status in statuses:
        note = status.error or status.warning or "ok"
        bad += bool(status.error)
        mark = "approved" if status.speaker in approved else "-"
        print(f"  {status.speaker:20} {status.seconds:5.1f}s  {mark:8}  {note:30} {status.path or ''}")
    print("listen to each clip; approve in `storyaudio studio`, or replace a clip (keep the .txt transcript exact), or --regen ID")
    return EXIT_FAIL if bad else EXIT_OK


def _approvals(pipeline: Pipeline, args: argparse.Namespace) -> int:
    views = pipeline.cast_view(args.book)
    for ids, approval in ((args.approve, Approval.APPROVED), (args.revoke, Approval.REVOKED)):
        wanted = [i.upper() for i in ids]
        if ALL in wanted:
            wanted = [v.member.id for v in views if v.status.path is not None and not v.status.error]
        pipeline.approve(args.book, wanted, approval)
        if wanted:
            print(f"{approval.value}: {', '.join(wanted)}")
    return EXIT_OK


def _studio(args: argparse.Namespace, cfg: Config) -> int:
    from storyaudio.studio.server import Studio

    pipeline = Pipeline(cfg, notice=_say)
    ws = workspace(args.book)
    if not (ws.script_dir / "00-cast.md").exists():
        pipeline.draft(args.book, Overwrite.KEEP)
        _say(f"drafted a baseline script in {ws.script_dir}")
    _warm_engine(cfg, pipeline)

    studio = Studio(pipeline, args.book, args.port).start()
    _say(f"Casting Studio: {studio.url}")
    _say("waiting for Finish in the browser (Ctrl+C to stop)")
    if not args.no_browser:
        webbrowser.open(studio.url)
    try:
        studio.wait()
    except KeyboardInterrupt:
        print("studio closed from the terminal")
    finally:
        studio.stop()

    views = pipeline.cast_view(args.book)
    for view in views:
        state = "approved" if view.approved else ("no clip" if view.status.path is None else "NOT approved")
        print(f"  {view.member.id:20} {state:12} {view.lines:5} lines  {view.member.voice}")
    waiting = [v.member.id for v in views if v.lines and not v.approved]
    print(f"not approved: {', '.join(waiting)}" if waiting else "all speaking voices approved")
    return EXIT_OK


def _warm_engine(cfg: Config, pipeline: Pipeline) -> None:
    """Start an installed engine in the background so previews work soon."""
    engine = Engine(cfg)
    if engine.installed() is None or pipeline.server_problem() is None:
        return

    def start() -> None:
        try:
            engine.start(lambda line: None)
            _say("Drama 3 engine ready")
        except Exception as error:
            _say(f"engine did not start: {error}")

    _say("starting the Drama 3 engine in the background; previews work when it is ready")
    threading.Thread(target=start, daemon=True).start()


def _render(args: argparse.Namespace, cfg: Config) -> int:
    gate = Gate.ANY if args.unapproved else Gate.APPROVED
    result = Pipeline(cfg, notice=_say).render(args.book, _progress, gate, getattr(args, "chapters", None))
    print()
    for part in result.parts:
        print(f"  {part.mp3}  {part.seconds / 60:6.1f} min  ({len(part.chapters)} chapters)")
        print(f"  {part.srt}")
    print(f"  {result.script}\n  {result.manifest}")
    return EXIT_OK


def _make(args: argparse.Namespace, cfg: Config) -> int:
    pipeline = Pipeline(cfg)
    ws = workspace(args.book)
    if not ws.book_json.exists():
        _extract(args, cfg)
    if not ws.script_dir.exists():
        pipeline.draft(args.book, Overwrite.KEEP)
        print(f"drafted script in {ws.script_dir}")
    if _check(args, cfg) != EXIT_OK:
        return EXIT_FAIL
    args.regen, args.approve, args.revoke = [], [], []
    if _voices(args, cfg) != EXIT_OK:
        return EXIT_FAIL
    return _render(args, cfg)


def _server(args: argparse.Namespace, cfg: Config) -> int:
    engine = Engine(cfg)
    action = args.action

    if action == "plan":
        gpus, placement = engine.plan()
        for gpu in gpus:
            print(f"  {gpu}")
        print(f"plan: {placement.describe()}")
        print(f"serve flags: {' '.join(placement.serve_args())}")
    elif action == "install":
        installed = engine.install(_say)
        print(f"installed: {installed.checkpoint} via {installed.driver}; start with `storyaudio server start`")
    elif action == "start":
        engine.start(_say)
        print(f"Drama 3 ready at {engine.url}")
    elif action == "stop":
        engine.stop()
        print("stopped")
    elif action == "status":
        status = engine.status()
        print(f"installed: {status.installed.checkpoint + ' via ' + status.installed.driver if status.installed else 'no'}")
        print(f"process:   {status.state.value}")
        print(f"health:    {status.health or 'ok'} ({status.url})")
        return EXIT_OK if status.health is None else EXIT_FAIL
    else:
        print(engine.logs(args.lines))
    return EXIT_OK


def _doctor(args: argparse.Namespace, cfg: Config) -> int:
    ok = True
    try:
        ffmpeg.require()
        print("ok    ffmpeg + ffprobe on PATH")
    except ffmpeg.FfmpegError as error:
        ok = False
        print(f"FAIL  {error}")

    engine = Engine(cfg)
    try:
        _gpus, placement = engine.plan()
        print(f"ok    GPU can run {placement.describe()}")
    except HardwareError as error:
        print(f"info  local engine not possible: {error}")
    installed = engine.installed()
    print(f"{'ok' if installed else 'info':5} local engine {'installed: ' + installed.checkpoint + ' via ' + installed.driver if installed else 'not installed'}")
    if sys.platform == "darwin":
        _report_mlx()

    problem = Pipeline(cfg).server_problem()
    if problem:
        ok = False
        start = "storyaudio mlx-server" if sys.platform == "darwin" else "storyaudio server start"
        print(f"FAIL  Drama 3 server: {problem} (`{start}`, or docs/SERVER.md)")
    else:
        print(f"ok    Drama 3 server at {cfg.server.url}")
    return EXIT_OK if ok else EXIT_FAIL


def _report_mlx() -> None:
    """Mac route: mlx-audio, not CUDA. Says what to install before the first render."""
    if importlib.util.find_spec("mlx_audio") is not None:
        print("ok    mlx-audio installed (Apple Silicon path)")
        return
    print("info  mlx-audio not installed (`pip install mlx-audio` for `storyaudio mlx-server`)")


def _mock(args: argparse.Namespace, cfg: Config) -> int:
    from storyaudio.tts.mock_server import MockDrama3

    server = MockDrama3(port=args.port)
    print(f"mock Drama 3 on {server.url} (tones, not speech); Ctrl+C to stop")
    try:
        server.serve()
    except KeyboardInterrupt:
        pass
    return EXIT_OK


def _mlx(args: argparse.Namespace, cfg: Config) -> int:
    print(f"loading {args.model} (first run downloads it; 3-bit is about 18.5 GB)", flush=True)
    server = MlxDrama3(args.model, port=args.port, host=args.host)
    print(f"MLX Drama 3 on {server.url} | {server.model_id}")
    print(f"point the tool at it: storyaudio render BOOK --server {server.url}")
    print("one take at a time; keep [server] workers = 1; Ctrl+C to stop", flush=True)
    try:
        server.serve()
    except KeyboardInterrupt:
        pass
    return EXIT_OK


def _say(message: str) -> None:
    print(message, flush=True)


_last_percent = -1


def _progress(done: int, total: int, cached: bool) -> None:
    """One update per percent on a terminal; one line per 10% in logs."""
    global _last_percent
    percent = done * 100 // max(total, 1)
    step = 1 if sys.stdout.isatty() else 10
    if percent // step == _last_percent // step and done != total:
        return
    _last_percent = percent

    label = "cached" if cached else "rendered"
    end = "" if sys.stdout.isatty() else "\n"
    print(f"\r  {label} {done}/{total} lines ({percent}%)   ", end=end, flush=True)


if __name__ == "__main__":
    sys.exit(main())
