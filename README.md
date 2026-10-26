# book_to_audio

Book in (EPUB, PDF, TXT). Audiobook out: MP3 part(s) with chapter
markers, SRT subtitles, and the performance script in Markdown. Voices
from Fish Audio Drama 3, run on your own GPU. An AI agent (Claude Code or
Codex) reads the book, proposes how to tell it, casts and directs it; you
choose the form and approve every voice in a local Casting Studio.

## Quick start

1. Unzip. Open the `book_to_audio` folder in Claude Code or Codex.
2. Put your book in `books/`.
3. Say: **"Make an audiobook of books/My Book.epub"**.

The agent sets up the tool, analyzes the book, proposes 2-4 ways to tell
it, and waits for your choice. Then it casts and directs the script, opens
the Casting Studio for your voices, installs and starts Drama 3 if needed,
renders, and checks the result.

## Needs

| | |
| --- | --- |
| Python | 3.11+ (`setup.ps1` / `setup.sh` make `.venv`) |
| ffmpeg | on PATH (`winget install Gyan.FFmpeg`, `apt install ffmpeg`, `brew install ffmpeg`) |
| GPU for Drama 3 | NVIDIA, compute 8.0+, ~32 GB (RTX 5090, A6000, L40S, H100) or two 24 GB cards. On Windows: Docker Desktop with WSL2. See [docs/SERVER.md](docs/SERVER.md). |
| or a Mac | M1 or later, 32 GB unified memory: `SA mlx-server` runs the same Drama 3 checkpoint through MLX. See [docs/SERVER.md](docs/SERVER.md#apple-silicon-m1-and-later). |

No big GPU here? Run the engine on another machine and point this one at
it (docs/SERVER.md). Everything else (analysis, script, studio) works
on any computer.

## How it works

```
 books/book.epub
      │ extract, analyze          work/<id>/book.json, source.md, analysis.json
      ▼
 agent reads, proposes ══▶ you choose a form          work/<id>/proposal.md
      │ draft, direct             work/<id>/script/*.md  (cast, who says what, tags)
      ▼
 Casting Studio ══▶ you set + approve voices          work/<id>/voices/
      │ render ── HTTP ──▶ Drama 3 (sglang-omni, local GPU, managed by `server`)
      ▼
 output/<id>/<Title> - Part 01.mp3 + .srt, <Title> - Script.md, manifest.json
```

## By hand

`SA` = `.venv\Scripts\storyaudio` (Windows) or `.venv/bin/storyaudio`.

```bash
SA doctor
SA extract  "books/My Book.epub"
SA analyze  "books/My Book.epub"     # forms ranked, speakers, dialogue share
SA draft    "books/My Book.epub"     # baseline script: narrator + named speakers
#  edit work/my-book/script/*.md      # cast, attribution, tags
SA check    "books/My Book.epub"     # 0 errors required
SA studio   "books/My Book.epub"     # voices: upload/record/generate, approve
SA server install && SA server start # NVIDIA route
SA mlx-server                        # Apple Silicon route (separate terminal)
SA render   "books/My Book.epub" --chapters 2   # proof
SA render   "books/My Book.epub"
```

## Output rules

- 1 h or less: one MP3. Longer: parts of 1 h or less, cut at chapter
  ends; one chapter may stand alone up to 2 h; longer chapters are cut
  at scene breaks.
- One SRT per MP3. One script `.md` per book. A `manifest.json`.
- Two-pass EBU R128 loudness, -19 LUFS, -2 dBTP, 128 kb/s mono, ID3 chapters.

## Casting Studio

`SA studio BOOK` opens a page on 127.0.0.1 (session token, no network
exposure). Per voice: name, description, upload or record a clip (5-30
s, max 60 s; any format ffmpeg reads), its exact transcript, generate a
voice from the description, play, preview any line, approve. Script tab:
every chapter as colored lines per speaker, play any line, edit, check.
A changed clip or transcript needs a new approval; `render` refuses
voices nobody approved.

## Script format

```markdown
# Chapter Title

NARRATOR: [warm, wry] Four years later...

PEGGY: [bright] "Morning."

---
```

Details: `storyaudio/script/fmt.py`, `.claude/skills/story-director/SKILL.md`.

## Settings

`storyaudio.toml`: server URL, workers, sampling, segment length, tag
policy, gaps, part limits, engine driver / checkpoint / port. Every key
is optional.

## Tests

```bash
.venv/Scripts/python -m pytest      # Windows
.venv/bin/python -m pytest          # Linux / macOS
```

No GPU, no network, no Docker: a mock Drama 3 server answers with tones
and makes the same request checks; the engine tests use a fake runner.

## Package

```bash
python tools/package.py      # -> dist/book_to_audio-<version>.zip
```

## Disk

`work/<id>/cache/` keeps every rendered line (about 300 MB per hour of
audio). Delete it after the final render to free space. Weights: int4
~21 GB, FP8 ~37 GB.
