"""Engine planning and control with a fake command runner: no Docker, GPU or network."""

from dataclasses import replace
from pathlib import Path

import pytest

from storyaudio.config import CheckpointChoice, Config, Driver, EngineCfg
from storyaudio.engine import hardware
from storyaudio.engine.drivers import CONTAINER, RunState
from storyaudio.engine.hardware import Checkpoint, Gpu, HardwareError
from storyaudio.engine.manager import Engine, EngineError
from storyaudio.engine.runner import EXIT_NOT_FOUND, Completed

MIB = hardware.MIB
RTX_1650 = "0, NVIDIA GeForce GTX 1650, 4096, 7.5"
RTX_4090 = "0, NVIDIA GeForce RTX 4090, 24564, 8.9"
RTX_5090 = "0, NVIDIA GeForce RTX 5090, 32607, 12.0"
A6000 = "0, NVIDIA RTX A6000, 49140, 8.6"
H100 = "0, NVIDIA H100 80GB HBM3, 81559, 9.0"


class FakeRunner:
    """Answers commands by prefix; records everything it was asked to run."""

    def __init__(self, gpus: str = RTX_5090, programs: tuple[str, ...] = ("docker",), docker_up: bool = True) -> None:
        self.calls: list[list[str]] = []
        self.streamed: list[list[str]] = []
        self.spawned: list[list[str]] = []
        self.container = "running"
        self._gpus = gpus
        self._programs = programs
        self._docker_up = docker_up

    def which(self, program: str) -> str | None:
        return f"/usr/bin/{program}" if program in self._programs else None

    def run(self, args: list[str], timeout_s: float = 60.0) -> Completed:
        self.calls.append(args)
        if args[0] == "nvidia-smi":
            return Completed(0, self._gpus, "") if self._gpus else Completed(EXIT_NOT_FOUND, "", "")
        if args[:2] == ["docker", "info"]:
            return Completed(0 if self._docker_up else 1, "27.0", "")
        if args[:2] == ["docker", "inspect"]:
            return Completed(0, self.container, "") if self.container else Completed(1, "", "no such container")
        if args[:2] == ["docker", "logs"]:
            return Completed(0, "CUDA out of memory", "")
        return Completed(0, "", "")

    def stream(self, args, log, on_line, env=None) -> int:
        self.streamed.append(args)
        on_line("step output")
        return 0

    def spawn(self, args, log, env=None) -> int:
        self.spawned.append(args)
        return 4242

    def alive(self, pid: int) -> bool:
        return True

    def kill(self, pid: int) -> None:
        self.calls.append(["kill", str(pid)])


def _gpus(*lines: str) -> list[Gpu]:
    return hardware.parse_gpus("\n".join(lines))


def _engine(tmp_path: Path, runner: FakeRunner, engine: EngineCfg = EngineCfg(), **kwargs) -> Engine:
    return Engine(replace(Config(), engine=engine), tmp_path, runner, sleep=lambda s: None, **kwargs)


# ---------------------------------------------------------------- hardware


def test_parse_gpus_reads_nvidia_smi_csv():
    gpus = _gpus(RTX_4090, "1, Virtual GPU, [N/A], [N/A]")

    assert gpus == [Gpu(0, "NVIDIA GeForce RTX 4090", 24564 * MIB, 8.9)]


def test_gtx_1650_is_refused_with_reason():
    with pytest.raises(HardwareError, match="compute capability 8.0"):
        hardware.plan(_gpus(RTX_1650), CheckpointChoice.AUTO)


def test_no_gpu_points_to_remote_server():
    with pytest.raises(HardwareError, match=r"\[server\] url"):
        hardware.plan([], CheckpointChoice.AUTO)


def test_single_24gb_card_is_too_small_but_two_fit():
    with pytest.raises(HardwareError, match="two 24 GB GPUs"):
        hardware.plan(_gpus(RTX_4090), CheckpointChoice.AUTO)

    placement = hardware.plan(_gpus(RTX_4090, RTX_4090.replace("0,", "1,", 1)), CheckpointChoice.AUTO)
    assert placement.checkpoint is Checkpoint.INT4
    assert placement.serve_args() == [
        "--tts_engine.gpu", "0", "--preprocessing.gpu", "1", "--vocoder.gpu", "1",
        "--tts_engine.gpu_memory_fraction", "0.87",
    ]


def test_rtx_5090_runs_int4_with_default_shares():
    placement = hardware.plan(_gpus(RTX_5090), CheckpointChoice.AUTO)

    assert placement.checkpoint is Checkpoint.INT4
    assert placement.serve_args() == ["--tts_engine.gpu", "0"]


