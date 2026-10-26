"""Build the shippable zip: dist/book_to_audio-<version>.zip.

    python tools/package.py

The zip holds one top folder, book_to_audio/, with the package, tests,
skills, docs, settings and setup scripts. It never holds user data or
machine state: books, work, output, clips, engine, venv, caches.
"""

from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOP = "book_to_audio"
DIST = ROOT / "dist"

# Machine state and tool caches, never shipped, at any depth.
_SKIP_DIRS = frozenset({".venv", "__pycache__", ".pytest_cache", ".git", ".idea", ".vscode"})

# User data and build output: skipped only at the top level, so the
# storyaudio/engine/ package is not mistaken for the engine/ state folder.
_SKIP_TOP = frozenset({"work", "output", "engine", "dist", "build"})
_SKIP_SUFFIXES = (".pyc", ".pyo", ".log", ".mp3", ".wav", ".egg-info")
_SKIP_NAMES = frozenset({"settings.local.json", ".DS_Store", "Thumbs.db"})

# User-data folders: only their README goes in.
_README_ONLY = frozenset({"books", "voices"})


def main() -> int:
    version = _version()
    target = DIST / f"{TOP}-{version}.zip"
    DIST.mkdir(exist_ok=True)

    files = sorted(_shipped())
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            archive.write(path, f"{TOP}/{path.relative_to(ROOT).as_posix()}")

    print(f"{target}  {len(files)} files  {target.stat().st_size / 1024:.0f} KiB")
    return 0


def _shipped() -> list[Path]:
    shipped = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        parts = path.relative_to(ROOT).parts
        if any(p in _SKIP_DIRS or p.endswith(".egg-info") for p in parts[:-1]):
            continue
        if len(parts) > 1 and parts[0] in _SKIP_TOP:
            continue
        if path.name.endswith(_SKIP_SUFFIXES) or path.name in _SKIP_NAMES:
            continue
        if parts[0] in _README_ONLY and path.name != "README.md":
            continue
        shipped.append(path)
    return shipped


def _version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if match is None:
        sys.exit("pyproject.toml has no version")
    return match.group(1)


if __name__ == "__main__":
    sys.exit(main())
