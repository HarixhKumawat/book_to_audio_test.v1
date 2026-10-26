---
name: storyaudio
description: Convert an EPUB, PDF or TXT book into a narrated MP3 audiobook (plus SRT subtitles and the Markdown script) with Fish Audio Drama 3, run locally. Use when the user gives a book file and asks for an audiobook, narration, audio drama, story audio, a full-cast reading, or "make an mp3 of this book". Trigger: /storyaudio <book path>.
---

# storyaudio: book to audiobook

You are the director. The `storyaudio` CLI does the mechanics: extract,
analyze, check, engine, studio, synthesize, assemble. You do the craft:
how the story is told, who plays whom, who says each line, how it is
performed. The user decides the form and approves the voices.

Truth sources (read, never edit): `docs/reference/drama3.md`,
`docs/reference/voice-direction.md`.

`SA` below means the CLI in this folder's venv:
Windows `.venv\Scripts\storyaudio`, Linux/macOS `.venv/bin/storyaudio`.

## Deliverables (all in `output/<id>/`)

1. `<Title>.mp3`, or `<Title> - Part NN.mp3` when the book is over 1 h.
   Parts are 1 h or less when chapters allow; 2 h is the hard limit.
2. One `.srt` per MP3, same name.
3. `<Title> - Script.md`: the script you performed.
4. `manifest.json`: cast, parts, durations.

Done = all four exist, `check` shows 0 errors, every voice was approved
by the user, and you verified the outputs (phase 8). Not before.

## Phases and gates

    0 Setup ─▶ 1 Analyze ─▶ 2 Propose ══▶ user picks a form
      ─▶ 3 Cast ─▶ 4 Direct ─▶ 5 Studio ══▶ user approves voices + script
      ─▶ 6 Engine ─▶ 7 Render ─▶ 8 Verify ─▶ 9 Deliver

`══▶` is a gate: stop and wait for the user. Skip a gate only when the
user said so in this conversation ("just do it", "pick for me").

### 0. Setup

1. No `.venv`? Run `setup.ps1` (Windows) or `sh setup.sh`.
2. `SA doctor`. It reports ffmpeg, the GPU plan, the engine and the server.
3. ffmpeg missing: tell the user the one install command it prints.
4. The engine comes later (phase 6). Do not block the creative work on it.

### 1. Analyze

    SA extract BOOK
    SA analyze BOOK

Check the chapter list `extract` prints: real titles? front matter split
off? word count plausible? If not, fix `work/<id>/book.json` by hand and
note it.

Then read `work/<id>/source.md`. All of it, before you propose. For a
long book, read chapter by chapter and keep notes: characters (age,
gender, accent, temperament, relationships), narrator person and tone,
genre, structure (letters, diaries, frame story), anything hard to voice
(songs, poems, foreign phrases, phonetic dialect).

`analyze` gives numbers (dialogue share, named speakers with line
counts, first/third person, short chapters) and ranks the forms. The
numbers are evidence. Your reading decides.

### 2. Propose (gate)

Write `work/<id>/proposal.md` (the studio shows it), then give the user a
short version in chat. Offer 2-4 forms that fit this book, best first:

| Form | Fits | Script |
| --- | --- | --- |
| Full cast | dialogue-rich fiction, clear speakers | NARRATOR + one ID per speaking character |
| Narrator + leads | many minor characters | leads get voices; NARRATOR voices the rest with moods |
| Single narrator | literary prose, essays, non-fiction, low dialogue | NARRATOR only; moods color each character |
| Protagonist narrates | first-person books | NARRATOR is the main character's voice |
| Conversation retelling | the user wants it, or non-narrative source | `Mode: adapted`; two hosts retell and discuss |

`proposal.md` template:

    # <Title>: how to tell it

    <words> words, about <h> h of audio, <n> chapters. <Person> person.
    Dialogue <x>%. Tone: <one line from your reading>.

    ## Option 1 (recommended): <form>
    Why: <evidence from the book, 2-3 lines>
    Cast (<n> voices): NARRATOR (<description>), PEGGY (<description>), ...
    Cost: <n> clips to approve, ~<h> h of audio to render.

    ## Option 2: <form>
    ...

    Skipped as front matter: <chapter numbers and why>.

Ask: which option, and do they have voice clips of their own (their own
voice, a voice actor, a licensed clip)? Then wait.

### 3. Cast

Edit `work/<id>/script/` (run `SA draft BOOK` first if it does not exist).
In `00-cast.md`, one row per voice. The description seeds the voice when
the user has no clip, so it must be audible and concrete:

    | PEGGY | Peggy | woman, early 30s, soft Northern English accent, warm, quick, kind |

Rules:
- IDs: `UPPERCASE_WITH_UNDERSCORES`, max 32 chars. `NARRATOR` is required.
- Merge the draft's duplicates (for example `HOLYOAKE` + `OLIVER_HOLYOAKE`).
- 1-2 short lines: a shared minor voice (`VILLAGER_M`, `VILLAGER_F`) or NARRATOR.
- 2-10 voices is typical. Each voice is one more clip the user must approve.
- Contrast: voices that share scenes must differ in at least two of age,
  gender, accent, pitch, pace.
