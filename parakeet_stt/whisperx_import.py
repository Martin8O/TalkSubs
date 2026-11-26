"""A WhisperX result as a transcript document, so the subtitle rules and the word lists of this app can cut it.

WhisperX recognises well when the language is fixed (Parakeet v3 cannot be told one) but its cues are long: whole
sentences of 6 to 8 s and 70 to 110 characters. The words and their times in its JSON are all the subtitle rules
need, so a caller can keep WhisperX for the recognition and let `rechunk` make the cues.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from parakeet_stt.transcript import Document, Media, RunConfig, Segment, VadConfig, Word

_PAUSE_MS = 800  # a gap this long starts a new segment, as a pause between two speech windows does
_MAX_SEGMENT_MS = 25_000
_FALLBACK_WORD_MS = 250
# WhisperX stretches the last word before a pause across it ("La." lasted 11 s); spoken words do not last that long
_MAX_WORD_MS = 1200


def _ms(seconds: float) -> int:
    return max(0, round(seconds * 1000))


def words_from(data: dict[str, Any]) -> list[Word]:
    """Every word of the JSON in time order. Words WhisperX could not align (a number, a symbol) have no times: they
    take the gap between their neighbours. A segment without any aligned word is spread over its own span."""
    raw: list[tuple[str, float | None, float | None, float | None]] = []
    for seg in data.get("segments", []):
        words = seg.get("words") or []
        if not words and seg.get("text", "").strip():
            tokens = seg["text"].split()
            seg_start, seg_end = float(seg["start"]), float(seg["end"])
            step = (seg_end - seg_start) / len(tokens)
            words = [
                {"word": t, "start": seg_start + i * step, "end": seg_start + (i + 1) * step}
                for i, t in enumerate(tokens)
            ]
        for w in words:
            text = str(w.get("word", "")).strip()
            if text:
                raw.append((text, w.get("start"), w.get("end"), w.get("score")))
    out: list[Word] = []
    prev_end = 0
    for i, (text, start, end, score) in enumerate(raw):
        nxt = next((_ms(s) for _t, s, _e, _c in raw[i + 1 :] if s is not None), None)
        start_ms = _ms(start) if start is not None else prev_end
        start_ms = max(start_ms, out[-1].start_ms if out else 0)  # starts never go backwards
        if end is not None:
            end_ms = _ms(end)
        else:
            end_ms = min(start_ms + _FALLBACK_WORD_MS, nxt) if nxt is not None else start_ms + _FALLBACK_WORD_MS
        end_ms = min(max(end_ms, start_ms), start_ms + _MAX_WORD_MS)
        conf = float(score) if isinstance(score, (int, float)) and 0.0 <= float(score) <= 1.0 else None
        out.append(Word(text=text, start_ms=start_ms, end_ms=end_ms, conf=conf))
        prev_end = end_ms
    return out


def clamp(words: list[Word], limit_ms: int) -> list[Word]:
    """WhisperX can end the last word a few milliseconds past the audio (13 ms seen): times stay inside it."""
    out: list[Word] = []
    for w in words:
        if w.start_ms >= limit_ms:
            continue
        out.append(w if w.end_ms <= limit_ms else Word(text=w.text, start_ms=w.start_ms, end_ms=limit_ms, conf=w.conf))
    return out


def segments_from(words: list[Word]) -> list[Segment]:
    """Speech stretches: a pause, or the length bound, starts the next one; they never overlap."""
    segments: list[Segment] = []
    group: list[Word] = []
    end = 0
    for w in words:
        pause = w.start_ms >= end + _PAUSE_MS
        too_long = w.end_ms - group[0].start_ms > _MAX_SEGMENT_MS if group else False
        if group and (pause or too_long) and w.start_ms >= end:
            segments.append(Segment(start_ms=group[0].start_ms, end_ms=end, words=group))
            group = []
        group.append(w)
        end = max(end, w.end_ms)
    if group:
        segments.append(Segment(start_ms=group[0].start_ms, end_ms=end, words=group))
    return segments


def document_from_whisperx(
    data: dict[str, Any], media: Media, run_config: RunConfig | None = None, app_version: str = "0"
) -> Document:
    words = clamp(words_from(data), media.decoded_ms)
    if not words:
        raise ValueError("The WhisperX result has no words.")
    config = run_config or RunConfig(
        engine="whisperx",
        model="whisperx",
        provider="external",
        engine_options={},
        vad=VadConfig(
            model="whisperx",
            threshold=0.0,
            min_speech_duration_ms=0,
            min_silence_duration_ms=0,
            speech_pad_ms=0,
            max_speech_duration_s=0.0,
        ),
        decode_sample_rate_hz=media.sample_rate_hz,
        decode_channels=media.channels,
        versions={"app": app_version},
        created_utc=datetime.now(UTC),
    )
    return Document(app_version=app_version, media=media, run_config=config, segments=segments_from(words))
