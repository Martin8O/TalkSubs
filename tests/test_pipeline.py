from __future__ import annotations

from collections.abc import Iterable, Iterator

import numpy as np
import numpy.typing as npt
import pytest

from parakeet_stt import engines, pipeline
from parakeet_stt.cancel import Cancelled, CancelToken
from parakeet_stt.engines.base import EngineInfo, ProgressFn, TimedSegment, TimedWord
from parakeet_stt.vad import JoinStats, VadOptions, Window, resolve_joins
from tests.conftest import MakeClip

SR = 16_000


def _win(a_s: float, b_s: float, own: tuple[float, float] | None = None) -> Window:
    o = own or (a_s, b_s)
    return Window(int(a_s * SR), int(b_s * SR), int(o[0] * SR), int(o[1] * SR))


def test_short_pause_at_the_length_cap_leaves_no_audio_uncovered() -> None:
    # library output: a 0.25 s padded gap at a forced join (raw gap 0.65 s: the "Of course" case)
    raw = iter([(0, 20 * SR), (int(20.25 * SR), 30 * SR), (40 * SR, 45 * SR)])
    stats = JoinStats()
    ws = list(resolve_joins(raw, VadOptions(), 50 * SR, stats))
    assert stats.forced_joins == 1 and stats.hard_splits == 0 and stats.windows == 3
    a, b, c = ws
    assert a.own_end == b.own_start  # one boundary in the pause
    assert a.end > a.own_end and b.start < b.own_start  # both windows hear past it
    assert c.own_start == c.start  # a real pause stays a real pause


def test_hard_split_shares_audio_but_not_ownership() -> None:
    pad = 200 * SR // 1000
    raw = iter([(0, 25 * SR + pad), (25 * SR - pad, 40 * SR)])
    stats = JoinStats()
    a, b = resolve_joins(raw, VadOptions(), 40 * SR, stats)
    assert stats.hard_splits == 1
    assert a.own_end == b.own_start == 25 * SR


def test_word_heard_by_both_windows_is_kept_once() -> None:
    a = _win(0, 10.2, (0, 10))
    b = _win(9.8, 20, (10, 20))
    word = TimedWord("same", 9.85, 10.05, 1.0)  # midpoint 9.95 -> window a
    stats = pipeline.RunStats()
    segs = pipeline.build_segments(
        [TimedSegment(a, [TimedWord("x", 5, 5.2, 1.0), word]), TimedSegment(b, [word, TimedWord("y", 12, 12.3, 1.0)])],
        stats,
    )
    assert [w.text for s in segs for w in s.words] == ["x", "same", "y"]
    assert stats.words_dropped_at_joins == 1


def test_word_completed_from_its_first_moment_at_a_window_edge_is_kept_once() -> None:
    # measured case: window a hears only the onset of "effective" at its edge, but emits the whole word,
    # timed in the pause before the boundary; window b hears it fully
    a = _win(477.43, 502.34, (477.43, 502.14))
    b = _win(501.94, 503.08, (502.14, 503.08))
    stats = pipeline.RunStats()
    segs = pipeline.build_segments(
        [
            TimedSegment(a, [TimedWord("very", 501.43, 501.6, 1.0), TimedWord("effective.", 501.91, 502.23, 0.9)]),
            TimedSegment(b, [TimedWord("Effective.", 502.26, 502.98, 1.0)]),
        ],
        stats,
    )
    assert [w.text for s in segs for w in s.words] == ["very", "Effective."]
    assert stats.words_dropped_at_joins == 1


def test_a_real_repeat_away_from_the_edges_is_kept() -> None:
    a = _win(0, 10.2, (0, 10))
    b = _win(9.8, 20, (10, 20))
    segs = pipeline.build_segments(
        [TimedSegment(a, [TimedWord("very,", 9.3, 9.6, 1.0)]), TimedSegment(b, [TimedWord("very", 10.2, 10.5, 1.0)])]
    )
    assert [w.text for s in segs for w in s.words] == ["very,", "very"]


def test_negative_end_offset_never_makes_a_word_end_before_it_starts() -> None:
    w = _win(0, 5)
    one_token = TimedWord("I", 1.0, 0.99, None)  # last token + (-10 ms)
    (seg,) = pipeline.build_segments([TimedSegment(w, [one_token, TimedWord("am", 1.04, 1.2, None)])])
    first, second = seg.words
    assert first.start_ms == 1000 and first.end_ms == 1040  # min one frame, but not past the next word
    assert second.end_ms >= second.start_ms