def test_fp8_needs_ada_or_newer_and_room():
    assert hardware.plan(_gpus(A6000), CheckpointChoice.AUTO).checkpoint is Checkpoint.INT4
    assert hardware.plan(_gpus(H100), CheckpointChoice.AUTO).checkpoint is Checkpoint.FP8

    with pytest.raises(HardwareError, match="drama-3 needs"):
        hardware.plan(_gpus(RTX_5090), CheckpointChoice.FP8)


# ---------------------------------------------------------------- docker driver


def test_install_docker_pins_commit_and_writes_state(tmp_path: Path):
    runner = FakeRunner()
    lines: list[str] = []
    installed = _engine(tmp_path, runner).install(lines.append)

    assert installed.driver == "docker"
    assert installed.checkpoint == Checkpoint.INT4.value
    assert (tmp_path / "engine" / "state.json").exists()

    pull, build = runner.streamed
    assert pull == ["docker", "pull", EngineCfg().image]
    script = build[-1]
    assert f"git checkout --quiet {EngineCfg().commit}" in script
    assert "uv venv -p 3.12" in script
    assert "snapshot_download('fishaudio/drama-3-int4')" in script
    assert "--rm" in build and "--gpus" in build
    assert any("driver: docker" in line for line in lines)


def test_docker_start_binds_localhost_only(tmp_path: Path):
    runner = FakeRunner()
    engine = _engine(tmp_path, runner)
    engine.install(lambda line: None)
    runner.container = "exited"
    engine._health = iter(["down", None]).__next__   # type: ignore[method-assign]
    engine.start(lambda line: None)

    run = next(c for c in runner.calls if c[:3] == ["docker", "run", "--gpus"])
    assert "127.0.0.1:8000:8000" in run
    assert "--name" in run and CONTAINER in run
    assert "--host 0.0.0.0 --port 8000" in run[-1]   # inside the container only


def test_start_fails_fast_when_server_dies(tmp_path: Path):
    runner = FakeRunner()
    engine = _engine(tmp_path, runner)
    engine.install(lambda line: None)
    engine._health = lambda: "down"   # type: ignore[method-assign]
    runner.container = "exited"

    with pytest.raises(EngineError, match="CUDA out of memory"):
        engine.start(lambda line: None)


def test_start_times_out_with_log_tail(tmp_path: Path):
    runner = FakeRunner()
    ticks = iter(range(0, 10_000, 600))
    engine = _engine(tmp_path, runner, EngineCfg(ready_timeout_s=1200), clock=lambda: next(ticks))
    engine.install(lambda line: None)
    engine._health = lambda: "down"   # type: ignore[method-assign]

    with pytest.raises(EngineError, match="not ready after 1200 s"):
        engine.start(lambda line: None)


def test_start_without_install_is_clear(tmp_path: Path):
    with pytest.raises(EngineError, match="server install"):
        _engine(tmp_path, FakeRunner()).start(lambda line: None)


def test_status_and_stop(tmp_path: Path):
    runner = FakeRunner()
    engine = _engine(tmp_path, runner)
    assert engine.status().state is RunState.ABSENT

    engine.install(lambda line: None)
    assert engine.status().state is RunState.RUNNING

    engine.stop()
    assert ["docker", "rm", "-f", CONTAINER] in runner.calls


# ---------------------------------------------------------------- driver choice


def test_auto_falls_back_and_explains(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    runner = FakeRunner(docker_up=False)

    with pytest.raises(EngineError, match="not running.*Linux only"):
        _engine(tmp_path, runner).install(lambda line: None)


def test_native_driver_on_linux(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    runner = FakeRunner(programs=("git", "uv"))
    engine = _engine(tmp_path, runner, EngineCfg(driver=Driver.NATIVE))

    installed = engine.install(lambda line: None)
    engine._health = iter(["down", None]).__next__   # type: ignore[method-assign]
    engine.start(lambda line: None)

    assert installed.driver == "native"
    assert ["uv", "venv", "-p", "3.12", str(tmp_path / "engine" / "native" / "sglang-omni" / ".venv")] in runner.streamed
    serve = runner.spawned[0]
    assert serve[0].endswith("sgl-omni") and serve[1] == "serve"
    assert serve[serve.index("--host") + 1] == "127.0.0.1"
    assert (tmp_path / "engine" / "native" / "server.pid").read_text() == "4242"


def test_hardware_refusal_stops_install(tmp_path: Path):
    runner = FakeRunner(gpus=RTX_1650)

    with pytest.raises(HardwareError):
        _engine(tmp_path, runner).install(lambda line: None)
    assert runner.streamed == []
