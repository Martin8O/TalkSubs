"""Timecoded text (.txt): one cue per line - `HH:MM:SS:FF<TAB>HH:MM:SS:FF<TAB>line 1|line 2` - in UTF-8 with
BOM and CRLF, so it pastes straight into a spreadsheet or a translation tool. `|` marks a line break."""

from __future__ import annotations

from parakeet_stt.export.common import BOM, CRLF
from parakeet_stt.timecode import timecode
from parakeet_stt.transcript import Document, SubtitleTrack

LINE_BREAK = "|"


def render(doc: Document, track: SubtitleTrack) -> bytes:
    del doc
    fps = track.frame_rate
    rows = [f"{timecode(c.start_ms, fps)}\t{timecode(c.end_ms, fps)}\t{LINE_BREAK.join(c.lines)}" for c in track.cues]
    return (BOM + "".join(r + CRLF for r in rows)).encode("utf-8")
