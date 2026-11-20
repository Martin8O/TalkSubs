"""Going on after an interruption: the same document as one uninterrupted run, and nothing stale is trusted."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterable, Iterator
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest

from parakeet_stt import checkpoint as ckpt
from parakeet_stt import pipeline, project
from parakeet_stt.cancel import CancelToken
from parakeet_stt.engines.base import EngineInfo, EngineOutOfMemory, ProgressFn, TimedSegment, TimedWord
from parakeet_stt.vad import JoinStats, VadOptions, Window
from parakeet_stt.vault import Role, Vault
from tests.conftest import MakeClip

SR = 16_000


class CountingVad:
    runs = 0

    def __init__(self, options: VadOptions | None = None) -> None:
        self.options = options or VadOptions()
        self.stats = JoinStats()

    def windows(self, audio: npt.NDArray[np.float32]) -> Iterator[Window]:
        CountingVad.runs += 1
        self.stats = JoinStats()
        n = len(audio)
        for a in range(0, n - SR, 5 * SR):  # 5 s apart, 4 s long, joined: windows share no audio
            self.stats.windows += 1
            yield Window(a, min(a + 4 * SR, n), a, min(a + 4 * SR, n))


class WindowEngine:
    """Words depend on the window and the sound only (like the real one), with awkward float times."""

    name = "fake"

    def __init__(self, fail_at: int | None = None, batch_size: int = 2) -> None:
        self.fail_at, self.batch_size = fail_at, batch_size
        self.recognised: list[int] = []  # window starts, in order

    def load(self) -> None: ...

    def close(self) -> None: ...

    def describe(self) -> EngineInfo:
        return EngineInfo("fake", "fake@1", "CPU", {"batch_size": self.batch_size}, {"lib": "1"})

    def transcribe(
        self, audio: npt.NDArray[np.float32], windows: Iterable[Window], on_progress: ProgressFn, cancel: CancelToken
    ) -> list[TimedSegment]:
        out = []
        for w in windows:
            cancel.raise_if_cancelled()
            if self.fail_at is not None and len(self.recognised) == self.fail_at:
                raise EngineOutOfMemory("full")
            self.recognised.append(w.start)
            level = float(np.abs(audio[w.start : w.end]).mean())
            t = w.start_s + 1 / 3 + level / 7
            out.append(TimedSegment(w, [TimedWord(f"w{w.start // SR}", t, t + 0.4142135623730951, level)]))
            on_progress(w.end_s)
        return out


@pytest.fixture
def setup(monkeypatch: pytest.MonkeyPatch, make_clip: MakeClip, tmp_path: Path) -> tuple[Path, ckpt.Checkpoint]:
    monkeypatch.setattr(pipeline, "Vad", CountingVad)
    monkeypatch.setattr(pipeline, "SLICE_WINDOWS", 4)
    monkeypatch.setattr(pipeline, "CHECKPOINT_EVERY_S", 0.0)
    CountingVad.runs = 0
    clip = make_clip("talk.wav", seconds=61.0)  # 12 windows: slices of 4, 4, 4
    data = tmp_path / "data"
    data.mkdir()
    return clip, ckpt.Checkpoint(data / "job" / ckpt.FILE, Vault.create_or_open(data))


def _words(res: pipeline.Result) -> list[tuple[str, int, int, float | None]]:
    return [(w.text, w.start_ms, w.end_ms, w.conf) for w in res.document.words()]


def test_an_interrupted_run_goes_on_and_gives_the_same_document(setup: tuple[Path, ckpt.Checkpoint]) -> None:
    clip, cp = setup
    whole = pipeline.transcribe_file(clip, engine=WindowEngine())
    assert len(whole.document.segments) == 12 and CountingVad.runs == 1

    first = WindowEngine(fail_at=9)  # in the third slice
    with pytest.raises(EngineOutOfMemory):
        pipeline.transcribe_file(clip, engine=first, checkpoint=cp)
    assert cp.path.is_file()

    again = WindowEngine()
    events: list[pipeline.Progress] = []
    res = pipeline.transcribe_file(clip, engine=again, checkpoint=cp, on_progress=events.append)
    assert len(again.recognised) == 4  # the two saved slices were not recognised again
    assert again.recognised == [i * 5 * SR for i in range(8, 12)]
    assert CountingVad.runs == 2  # the windows came from the checkpoint
    first_recognise = next(e for e in events if e.phase == "recognise")
    lo, hi = pipeline.PHASE_SHARE["recognise"]
    assert first_recognise.media_done_s == 39.0  # the end of the 8th window, where it goes on
    assert first_recognise.fraction == pytest.approx(lo + (hi - lo) * 39.0 / 61.0, abs=2e-3)
    assert _words(res) == _words(whole)
    assert [s.model_dump() for s in res.document.segments] == [s.model_dump() for s in whole.document.segments]
    fr = [e.fraction for e in events]
    assert fr == sorted(fr) and fr[-1] == 1.0


@pytest.mark.parametrize("stop", ["cancel", "memory"])
def test_a_stopped_run_keeps_every_whole_slice_it_finished(
    setup: tuple[Path, ckpt.Checkpoint], monkeypatch: pytest.MonkeyPatch, stop: str
) -> None:
    from parakeet_stt.cancel import Cancelled

    clip, cp = setup
    monkeypatch.setattr(pipeline, "CHECKPOINT_EVERY_S", 3600.0)  # no save on the way: only the one when stopped
    whole = pipeline.transcribe_file(clip, engine=WindowEngine())
    token = CancelToken()
    eng = WindowEngine(fail_at=10 if stop == "memory" else None)
    if stop == "cancel":
        real = eng.transcribe

        def cancel_in_third_slice(
            audio: npt.NDArray[np.float32], windows: Iterable[Window], on_progress: ProgressFn, cancel: CancelToken
        ) -> list[TimedSegment]:
            ws = list(windows)
            if ws[0].start == 8 * 5 * SR:
                out = real(audio, ws[:2], on_progress, cancel)  # halfway through the slice ...
                token.cancel()  # ... the app is closed
                return out + real(audio, ws[2:], on_progress, cancel)
            return real(audio, ws, on_progress, cancel)

        eng.transcribe = cancel_in_third_slice  # type: ignore[method-assign]
    with pytest.raises(Cancelled if stop == "cancel" else EngineOutOfMemory):
        pipeline.transcribe_file(clip, engine=eng, checkpoint=cp, cancel=token)
    again = WindowEngine()
    res = pipeline.transcribe_file(clip, engine=again, checkpoint=cp)
    assert again.recognised == [i * 5 * SR for i in range(8, 12)]  # 8 windows kept, the cut slice done again
    assert _words(res) == _words(whole)


def test_a_slice_never_splits_a_batch() -> None:
    from parakeet_stt.engines import parakeet

    assert max(parakeet.BATCH_SIZES) > 1 and pipeline.SLICE_WINDOWS % max(parakeet.BATCH_SIZES) == 0


def test_an_odd_last_slice_and_a_failure_on_a_slice_boundary(setup: tuple[Path, ckpt.Checkpoint]) -> None:
    clip, cp = setup
    whole = pipeline.transcribe_file(clip, engine=WindowEngine())
    with pytest.raises(EngineOutOfMemory):
        pipeline.transcribe_file(clip, engine=WindowEngine(fail_at=8), checkpoint=cp)  # first window of a slice
    again = WindowEngine()
    res = pipeline.transcribe_file(clip, engine=again, checkpoint=cp)
    assert len(again.recognised) == 4 and _words(res) == _words(whole)


@pytest.mark.parametrize(
    "change",
    ["engine", "vad", "code", "sound"],
)
def test_a_checkpoint_of_another_run_is_not_used(
    setup: tuple[Path, ckpt.Checkpoint], monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    clip, cp = setup
    with pytest.raises(EngineOutOfMemory):
        pipeline.transcribe_file(clip, engine=WindowEngine(fail_at=9), checkpoint=cp)
    engine, vad = WindowEngine(), None
    if change == "engine":
        engine = WindowEngine(batch_size=1)
    elif change == "vad":
        vad = VadOptions(threshold=0.6)
    elif change == "code":
        monkeypatch.setattr(ckpt, "code_digest", lambda: "another version")
    else:  # the user's file was replaced by another recording of the same length
        subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "sine=frequency=880:sample_rate=44100:duration=61", "-ac", "2", str(clip)],
                       check=True, timeout=60)  # fmt: skip
    pipeline.transcribe_file(clip, engine=engine, checkpoint=cp, vad_options=vad)
    assert len(engine.recognised) == 12


def test_a_damaged_foreign_or_inconsistent_checkpoint_is_not_used(setup: tuple[Path, ckpt.Checkpoint]) -> None:
    clip, cp = setup
    with pytest.raises(EngineOutOfMemory):
        pipeline.transcribe_file(clip, engine=WindowEngine(fail_at=9), checkpoint=cp)
    good = cp.vault.read(cp.path, Role.CHECKPOINT)

    def run_with(raw: bytes) -> int:
        cp.path.write_bytes(raw)
        eng = WindowEngine()
        pipeline.transcribe_file(clip, engine=eng, checkpoint=cp)
        return len(eng.recognised)

    data = json.loads(good)
    swapped = {**data, "windows": [data["windows"][1], data["windows"][0], *data["windows"][2:]]}
    fewer = {**data, "joins": [data["joins"][0] - 1, *data["joins"][1:]]}
    bad_word = {**data, "done": [[["x", "not a time", 1.0, None]], *data["done"][1:]]}
    assert run_with(cp.vault.seal(json.dumps(swapped).encode(), Role.CHECKPOINT)) == 12
    assert run_with(cp.vault.seal(json.dumps(fewer).encode(), Role.CHECKPOINT)) == 12
    assert run_with(cp.vault.seal(json.dumps(bad_word).encode(), Role.CHECKPOINT)) == 12
    assert run_with(cp.vault.seal(good, Role.PROJECT)) == 12  # sealed as another kind of file
    assert run_with(good) == 12  # plain: the app only trusts what it sealed
    assert run_with(b"\x00garbage") == 12
    assert run_with(cp.vault.seal(good, Role.CHECKPOINT)) == 4  # and the real one still works


def test_a_checkpoint_that_cannot_be_saved_does_not_stop_the_job(
    setup: tuple[Path, ckpt.Checkpoint], monkeypatch: pytest.MonkeyPatch
) -> None:
    clip, cp = setup

    def full_disk(_files: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(project, "write_atomically", full_disk)
    res = pipeline.transcribe_file(clip, engine=WindowEngine(), checkpoint=cp)
    assert len(res.document.segments) == 12 and not cp.path.exists()


def test_a_full_disk_mid_write_leaves_no_partial_temp_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "a.bin"

    def failing_fsync(fd: int) -> None:
        raise OSError(28, "No space left on device")

    with monkeypatch.context() as m:
        m.setattr(os, "fsync", failing_fsync)
        with pytest.raises(OSError):
            project.write_atomically({target: b"data"})
    assert list(tmp_path.iterdir()) == []
