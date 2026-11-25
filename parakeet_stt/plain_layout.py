"""The "plain" output layout: `<stem>.srt`, `.vtt`, `.txt` and `<stem>.json`, the file names and the JSON shape a
WhisperX run leaves (`segments[]` with `start`/`end` in seconds, `text` and `words[]` of `word`/`start`/`end`/`score`).
Text files are plain UTF-8 with LF line ends and no BOM, as WhisperX writes them (the standard exporters write the BOM
and CRLF that Windows tools expect). Writing `.vtt` and `.txt` too keeps a re-run from leaving an older run's files
beside the new `.srt`.

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


def _word(w: Word) -> dict[str, object]:
    out: dict[str, object] = {"word": w.text, "start": _seconds(w.start_ms), "end": _seconds(w.end_ms)}
    if w.conf is not None:
        out["score"] = round(w.conf, 3)
    return out


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
                "words": [_word(w) for w in mine],
            }
        )
    return {"engine": doc.run_config.engine, "model": doc.run_config.model, "segments": segments}


_BOM = "\ufeff".encode()


def _plain_text(data: bytes) -> bytes:
    """No BOM, LF line ends."""
    return data.removeprefix(_BOM).replace(b"\r\n", b"\n")


def files(doc: Document, track: SubtitleTrack, gl: Glossary | None, out_dir: Path, stem: str) -> dict[Path, bytes]:
    """`<stem>.srt`, `.vtt`, `.txt` and `<stem>.json` of the track."""
    out = {out_dir / f"{stem}.{fmt}": _plain_text(export.FORMATS[fmt](doc, track)) for fmt in ("srt", "vtt", "txt")}
    out[out_dir / f"{stem}.json"] = json.dumps(build(doc, track, gl), ensure_ascii=False, indent=2).encode("utf-8")
    return out
