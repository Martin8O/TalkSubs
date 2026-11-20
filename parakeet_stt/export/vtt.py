"""WebVTT (.vtt): UTF-8 without BOM, LF line ends; run facts in a NOTE block."""

from __future__ import annotations

from parakeet_stt.export.common import about, clock
from parakeet_stt.transcript import Document, SubtitleTrack


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render(doc: Document, track: SubtitleTrack) -> bytes:
    # a NOTE may not contain "-->" nor a blank line
    note = "\n".join(f"{k}: {v}".replace("-->", "->") for k, v in about(doc, track))
    parts = ["WEBVTT", f"NOTE\n{note}"]
    for c in track.cues:
        lines = "\n".join(_escape(ln) for ln in c.lines)
        parts.append(f"{clock(c.start_ms, '.')} --> {clock(c.end_ms, '.')}\n{lines}")
    return ("\n\n".join(parts) + "\n").encode("utf-8")
