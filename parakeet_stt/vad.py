"""Speech windows (Silero VAD on the CPU), independent of the recognition engine.

Windows are produced lazily while the audio is scanned, so recognition can start on the first window
and progress follows the audio position actually processed.

Where the length cap forces two windows to meet at a short pause (or splits one long run of speech),
the library leaves either a stretch of audio in no window or a stretch in both. Here such a join gets one
boundary in the middle of the pause; both windows are recognised a padding's length past it, and each
word belongs to the window on whose side of the boundary its midpoint lies. So no audio is skipped and
no word is counted twice.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict, dataclass, replace

import numpy as np
import numpy.typing as npt

from parakeet_stt import gpu, models
from parakeet_stt.media import SAMPLE_RATE

VAD_MODEL = "silero"


@dataclass(frozen=True)
class VadOptions:
    threshold: float = 0.5
    min_speech_duration_ms: int = 250
    min_silence_duration_ms: int = 300
    speech_pad_ms: int = 200
    max_speech_duration_s: float = 25.0  # Parakeet's attention window is comfortable up to ~25 s

    def as_dict(self) -> dict[str, float]:
        return {k: float(v) for k, v in asdict(self).items()}


@dataclass(frozen=True)
class Window:
    """Audio to recognise (`start`..`end`) and the span whose words it owns (`own_start`..`own_end`), in samples."""

    start: int
    end: int
    own_start: int
    own_end: int

    @property
    def end_s(self) -> float:
        return self.end / SAMPLE_RATE

    @property
    def start_s(self) -> float:
        return self.start / SAMPLE_RATE

    @property
    def own_start_s(self) -> float:
        return self.own_start / SAMPLE_RATE

    @property
    def own_end_s(self) -> float:
        return self.own_end / SAMPLE_RATE


@dataclass
class JoinStats:
    windows: int = 0
    forced_joins: int = 0  # length cap met a short pause: the library would have left audio uncovered
    hard_splits: int = 0  # length cap cut one continuous run of speech: the library would have shared audio


def resolve_joins(
    raw: Iterator[tuple[int, int]], options: VadOptions, total: int, stats: JoinStats
) -> Iterator[Window]:
    min_gap = options.min_silence_duration_ms * SAMPLE_RATE // 1000
    pad = options.speech_pad_ms * SAMPLE_RATE // 1000
    prev: Window | None = None
    for start, end in raw:
        cur = Window(start, end, start, end)
        if prev is not None:
            gap = cur.start - prev.end
            if gap < min_gap:
                if gap < 0:
                    stats.hard_splits += 1
                else:
                    stats.forced_joins += 1
                boundary = (prev.end + cur.start) // 2
                prev = replace(prev, end=min(max(prev.end, boundary + pad), total), own_end=boundary)
                cur = replace(cur, start=max(min(cur.start, boundary - pad), 0), own_start=boundary)
            stats.windows += 1
            yield prev
        prev = cur
    if prev is not None:
        stats.windows += 1
        yield prev


class Vad:
    def __init__(self, options: VadOptions | None = None) -> None:
        self.options = options or VadOptions()
        self.stats = JoinStats()
        onnx_asr = gpu.onnx_asr()
        models.fetch(repos=(models.VAD,))  # 2 MB; normally already here (the app fetches it with the model)
        self._vad = onnx_asr.load_vad(VAD_MODEL, models.folder(models.VAD), providers=["CPUExecutionProvider"])

    def windows(self, audio: npt.NDArray[np.float32]) -> Iterator[Window]:
        self.stats = JoinStats()
        lens = np.array([len(audio)], dtype=np.int64)
        (segments,) = self._vad.segment_batch(audio[None, :], lens, SAMPLE_RATE, **self.options.as_dict())
        raw = ((int(s), int(e)) for s, e in segments)
        yield from resolve_joins(raw, self.options, len(audio), self.stats)
