"""Advanced SubStation Alpha (.ass) via pysubs2: a 1920x1080 canvas with a readable default style; run facts as
comment lines in [Script Info]. ASS stores centiseconds, so times may move by up to 5 ms."""

from __future__ import annotations

import pysubs2

from parakeet_stt.export.common import about
from parakeet_stt.transcript import Document, SubtitleTrack

PLAY_RES = (1920, 1080)
FONT, FONT_SIZE, OUTLINE, SHADOW, MARGIN_V = "Arial", 64, 3.0, 1.0, 60


def _escape(text: str) -> str:
    # braces open override tags; a backslash starts a command
    lookalike = chr(0x29F5)  # a backslash look-alike, so the text can never start a command
    return text.replace("\\", lookalike).replace("{", "\\{").replace("}", "\\}")


def render(doc: Document, track: SubtitleTrack) -> bytes:
    subs = pysubs2.SSAFile()
    subs.info["Title"] = doc.media.name
    subs.info["PlayResX"], subs.info["PlayResY"] = str(PLAY_RES[0]), str(PLAY_RES[1])
    style = subs.styles["Default"]
    style.fontname, style.fontsize, style.outline, style.shadow, style.marginv = (
        FONT, FONT_SIZE, OUTLINE, SHADOW, MARGIN_V,
    )  # fmt: skip
    for c in track.cues:
        subs.append(pysubs2.SSAEvent(start=c.start_ms, end=c.end_ms, text="\\N".join(_escape(ln) for ln in c.lines)))
    text = subs.to_string("ass")
    head = "[Script Info]\n"
    comments = "".join(f"; {k}: {v}\n" for k, v in about(doc, track))
    return text.replace(head, head + comments, 1).encode("utf-8")
