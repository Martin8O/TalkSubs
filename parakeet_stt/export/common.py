"""What every exported file says about how it was made, and shared time formats."""

from __future__ import annotations

from parakeet_stt.transcript import Document, SubtitleTrack

BOM = "﻿"
CRLF = "\r\n"


def about(doc: Document, track: SubtitleTrack) -> list[tuple[str, str]]:
    """Facts for a file header. No media folder, no machine details: exports get passed around."""
    fps = track.frame_rate
    return [
        ("Made with", f"TalkSubs {doc.app_version}"),
        ("Engine", f"{doc.run_config.engine} ({doc.run_config.model})"),
        ("Preset", f"{track.preset_id} - {track.preset.name}"),
        ("Rules", track.digest),
        ("Frame rate", f"{fps.num}/{fps.den}"),
        ("Created", f"{track.created_utc:%Y-%m-%d %H:%M} UTC"),
        ("Edited", "yes" if track.edited else "no"),
    ]


def clock(ms: int, sep: str) -> str:
    """HH:MM:SS<sep>mmm (SRT uses ',', WebVTT '.')."""
    h, rest = divmod(ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    s, milli = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{milli:03d}"
