"""Stands in for `python -m parakeet_stt worker` in server tests: same protocol, no GPU.

What it does depends on the media file's name: `slow*` reports progress for ~1.5 s, `hold*` until it is
cancelled, `crash*` dies,
`busy*` says the GPU is in use, `error*` reports an error, `hang*` ignores cancel. Anything else is quick.
Checkpoints (sealed like the real one, holding just the mode's name): `hold*`, `crash*`, `busy*`, `error*` leave one;
`crashonce*` / `memoryonce*` die / find the GPU memory full unless their own checkpoint says they already did,
so they finish only when the runner kept it; `memory*` always finds the GPU memory full; `diskonce*` finds the
disk full at the final save unless its checkpoint says it already did; `disk*` always finds it full.
The speed test (spec kind "benchmark") answers at once, or as `FAKE_BENCH` says: `hold` until cancelled, `fail`.
A finished job reports its timing as if a 120 s recording took 2 s to start and 6 s of work on the processor.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any


def send(obj: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main() -> int:
    send({"t": "hello", "pid": os.getpid()})
    spec = json.loads(sys.stdin.readline())
    name = Path(spec.get("source", "")).name
    cancelled = threading.Event()

    def watch() -> None:
        for line in sys.stdin:
            if line.strip() == "cancel":
                cancelled.set()
        cancelled.set()

    threading.Thread(target=watch, daemon=True).start()
    log = os.environ.get("FAKE_WORKER_LOG")  # "<kind> start|end <time>" lines: tests check what overlapped
    kind = str(spec.get("kind", "job"))
    if log:
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(f"{kind} start {time.monotonic()}\n")
    try:
        if kind == "benchmark":
            return benchmark(cancelled)
        return job(spec, name, cancelled)
    finally:
        if log:
            with open(log, "a", encoding="utf-8") as fh:
                fh.write(f"{kind} end {time.monotonic()}\n")


def job(spec: dict[str, Any], name: str, cancelled: threading.Event) -> int:  # noqa: PLR0911, PLR0912
    from parakeet_stt.vault import Role, Vault

    vault = Vault.open(Path(spec["data_dir"]))
    ckpt = Path(spec["out_dir"]) / "checkpoint.bin"
    before = vault.read(ckpt, Role.CHECKPOINT).decode() if ckpt.is_file() else ""

    def keep(mode: str) -> None:
        ckpt.write_bytes(vault.seal(mode.encode(), Role.CHECKPOINT))

    for mode in ("crashonce", "memoryonce", "memory", "diskonce", "disk"):
        if name.startswith(mode) and not (mode.endswith("once") and before == mode):
            keep(mode)
            if mode == "crashonce":
                print("Fatal: pretend native crash", file=sys.stderr, flush=True)
                return 3
            if mode.startswith("disk"):
                send({"t": "disk_full", "message": "The disk is full, so the finished transcript cannot be saved."})
                return 0
            send({"t": "gpu_memory", "message": "The graphics card has no memory left for the model."})
            return 0
        if name.startswith(mode):
            break  # its checkpoint survived: finish
    for mode in ("hold", "crash", "busy", "error"):
        if name.startswith(mode):
            keep(mode)
    if name.startswith("crash") and not name.startswith("crashonce"):
        print("Fatal: pretend CUDA failure", file=sys.stderr, flush=True)
        return 3
    if name.startswith("busy"):
        send({"t": "gpu_busy", "message": "Another transcription is using the GPU; wait until it finishes."})
        return 0
    if name.startswith("error"):
        send({"t": "error", "message": "pretend media error"})
        return 0
    if name.startswith("hold"):  # progress until cancelled (at most 60 s): cancel tests never race the job
        for i in range(1200):
            if cancelled.is_set():
                send({"t": "cancelled"})
                return 0
            frac = min(0.01 + i / 1300, 0.95)
            send({"t": "progress", "phase": "recognise", "fraction": frac, "media_done_s": frac * 3,
                  "media_total_s": 3.0})  # fmt: skip
            time.sleep(0.05)
        return 4
    steps = 30 if name.startswith(("slow", "hang")) else 2
    for i in range(steps):
        if cancelled.is_set() and not name.startswith("hang"):
            send({"t": "cancelled"})
            return 0
        frac = (i + 1) / steps * 0.99
        send({"t": "progress", "phase": "recognise", "fraction": frac, "media_done_s": frac * 3, "media_total_s": 3.0})
        time.sleep(0.05)
    if name.startswith("hang"):
        time.sleep(60)
    out = Path(spec["out_dir"]) / f"{spec['stem']}.pstt.json"
    real = os.environ.get("FAKE_WORKER_PROJECT")  # a real project file, for tests that export the result
    out.write_bytes(vault.seal(Path(real).read_bytes() if real else b"{}", Role.PROJECT))
    timing = {"fixed_s": 2.0, "work_s": 6.0, "media_s": 120.0, "resumed": False}
    send({"t": "done", "project_file": out.name, "device": "cpu", "card": None, "timing": timing})
    return 0


def benchmark(cancelled: threading.Event) -> int:
    how = os.environ.get("FAKE_BENCH", "")
    if how == "fail":
        print("Fatal: pretend speed test failure", file=sys.stderr, flush=True)
        return 3
    if how == "hold":
        cancelled.wait(60)
        send({"t": "cancelled"})
        return 0
    send(
        {
            "t": "benchmark_done",
            "device": "cpu",
            "card": None,
            "cpu_threads": 6,
            "benchmark": {"fixed_s": 3.0, "vad_rate": 0.005, "model_rate": 0.02},
            "tried": {"6": 0.02, "8": 0.03},
        }
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