- Write the direction as a note at the top of `00-cast.md`:

      <!-- Direction: full cast; British narrator, warm, wry; Peggy carries
           the story; gothic tone darkens from chapter 6. -->

### 4. Direct

One chapter at a time, apply skill `story-director`
(`.claude/skills/story-director/SKILL.md`). After each chapter run
`SA check BOOK` and fix every error before the next.

Front matter that must not be narrated (TOC, copyright, ads): put
`<!-- skip: reason -->` at the top of that chapter file.

Many chapters: you may direct chapters in parallel with subagents. Give
each one `00-cast.md`, its chapter file, its `source.md` section, and the
story-director skill. Run `check` on the whole book yourself afterwards.

### 5. Studio (gate)

    SA studio BOOK

It opens the Casting Studio in the browser and blocks until the user
presses Finish. Run it in the background (so you are notified when it
exits) and tell the user:

- Cast tab: per voice, upload or record a 5-30 s clip and type exactly
  what it says, or press Generate to make one from the description. Play
  it, Preview a line, then Approve.
- Script tab: check who says what, play any line, fix it, press Check.
- Finish when done.

When it exits it prints each voice's state. Then:
1. Re-read `00-cast.md` and every chapter file the user edited (the user
   may have changed lines or added voices). Run `SA check BOOK`.
2. Voices still not approved: ask. Never approve for the user.

No browser (remote shell)? Use `SA voices BOOK` to seed, give the user
the clip paths to play, and `SA voices BOOK --approve ID ...` after they
approve each one in chat.

You cannot hear. Never say a voice "sounds" good. Say what you checked.

### 6. Engine

`SA doctor` says if the server is up. If not:

    SA server plan       # GPUs -> checkpoint + placement, or why not
    SA server install    # one time: image/venv, pinned sglang-omni, weights (20-40 GB)
    SA server start      # waits for /health; first start takes minutes

Install downloads tens of GB and runs for a long time: tell the user
first, run it in the background, and report progress lines.

`plan` refuses? This machine cannot run the CUDA engine (needs an NVIDIA
GPU, compute 8.0+, about 32 GB, or two 24 GB GPUs). Offer the user:
(a) on an Apple Silicon Mac with 32 GB or more, `pip install mlx-audio` then
`SA mlx-server` in one terminal, and `[server] url` at it on this machine
(`docs/SERVER.md`); (b) a GPU machine on the network or a rented GPU: run
this same folder there with `SA server install && SA server start`, then on
this machine set `[server] url` or an SSH tunnel (`docs/SERVER.md`);
(c) stop here with the script and proposal done. Never render the real book
with the mock server, except as a dry run the user asked for.

### 7. Render

    SA render BOOK

It refuses unapproved voices and scripts with errors. It caches every
line in `work/<id>/cache/`; when it stops, run it again and it resumes.
After a script edit only changed lines render again. A full book is
hours of GPU time: run it in the background and report progress.

Before the full book, offer a proof: `SA render BOOK --chapters 2`
writes `output/<id>/proof/`. Its lines go to the same cache, so the full
render reuses them. Do this by default for books over 3 h, and pick a
chapter with dialogue from most of the cast.

### 8. Verify

Report numbers, not impressions:
- `manifest.json`: each part's `seconds` is 3600 or less, or one chapter
  of 7200 or less.
- `ffprobe -v error -show_chapters "<mp3>"` lists the chapter markers.
- Each `.srt` exists; its last cue end is within 2 s of the MP3 length.
- `- Script.md` exists.

### 9. Deliver

Short report: the parts with durations, the cast, the form, warnings you
left and why, and the folder path. Offer next steps that are real: fix a
voice and re-render only its lines (cache), a different form, another book.

## Failure guide

| Symptom | Fix |
| --- | --- |
| `text changed after ...` | You changed, dropped or doubled source words. Restore them. |
| `speaker X not in cast` | Add X to `00-cast.md` or fix the label. |
| `voices not approved` | Phase 5. Never pass `--unapproved` for the real render. |
| `Drama 3 server not ready` | Phase 6. |
| `cannot run Drama 3` | Hardware too small. Phase 6 options (a)/(b). |
| `mlx-audio is not installed` | On the Mac: `pip install mlx-audio`, then `SA mlx-server`. |
| Mac swaps or the bridge dies | Close other apps; use the 3-bit checkpoint; `workers = 1`. |
| `Docker cannot reach the GPU` | Windows: Docker Desktop, WSL2 backend, current NVIDIA driver. Linux: nvidia-container-toolkit. |
| `server stopped while starting` + `out of memory` | Close other GPU programs; `SA server plan` again; set `[engine] checkpoint = "int4"`. |
| `clip is 61 s; the server limit is 60 s` | Trim to 5-30 s. |
| `audio hit max_new_tokens` | Lower `[script] max_segment_chars` or raise `[sampling] max_new_tokens`. |
| Tag words spoken aloud | `[script] tag_policy = "events"` (or `"none"`), render again. |
| PDF: `no extractable text` | Scanned PDF. OCR it first (`ocrmypdf`). |
| Wrong chapters from PDF/TXT | Fix `work/<id>/book.json` by hand, then `SA draft BOOK --force`. |
