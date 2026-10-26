#!/bin/sh
# One-time setup on Linux / macOS: .venv with storyaudio, then a health check.
# Run: sh setup.sh
set -e
cd "$(dirname "$0")"

# Python 3.11+ is needed (tomllib). uv is used when present: it is faster.
if command -v uv >/dev/null 2>&1; then
    uv venv --allow-existing -p ">=3.11" .venv
    uv pip install --python .venv/bin/python -e ".[dev]"
else
    PY=""
    for candidate in python3.13 python3.12 python3.11 python3; do
        if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
            PY="$candidate"
            break
        fi
    done
    if [ -z "$PY" ]; then
        echo "Python 3.11+ not found. Install it (apt install python3.12 python3.12-venv, or brew install python@3.12)."
        exit 1
    fi
    [ -d .venv ] || "$PY" -m venv .venv
    .venv/bin/python -m pip install --quiet --upgrade pip
    .venv/bin/python -m pip install --quiet -e ".[dev]"
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "ffmpeg not found. Install it: sudo apt install ffmpeg  (macOS: brew install ffmpeg)"
fi

echo
.venv/bin/storyaudio doctor || true
echo
echo "Setup done. CLI: .venv/bin/storyaudio"
