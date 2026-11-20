"""How long a transcription takes on this PC: a fixed start-up cost plus a cost per second of recording
(docs/adr.md 0036), kept in `<data>/speed.json` (sealed) per device.

The first numbers come from a short speed test (`benchmark`); as soon as real transcriptions have finished, their
own timings replace them (median of the last few, so one slow run does not throw the estimate).
"""

from __future__ import annotations

import json
import logging
import statistics
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from parakeet_stt.project import write_atomically
from parakeet_stt.vault import Role, Vault, VaultError

log = logging.getLogger("parakeet_stt.speed")

SPEED_FILE = "speed.json"
SCHEMA_VERSION = 1
KEEP_JOBS = 5  # real transcriptions remembered per device
# A real transcription's cost per second of recording over the speed test's (finding speech + model on generated
# sound). Room for a correction; none was needed: through the app the test alone predicted the first lecture
# within 3-6 % on the graphics card and 4 % on the processor (docs/adr.md 0036).
SPEECH_FACTOR = {"cuda": 1.0, "cpu": 1.0}
MIN_MEDIA_S = 60.0  # a shorter recording says too little about the cost per second


class Benchmark(BaseModel):
    fixed_s: float = Field(ge=0)  # process start -> model ready
    vad_rate: float = Field(ge=0)  # seconds of work per second of sound for finding speech
    model_rate: float = Field(ge=0)  # seconds of work per second of speech for the model


class JobTiming(BaseModel):
    """What one finished transcription took, as the worker measured it."""

    fixed_s: float = Field(ge=0)  # process start -> model ready
    work_s: float = Field(ge=0)  # model ready -> transcript saved
    media_s: float = Field(ge=0)
    resumed: bool = False  # went on from an earlier run's progress: its work time says nothing


class DeviceSpeed(BaseModel):
    device: str  # "cuda" | "cpu"
    card: str | None = None
    cpu_threads: int | None = None  # the thread count the speed test found fastest (processor only)
    benchmark: Benchmark | None = None
    jobs: list[JobTiming] = []


class SpeedFile(BaseModel):
    schema_version: int = SCHEMA_VERSION
    devices: dict[str, DeviceSpeed] = {}


class Estimate(BaseModel):
    """A job takes about fixed_s + media_s * per_s seconds (the window does that sum)."""

    fixed_s: float
    per_s: float  # seconds of work per second of recording


def key(device: str, card: str | None) -> str:
    """One entry per way of running: the processor, or a particular graphics card."""
    return f"cuda:{card}" if device == "cuda" else "cpu"


def estimate(entry: DeviceSpeed) -> Estimate | None:
    real = real_jobs(entry)
    if real:
        return Estimate(
            fixed_s=statistics.median(j.fixed_s for j in real),
            per_s=statistics.median(j.work_s / j.media_s for j in real),
        )
    b = entry.benchmark
    if b is None:
        return None
    return Estimate(fixed_s=b.fixed_s, per_s=b.vad_rate + SPEECH_FACTOR.get(entry.device, 1.0) * b.model_rate)


def with_job(entry: DeviceSpeed, timing: JobTiming) -> DeviceSpeed:
    if timing.resumed or timing.media_s < MIN_MEDIA_S:
        return entry
    return entry.model_copy(update={"jobs": [*entry.jobs, timing][-KEEP_JOBS:]})


class SpeedStore:
    def __init__(self, data_dir: Path, vault: Vault) -> None:
        self.path = data_dir / SPEED_FILE
        self.vault = vault

    def load(self) -> SpeedFile:
        """What was measured so far; nothing (a new test is made) if the file is missing, damaged or newer."""
        if not self.path.exists():
            return SpeedFile()
        try:
            raw = json.loads(self.vault.read(self.path, Role.SPEED).decode("utf-8"))
            if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
                raise ValueError("unknown version")
            return SpeedFile.model_validate(raw)
        except (VaultError, UnicodeDecodeError, ValueError, ValidationError) as exc:
            log.warning("the speed measurements cannot be read (%s); they are made again", type(exc).__name__)
            return SpeedFile()

    def save(self, data: SpeedFile) -> None:
        text = data.model_dump_json(indent=1) + "\n"
        write_atomically({self.path: self.vault.seal(text.encode("utf-8"), Role.SPEED)})


def real_jobs(entry: DeviceSpeed) -> list[JobTiming]:
    return [j for j in entry.jobs if not j.resumed and j.media_s >= MIN_MEDIA_S]
