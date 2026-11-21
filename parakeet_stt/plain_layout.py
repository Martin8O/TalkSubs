"""The "plain" output layout: `<stem>.srt` and `<stem>.json`, the file names and the JSON shape a WhisperX run leaves
(`segments[]` with `start`/`end` in seconds, `text` and `words[]` of `word`/`start`/`end`/`score`).

For callers that already read those files (a batch pipeline that collects "the .srt next to the media" and reads
cue times from the JSON): they switch the engine without changing how they read the result. The words carry the
glossary's corrections; one segment per subtitle cue of the chosen track.
"""

from __future__ import annotations

import json
from pathlib import Path

from parakeet_stt import export
from parakeet_stt.glossary import Glossary
from parakeet_stt.transcript import Document, SubtitleTrack, Word


def _seconds(ms: int) -> float:
    return round(ms / 1000, 3)


def build(doc: Document, track: SubtitleTrack, gl: Glossary | None) -> dict[str, object]:
    """The WhisperX-shaped result; every word goes to the cue its start time falls in."""
    words: list[Word] = [w for seg in doc.segments for w in seg.words]
    if gl is not None:
        words = gl.apply(words)
    segments: list[dict[str, object]] = []
    i = 0
    for n, cue in enumerate(track.cues):
        last = n == len(track.cues) - 1
        mine: list[Word] = []
        while i < len(words) and (last or words[i].start_ms < cue.end_ms):
            mine.append(words[i])
            i += 1
        segments.append(
            {
                "start": _seconds(cue.start_ms),
                "end": _seconds(cue.end_ms),
                "text": " ".join(cue.lines),
                "words": [
                    {"word": w.text, "start": _seconds(w.start_ms), "end": _seconds(w.end_ms),
                     **({"score": round(w.conf, 3)} if w.conf is not None else {})}  # fmt: skip
                    for w in mine
                ],
            }
        )
    return {"engine": doc.run_config.engine, "model": doc.run_config.model, "segments": segments}


def files(doc: Document, track: SubtitleTrack, gl: Glossary | None, out_dir: Path, stem: str) -> dict[Path, bytes]:
    """`<stem>.srt` and `<stem>.json` of the track."""
    srt = export.FORMATS["srt"](doc, track)
    data = json.dumps(build(doc, track, gl), ensure_ascii=False, indent=2).encode("utf-8")
    return {out_dir / f"{stem}.srt": srt, out_dir / f"{stem}.json": data}