def test_first_short_word_survives_a_negative_end_offset() -> None:
    # a one-token word at the very start of a window: end (last token - 40 ms) < start must not drop it
    (seg,) = pipeline.build_segments([TimedSegment(_win(2, 6), [TimedWord("And", 2.0, 1.96, None)])])
    assert [w.text for w in seg.words] == ["And"]


def test_word_end_never_runs_past_its_window() -> None:
    (seg,) = pipeline.build_segments([TimedSegment(_win(0, 2), [TimedWord("end", 1.8, 2.1, None)])])
    assert seg.words[0].end_ms == 2000


class FakeVad:
    def __init__(self, options: VadOptions | None = None) -> None:
        self.options = options or VadOptions()
        self.stats = JoinStats()

    def windows(self, audio: npt.NDArray[np.float32]) -> Iterator[Window]:
        n = len(audio) / SR
        for a in np.arange(0.0, n - 1, 5.0):
            yield _win(float(a), float(min(a + 4, n)))


class FakeEngine:
    name = "fake"

    def __init__(self, cancel_after: int | None = None, cancel: CancelToken | None = None) -> None:
        self.loaded = False
        self.closed = False
        self.cancel_after, self.token = cancel_after, cancel

    def load(self) -> None:
        self.loaded = True

    def close(self) -> None:
        self.closed = True

    def describe(self) -> EngineInfo:
        return EngineInfo("fake", "fake@0", "CPU", {"batch_size": 1}, {})

    def transcribe(
        self, audio: npt.NDArray[np.float32], windows: Iterable[Window], on_progress: ProgressFn, cancel: CancelToken
    ) -> list[TimedSegment]:
        out = []
        for i, w in enumerate(windows):
            if self.cancel_after is not None and i == self.cancel_after and self.token:
                self.token.cancel()
            cancel.raise_if_cancelled()
            out.append(TimedSegment(w, [TimedWord(f"w{i}.", w.start_s + 0.5, w.start_s + 1.0, 0.9)]))
            on_progress(w.end_s)
        return out


@pytest.fixture
def fake_vad(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline, "Vad", FakeVad)


@pytest.mark.usefixtures("fake_vad")
def test_the_sound_of_a_video_keeps_the_videos_frame_grid(make_clip: MakeClip) -> None:
    from fractions import Fraction

    sound_only = make_clip("kept.m4a", seconds=6.0)
    res = pipeline.transcribe_file(sound_only, engine=FakeEngine(), picture=(True, Fraction(30000, 1001)))
    m = res.document.media
    assert m.has_video and m.frame_rate is not None and (m.frame_rate.num, m.frame_rate.den) == (30000, 1001)
    plain = pipeline.transcribe_file(sound_only, engine=FakeEngine())
    assert not plain.document.media.has_video and plain.document.media.frame_rate is None


@pytest.mark.usefixtures("fake_vad")
def test_every_progress_event_moves_forward_and_ends_at_one(make_clip: MakeClip) -> None:
    clip = make_clip("t.m4a", seconds=31.0, video_rate="25")
    events: list[pipeline.Progress] = []
    eng = FakeEngine()
    res = pipeline.transcribe_file(clip, engine=eng, on_progress=events.append)
    fr = [e.fraction for e in events]
    assert fr == sorted(fr) and fr[0] == 0.0 and fr[-1] == 1.0
    assert [e.phase for e in events].count("done") == 1
    assert {e.phase for e in events} == {"load", "decode", "speech", "recognise", "done"}
    assert all(e.media_done_s <= e.media_total_s for e in events)
    assert all(e.fraction < 1.0 for e in events if e.phase != "done")
    doc = res.document
    assert doc.media.has_video and doc.media.frame_rate is not None and doc.media.frame_rate.num == 25
    assert doc.run_config.engine == "fake" and doc.run_config.versions["app"]
    assert len(doc.segments) == 7 and doc.words()[0].text == "w0."
    assert eng.loaded and not eng.closed  # a passed-in engine stays loaded for the next job


@pytest.mark.usefixtures("fake_vad")
def test_cancel_during_recognition_raises_and_closes_own_engine(
    make_clip: MakeClip, monkeypatch: pytest.MonkeyPatch
) -> None:
    clip = make_clip("c.wav", seconds=31.0)
    token = CancelToken()
    made: list[FakeEngine] = []

    def create(_name: str, **_o: object) -> FakeEngine:
        made.append(FakeEngine(cancel_after=2, cancel=token))
        return made[-1]

    monkeypatch.setattr(engines, "create", create)
    with pytest.raises(Cancelled):
        pipeline.transcribe_file(clip, cancel=token)
    assert made[0].closed
