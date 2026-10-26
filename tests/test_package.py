"""The shipped zip holds the whole product and no user data."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _package_module():
    spec = importlib.util.spec_from_file_location("package_tool", ROOT / "tools" / "package.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_zip_contents_are_complete_and_clean():
    shipped = {p.relative_to(ROOT).as_posix() for p in _package_module()._shipped()}

    for required in (
        "AGENTS.md", "CLAUDE.md", "setup.ps1", "setup.sh", "storyaudio.toml", "pyproject.toml",
        "storyaudio/engine/manager.py", "storyaudio/studio/page.html", "storyaudio/tts/voices.py",
        ".claude/skills/storyaudio/SKILL.md", ".claude/skills/story-director/SKILL.md",
        "docs/reference/drama3.md", "docs/reference/voice-direction.md", "books/README.md", "voices/README.md",
    ):
        assert required in shipped, required

    top = {path.split("/")[0] for path in shipped}
    assert not top & {"work", "output", "engine", "dist", ".venv"}
    assert [p for p in shipped if p.startswith("books/")] == ["books/README.md"]
    assert not [p for p in shipped if "__pycache__" in p or p.endswith((".pyc", ".mp3", ".wav"))]
