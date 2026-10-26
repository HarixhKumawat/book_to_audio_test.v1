# The Drama 3 server

storyaudio sends HTTP requests to a Fish Audio Drama 3 server
(sglang-omni). The pipeline runs on any machine. The server needs a large
NVIDIA GPU. storyaudio installs and runs it for you.

## Managed engine (recommended)

```bash
SA server plan       # what this machine can run, and on which GPU
SA server install    # one time: sglang-omni (pinned) + weights
SA server start      # starts and waits for GET /health
SA server status     # installed? running? healthy?
SA server logs       # last 60 log lines (--lines N)
SA server stop
```

`SA` is `.venv\Scripts\storyaudio` (Windows) or `.venv/bin/storyaudio`.
After install, `voices`, `studio` and `render` start the engine when they
need it (`[engine] autostart = "on"`).

### What `plan` decides

The server splits each GPU's memory between stages (docs/reference/drama3.md):

    one GPU                          two or more GPUs
    engine   0.70 (default)          GPU 0: engine, up to 0.98
    vocoder  0.10                    GPU 1: preprocessing + vocoder
    preproc  6 GiB reserve

The engine share must hold the weights plus a KV cache:

| Checkpoint | Weights | Needs |
| --- | --- | --- |
| `fishaudio/drama-3-int4` | 20.8 GB | compute capability 8.0+ (GPTQ-Marlin) |
| `fishaudio/drama-3` (FP8 experts) | 37.0 GB | compute 8.9+ (native FP8) |

FP8 is chosen only where it fits. int4 has the same quality (WER 1.63%
vs 1.99% in the Drama 3 docs) and is about 8% slower.

| Machine | Plan |
| --- | --- |
| GTX 10/16, RTX 20, no NVIDIA GPU, macOS | cannot run here |
| one RTX 3090/4090 (24 GB) | too small for the default shares |
| two 24 GB GPUs | int4, engine GPU 0 (share 0.87), vocoder + preprocessing GPU 1 |
| RTX 5090 (32 GB), A6000 / L40S (48 GB) | int4, one GPU |
| H100 / H200 | FP8, one GPU |

Override: `[engine] checkpoint = "int4"` or `"fp8"`.

### Drivers

| Driver | Where | Needs |
| --- | --- | --- |
| `docker` | Windows (Docker Desktop, WSL2 backend), Linux | Docker; NVIDIA driver; Linux: nvidia-container-toolkit |
| `native` | Linux | `git`, `uv`, CUDA driver |

`[engine] driver = "auto"` picks Docker when it runs, else native on
Linux. Both pin sglang-omni to commit
`8b81cda3578c7cdd2f4fdbf1a684893905745d2a` (`[engine] commit`) and use
Python 3.12 (sglang-omni needs <3.13).

Docker keeps everything in named volumes: `storyaudio-engine` (checkout
and venv) and `storyaudio-hf` (weights). The container is
`storyaudio-drama3`, published on `127.0.0.1:8000` only. Remove all of
it with:

```bash
docker rm -f storyaudio-drama3
docker volume rm storyaudio-engine storyaudio-hf
```

Native keeps everything in `engine/native/` (delete the folder to
remove it) and the weights in the Hugging Face cache (`~/.cache/huggingface`).

Gated or rate-limited download: set `HF_TOKEN` in the environment before
`server install`.

## Apple Silicon (M1 and later)

Drama 3 runs on the Mac GPU through
[mlx-audio](https://github.com/Blaizzy/mlx-audio), which ports the same
checkpoint to Metal. No CUDA, no Docker, nothing leaves the machine.

```bash
pip install mlx-audio       # on the Mac, in the venv you use
SA mlx-server               # prints the URL; first run downloads the checkpoint
```

`mlx-server` loads the model once and answers the same
`POST /v1/audio/speech` contract as the CUDA server, so the CLI, the
Casting Studio and the line cache work unchanged. On the same Mac:

```toml
[server]
url = "http://127.0.0.1:8000"
workers = 1        # MLX runs one take at a time; the bridge serialises
```

| build | weights | peak memory | 32 GB Mac |
| --- | --- | --- | --- |
| `mlx-community/fish-audio-drama-3-3bit` | 18.5 GB | 22.5-24 GB | yes, the default |
| `mlx-community/fish-audio-drama-3-4bit` | 22 GB | 26 GB | too tight |
| bf16 | 67 GB | 74 GB | no |

`--model` selects another checkpoint. The port's own benchmarks put an
M3 Max at twice real time on 4-bit; an M1 Pro has about half the memory
bandwidth, so plan on roughly real time (a 2 h book renders in about 2 h).
The bridge prints one line per take: characters, frames, truncated or not.

Keep the Mac awake for a long render:

```bash
caffeinate -i .venv/bin/storyaudio render "books/My Book.epub"
```

Reference clips are written to the system temp folder for one take and
deleted afterwards. Bind to `127.0.0.1` (the default); `--host 0.0.0.0`
exposes the voices to the network, so use it only when you mean it.

## A GPU on another machine

This machine too small? Copy this folder to a machine with a big GPU (a
desktop, a rented RunPod / Vast.ai / Lambda box), and there run:

```bash
sh setup.sh
.venv/bin/storyaudio server install
.venv/bin/storyaudio server start
```

The server listens on that machine's 127.0.0.1 only. Reach it from here
through an SSH tunnel:

```bash
ssh -N -L 8000:127.0.0.1:8000 user@gpu-host
```

`storyaudio.toml` keeps `url = "http://localhost:8000"`. Only text and
audio cross the tunnel; the books stay here. Autostart does not apply to a
remote engine: start it there.

## By hand (no storyaudio)

```bash
git clone https://github.com/sgl-project/sglang-omni.git && cd sglang-omni
git checkout 8b81cda3578c7cdd2f4fdbf1a684893905745d2a
uv venv -p 3.12 && . .venv/bin/activate
uv pip install --prerelease=allow -e ".[fishaudio]"
hf download fishaudio/drama-3-int4
sgl-omni serve --model-path fishaudio/drama-3-int4 --host 127.0.0.1 --port 8000
```

## Flags that matter

| Need | Flag |
| --- | --- |
| Voice clips longer than 60 s | `--preprocessing.factory.max_reference_audio_seconds 90 --preprocessing.total_reserve_bytes 9GiB` |
| More parallel requests | `--tts_engine.factory.max_running_requests 64` (then raise `[server] workers`) |
| Two GPUs | `--tts_engine.gpu 0 --preprocessing.gpu 1 --vocoder.gpu 1` |

## Server facts the pipeline depends on

Checked against sglang-omni source (`serve/protocol.py`,
`models/fishaudio_drama3/speech_request.py`, `prompt.py`):

- `POST /v1/audio/speech` with `input`, `references[{data, media_type, text}]`,
  `response_format: "wav"`, `temperature`, `top_p`, `max_new_tokens`.
- Only `<|speaker:0|>` is accepted. Other speaker tags fail with HTTP 400.
  So storyaudio sends **one voice per request**: each line is cloned from
  that character's reference clip.
- Each reference is 60 s or less. A prompt is 8192 rows or less (about
  21.5 rows per second of reference audio, plus text tokens).
- Output: 44.1 kHz mono WAV. Header `X-Finish-Reason: length` means the
  audio stopped at `max_new_tokens`; storyaudio retries once with double.
- `seed` is refused; takes are not reproducible. The cache keeps every take.

## No GPU at all?

On a Mac, use the Apple Silicon route above. With no usable GPU anywhere,
test the whole pipeline with the mock server. It returns tones, not speech:

```bash
SA mock-server --port 8000
```
