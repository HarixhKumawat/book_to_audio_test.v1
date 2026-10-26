"""Settings loaded from storyaudio.toml, with code defaults for every key."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields, replace
from enum import Enum
from pathlib import Path

DEFAULT_CONFIG_NAME = "storyaudio.toml"


class TagPolicy(Enum):
    """Which direction tags reach the TTS model. Subtitles never show tags."""

    KEEP = "keep"            # events, spans and moods
    EVENTS_ONLY = "events"   # [laughs], [sighs] ... only
    NONE = "none"            # plain words


@dataclass(frozen=True)
class ServerCfg:
    url: str = "http://localhost:8000"
    timeout_s: float = 600.0
    workers: int = 4
    retries: int = 3


@dataclass(frozen=True)
class SamplingCfg:
    temperature: float = 1.0
    top_p: float = 0.9
    top_k: int | None = None
    max_new_tokens: int = 2048


@dataclass(frozen=True)
class ScriptCfg:
    max_segment_chars: int = 400
    tag_policy: TagPolicy = TagPolicy.KEEP


@dataclass(frozen=True)
class AudioCfg:
    bitrate: str = "128k"
    loudness_lufs: float = -19.0
    gap_piece_ms: int = 200
    gap_segment_ms: int = 350
    gap_speaker_ms: int = 500
    gap_scene_ms: int = 1200
    gap_chapter_ms: int = 2500


@dataclass(frozen=True)
class PartsCfg:
    target_s: float = 3600.0
    hard_max_s: float = 7200.0


class Driver(Enum):
    """How the local Drama 3 server runs."""

    AUTO = "auto"       # docker when it works, else native (Linux)
    DOCKER = "docker"   # Docker Desktop (Windows, WSL2) or Docker Engine (Linux)
    NATIVE = "native"   # uv venv + git checkout, Linux only


class CheckpointChoice(Enum):
    AUTO = "auto"   # from GPU memory: fp8 where it fits, else int4
    FP8 = "fp8"
    INT4 = "int4"


class Autostart(Enum):
    """Start an installed engine when a command needs the server."""

    ON = "on"
    OFF = "off"


@dataclass(frozen=True)
class EngineCfg:
    driver: Driver = Driver.AUTO
    checkpoint: CheckpointChoice = CheckpointChoice.AUTO
    port: int = 8000
    autostart: Autostart = Autostart.ON
    ready_timeout_s: float = 1800.0
    image: str = "hongccc/sglang-omni:dev"
    repo: str = "https://github.com/sgl-project/sglang-omni.git"
    commit: str = "8b81cda3578c7cdd2f4fdbf1a684893905745d2a"


@dataclass(frozen=True)
class Config:
    server: ServerCfg = field(default_factory=ServerCfg)
    sampling: SamplingCfg = field(default_factory=SamplingCfg)
    script: ScriptCfg = field(default_factory=ScriptCfg)
    audio: AudioCfg = field(default_factory=AudioCfg)
    parts: PartsCfg = field(default_factory=PartsCfg)
    engine: EngineCfg = field(default_factory=EngineCfg)


def load_config(path: Path | None) -> Config:
    """Read a TOML file into Config. A missing default file gives defaults."""
    if path is None:
        path = Path(DEFAULT_CONFIG_NAME)
        if not path.exists():
            return Config()

    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    cfg = Config()

    # Overlay each [section] onto its dataclass; unknown keys are errors so
    # typos do not pass silently.
    for section in fields(Config):
        values = raw.pop(section.name, {})
        current = getattr(cfg, section.name)
        cfg = replace(cfg, **{section.name: _overlay(current, values, section.name)})

    if raw:
        raise ValueError(f"{path}: unknown sections {sorted(raw)}")

    return cfg


def _overlay(current: object, values: dict, section: str) -> object:
    known = {f.name: f for f in fields(current)}
    unknown = set(values) - set(known)
    if unknown:
        raise ValueError(f"[{section}]: unknown keys {sorted(unknown)}")

    # Enums are stored as strings in TOML, e.g. tag_policy = "events".
    coerced = {}
    for key, value in values.items():
        default = getattr(current, key)
        if isinstance(default, Enum):
            value = type(default)(value)
        coerced[key] = value

    return replace(current, **coerced)
