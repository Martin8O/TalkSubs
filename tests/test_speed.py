"""This PC's speed: what the estimate is made of, where it is kept, and how the queue measures it."""

from __future__ import annotations

import itertools
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from parakeet_stt import machine, speed
from parakeet_stt.speed import Benchmark, DeviceSpeed, JobTiming, SpeedFile, SpeedStore
from tests.conftest import MakeClip, key_of
from tests.test_server import ORIGIN, add, make_client, state_of, wait

PC: dict[str, Any] = {"device": "cpu", "reason": "no_card", "card": None, "card_mb": None,
                      "performance_cores": 6, "physical_cores": 8}  # fmt: skip
BENCH = Benchmark(fixed_s=3.0, vad_rate=0.005, model_rate=0.02)


@pytest.fixture
def data(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    return d


def timing(fixed: float, work: float, media: float = 600.0, resumed: bool = False) -> JobTiming:
    return JobTiming(fixed_s=fixed, work_s=work, media_s=media, resumed=resumed)


# ---- the estimate ----------------------------------------------------------------------------------------------


def test_the_speed_test_seeds_the_estimate() -> None:
    est = speed.estimate(DeviceSpeed(device="cpu", benchmark=BENCH))
    assert est is not None
    assert est.fixed_s == 3.0
    assert est.per_s == pytest.approx(0.005 + speed.SPEECH_FACTOR["cpu"] * 0.02)
    assert speed.estimate(DeviceSpeed(device="cpu")) is None


def test_real_transcriptions_replace_it_by_their_median() -> None:
    entry = DeviceSpeed(device="cpu", benchmark=BENCH)
    for fixed, work in ((2.0, 30.0), (4.0, 60.0), (3.0, 300.0)):  # one very slow run does not throw it
        entry = speed.with_job(entry, timing(fixed, work))
    est = speed.estimate(entry)
    assert est is not None
    assert est.fixed_s == 3.0 and est.per_s == pytest.approx(60.0 / 600)


def test_resumed_and_very_short_runs_say_nothing() -> None:
    entry = DeviceSpeed(device="cpu", benchmark=BENCH)
    entry = speed.with_job(entry, timing(2.0, 1.0, resumed=True))
    entry = speed.with_job(entry, timing(2.0, 1.0, media=10.0))
    assert entry.jobs == []


def test_only_the_last_few_are_kept() -> None:
    entry = DeviceSpeed(device="cuda", card="X")
    for i in range(speed.KEEP_JOBS + 3):
        entry = speed.with_job(entry, timing(1.0, float(i + 1)))
    assert [j.work_s for j in entry.jobs] == [float(i + 1) for i in range(3, speed.KEEP_JOBS + 3)]


def test_one_entry_per_card_and_one_for_the_processor() -> None:
    assert speed.key("cuda", "NVIDIA GeForce RTX 5070") == "cuda:NVIDIA GeForce RTX 5070"
    assert speed.key("cpu", None) == speed.key("cpu", "ignored") == "cpu"


def test_kept_sealed_and_a_damaged_file_is_measured_again(tmp_path: Path) -> None:
    store = SpeedStore(tmp_path, key_of(tmp_path))
    kept = SpeedFile(devices={"cpu": DeviceSpeed(device="cpu", cpu_threads=6, benchmark=BENCH)})
    store.save(kept)
    assert b"vad_rate" not in (tmp_path / speed.SPEED_FILE).read_bytes()
    assert store.load() == kept
    (tmp_path / speed.SPEED_FILE).write_bytes(b"not sealed")
    assert store.load() == SpeedFile()


# ---- this PC ---------------------------------------------------------------------------------------------------


def test_looking_at_the_pc_imports_neither_torch_nor_onnxruntime() -> None:
    code = "import sys; from parakeet_stt import machine; machine.detect(); " \
           "print('onnxruntime' in sys.modules, 'torch' in sys.modules)"  # fmt: skip
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.split() == ["False", "False"]


def test_a_hidden_card_means_the_processor() -> None:
    import os

    env = {**os.environ, "CUDA_VISIBLE_DEVICES": "-1"}
    out = subprocess.run([sys.executable, "-m", "parakeet_stt", "machine"], capture_output=True, text=True,
                         check=True, env=env)  # fmt: skip
    assert '"device": "cpu"' in out.stdout and '"reason": "no_card"' in out.stdout


RTX = ("RTX", (8, 6), 12_000)


@pytest.mark.parametrize(
    ("card", "driver", "provider", "device", "reason"),
    [
        (RTX, 12080, True, "cuda", "card"),
        (("GT 730", (3, 5), 2_000), 12080, True, "cpu", "card_too_old"),
        (("MX150", (6, 1), 2_000), 12080, True, "cpu", "card_too_small"),
        (None, 0, True, "cpu", "no_card"),
        (RTX, 11080, True, "cpu", "driver_too_old"),  # a driver before CUDA 12: the processor, said plainly
        (RTX, 12080, False, "cpu", "card_pack_missing"),  # the download package: the card support is offered
        (RTX, 11080, False, "cpu", "driver_too_old"),  # ... but not with a driver that could not run it
        (None, 0, False, "cpu", "no_card"),
    ],
)
def test_which_cards_are_used(  # noqa: PLR0917
    monkeypatch: pytest.MonkeyPatch, card: object, driver: int, provider: bool, device: str, reason: str
) -> None:
    monkeypatch.setattr(machine, "_cuda_provider_installed", lambda: provider)
    monkeypatch.setattr(machine, "_card", lambda: card)
    monkeypatch.setattr(machine, "_driver_present", lambda: True)
    monkeypatch.setattr(machine, "_driver_cuda", lambda: driver)
    m = machine.detect(probe=True)
    assert (m.device, m.reason) == (device, reason)


def test_a_broken_driver_is_not_hidden_behind_the_processor(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> None:
        raise machine.DriverError("cuInit 999")

    monkeypatch.setattr(machine, "_cuda_provider_installed", lambda: True)
    monkeypatch.setattr(machine, "_card", broken)
    assert machine.detect().device == "cuda"  # loading the model then shows the real error


def test_without_graphics_card_support_the_processor_runs_it(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> None:
        raise machine.DriverError("cuInit 999")

    monkeypatch.setattr(machine, "_cuda_provider_installed", lambda: False)
    monkeypatch.setattr(machine, "_card", broken)
    assert (machine.detect(probe=True).device, machine.detect(probe=True).reason) == ("cpu", "no_card_support")


def test_a_job_on_the_processor_never_wakes_the_card(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the graphics-card support only the start-up probe asks the driver; a job's worker does not."""

    def asked() -> None:
        raise AssertionError("the driver was asked")

    monkeypatch.setattr(machine, "_cuda_provider_installed", lambda: False)
    monkeypatch.setattr(machine, "_card", asked)
    monkeypatch.setattr(machine, "_driver_cuda", asked)
    assert (machine.detect().device, machine.detect().reason) == ("cpu", "no_card_support")


def test_threads_are_the_fast_cores() -> None:
    def pc(perf: int, phys: int) -> machine.Machine:
        return machine.Machine("cpu", "no_card", None, None, perf, phys)

    assert machine.cpu_threads(pc(8, 20)) == 8
    assert machine.cpu_threads(pc(2, 10)) == 10  # two fast cores alone are too few
    assert machine.thread_candidates(pc(8, 20)) == [8, 20]
    assert machine.thread_candidates(pc(6, 6)) == [6]
    perf, phys = machine.cpu_cores()
    assert 1 <= perf <= phys


# ---- the queue measures it -------------------------------------------------------------------------------------


def status(c: Any) -> dict[str, Any]:
    return dict(c.get("/api/status").json())


def test_a_new_pc_is_measured_once_the_queue_is_idle(data: Path) -> None:
    with make_client(data, machine_check=lambda: PC) as c:
        wait(lambda: (status(c)["machine"] or {}).get("per_s") is not None)
        m = status(c)["machine"]
        assert m["device"] == "cpu" and m["reason"] == "no_card" and m["cpu_threads"] == 6
        assert m["measured_by"] == "test" and not m["measuring"]
        assert m["per_s"] == pytest.approx(0.005 + speed.SPEECH_FACTOR["cpu"] * 0.02)
    with make_client(data, machine_check=lambda: PC) as c:  # measured: not again at the next start
        wait(lambda: status(c)["machine"] is not None)
        assert not status(c)["machine"]["measuring"]


def test_a_finished_job_measures_the_pc_and_jobs_get_their_length(data: Path, make_clip: MakeClip) -> None:
    clip = make_clip("talk.wav", seconds=2)
    with make_client(data, machine_check=lambda: PC) as c:
        job = add(c, clip)
        assert job["media_s"] == pytest.approx(2.0, abs=0.1)
        wait(lambda: state_of(c, job["id"]) == "done")
        wait(lambda: (status(c)["machine"] or {}).get("measured_by") == "jobs")
        m = status(c)["machine"]
        assert m["fixed_s"] == 2.0 and m["per_s"] == pytest.approx(6.0 / 120)


def test_a_job_added_during_the_speed_test_waits_for_it(
    data: Path, make_clip: MakeClip, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_BENCH", "hold")
    with make_client(data, machine_check=lambda: PC, kill_after_s=1.0) as c:
        wait(lambda: (status(c)["machine"] or {}).get("measuring") is True)
        job = add(c, make_clip("talk.wav", seconds=1))
        for _ in range(10):
            assert state_of(c, job["id"]) == "queued"
            time.sleep(0.1)
    # closing during the test: it is stopped and its (missing) result is not kept
    assert not (data / speed.SPEED_FILE).exists() or "cpu" not in SpeedStore(data, key_of(data)).load().devices


def runs(log: Path) -> list[tuple[str, float, float]]:
    """(kind, start, end) of every worker the stand-in log saw, in start order."""
    starts: dict[str, list[float]] = {}
    out = []
    for line in log.read_text(encoding="utf-8").splitlines():
        kind, what, t = line.split()
        if what == "start":
            starts.setdefault(kind, []).append(float(t))
        else:
            out.append((kind, starts[kind].pop(0), float(t)))
    return sorted(out, key=lambda r: r[1])


def test_the_speed_test_never_runs_beside_a_job_nor_twice(
    data: Path, make_clip: MakeClip, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = tmp_path / "workers.log"
    monkeypatch.setenv("FAKE_WORKER_LOG", str(log))
    with make_client(data, machine_check=lambda: PC) as c:
        wait(lambda: (status(c)["machine"] or {}).get("per_s") is not None)
        job = add(c, make_clip("slow.wav", seconds=1))
        wait(lambda: state_of(c, job["id"]) == "running")
        for _ in range(3):
            assert c.post("/api/speed/measure", json={}, headers=ORIGIN).status_code == 204
        m = status(c)["machine"]
        assert m["measure_waiting"] is True and m["measuring"] is False
        wait(lambda: state_of(c, job["id"]) == "done")
        wait(lambda: not status(c)["machine"]["measuring"] and not status(c)["machine"]["measure_waiting"])
        assert status(c)["machine"]["measured_by"] == "test"  # a new test starts the estimate over
    seen = runs(log)
    assert [k for k, _, _ in seen] == ["benchmark", "job", "benchmark"]  # the first: a new PC, measured at start
    for (_, _, end), (_, start, _) in itertools.pairwise(seen):
        assert end <= start  # one worker at a time


def test_a_pc_measured_by_its_jobs_is_not_tested_again(
    data: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entry = speed.with_job(DeviceSpeed(device="cpu"), timing(2.0, 30.0))
    SpeedStore(data, key_of(data)).save(SpeedFile(devices={"cpu": entry}))
    log = tmp_path / "workers.log"
    monkeypatch.setenv("FAKE_WORKER_LOG", str(log))
    with make_client(data, machine_check=lambda: PC) as c:
        wait(lambda: status(c)["machine"] is not None)
        time.sleep(1.0)
        assert status(c)["machine"]["measured_by"] == "jobs"
    assert not log.exists()


def test_a_failing_speed_test_is_not_started_again_and_again(data: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_BENCH", "fail")
    with make_client(data, machine_check=lambda: PC) as c:
        wait(lambda: status(c)["machine"] is not None)
        wait(lambda: status(c)["machine"]["measuring"] is False)
        m = status(c)["machine"]
        assert m["per_s"] is None
        for _ in range(10):
            time.sleep(0.1)
        assert status(c)["machine"]["measuring"] is False


def test_without_a_machine_there_is_no_speed_test(data: Path) -> None:
    with make_client(data) as c:  # the stand-in worker: nothing to measure
        assert status(c)["machine"] is None
        assert c.post("/api/speed/measure", json={}, headers=ORIGIN).status_code == 409
