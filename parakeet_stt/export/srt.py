"""SubRip (.srt): UTF-8 with BOM and CRLF line ends, as Windows subtitle tools expect. No header (the format has
no comment syntax)."""

from __future__ import annotations

from parakeet_stt.export.common import BOM, CRLF, clock
from parakeet_stt.transcript import Document, SubtitleTrack


def render(doc: Document, track: SubtitleTrack) -> bytes:
    del doc  # SRT has nowhere to put the run facts
    blocks = [
        CRLF.join([str(n), f"{clock(c.start_ms, ',')} --> {clock(c.end_ms, ',')}", *c.lines]) + CRLF
        for n, c in enumerate(track.cues, start=1)
    ]
    return (BOM + CRLF.join(blocks)).encode("utf-8")
