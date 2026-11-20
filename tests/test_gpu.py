"""End-to-end on the real model and the evaluation lectures (marker `gpu`; the gate requires them)."""

from __future__ import annotations

import re
from collections.abc import Iterator

import jiwer
import pytest

from parakeet_stt import engines, pipeline, presets, rules
from parakeet_stt.engines.base import EngineError
from parakeet_stt.engines.parakeet import ParakeetEngine
from parakeet_stt.segment import build_track
from tests.conftest import EVAL

pytestmark = pytest.mark.gpu
# Measured 0.8 % on this excerpt; the reference was partly adjudicated from engine output, so allow margin.
MAX_WER = 0.03


@pytest.fixture(scope="module")
def engine() -> Iterator[ParakeetEngine]:
    e = ParakeetEngine()
    e.load()
    yield e
    e.close()


@pytest.fixture(scope="module")
def lecture(engine: ParakeetEngine) -> pipeline.Result:
    return pipeline.transcribe_file(EVAL / "lecture-a.wav", engine=engine)


def _norm(text: str) -> str:
    t = re.sub(r"[^a-z0-9' ]+", " ", text.lower().replace("-", " ").replace("’", "'"))  # noqa: RUF001 - typographic apostrophe on purpose
    return " ".join(t.split())


def test_noise_and_silence_produce_no_words(engine: ParakeetEngine) -> None:
    res = pipeline.transcribe_file(EVAL / "bait-noise.wav", engine=engine)
    assert res.document.words() == []


def test_accuracy_on_the_checked_excerpt(lecture: pipeline.Result) -> None:
    ref = _norm((EVAL / "lecture-a.600-780.ref.txt").read_text(encoding="utf-8"))
    hyp = _norm(" ".join(w.text for w in lecture.document.words() if 600_000 <= w.start_ms < 780_000))
    assert jiwer.wer(ref, hyp) <= MAX_WER


def test_word_at_a_window_join_is_kept(lecture: pipeline.Result) -> None:
    # a word once fell into no window here: the window length cap met a short pause
    near = [w.text for w in lecture.document.words() if 688_000 <= w.start_ms < 692_000]
    assert "Of" in near and "course" in near


def test_real_tokens_build_clean_words(lecture: pipeline.Result) -> None:
    words = lecture.document.words()
    assert len(words) > 3000
    assert not [w.text for w in words if " " in w.text or not w.text.strip()]
    assert all(w.conf is not None and 0 < w.conf <= 1 for w in words)
    assert lecture.document.run_config.provider == "CUDAExecutionProvider"


def test_a_second_gpu_job_is_refused_while_one_holds_the_gpu(engine: ParakeetEngine) -> None:
    with pytest.raises(EngineError, match="Another transcription"):
        engines.create("parakeet").load()


@pytest.mark.parametrize("preset_id", presets.names())
def test_a_real_lecture_segments_with_no_hard_rule_breaks(lecture: pipeline.Result, preset_id: str) -> None:
    doc = lecture.document
    track = build_track(doc, presets.get(preset_id))
    hard = [v for v in rules.check_track(track, doc.media.decoded_ms) if v.severity == "hard"]
    assert hard == []
    assert len(track.cues) > 200  # a 22-minute lecture
