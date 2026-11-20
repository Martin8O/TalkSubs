"""Excel (.xlsx) via openpyxl: one row per cue with HH:MM:SS:FF timecodes and a Translation column (the cue's
translation where there is one, else empty);
a second sheet with the run facts and the preset.

Every text goes in as a text cell: Excel would otherwise turn "00:01:02:03" into a time and "=..." into a
formula. Characters Excel cannot store (control codes) are dropped.
"""

from __future__ import annotations

import io

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from parakeet_stt import rules, timecode
from parakeet_stt.export.common import about
from parakeet_stt.transcript import Document, SubtitleTrack

HEADERS = ("#", "Start", "End", "Duration (s)", "CPS", "English", "Translation")
WIDTHS = (6, 13, 13, 12, 7, 50, 50)
TEXT_COLUMNS = (2, 3, 6, 7)


def _text(ws: Worksheet, row: int, col: int, value: str) -> None:
    cell = ws.cell(row=row, column=col)
    cell.value = ILLEGAL_CHARACTERS_RE.sub("", value)
    cell.data_type = "s"  # never a formula, a date or a number


def render(doc: Document, track: SubtitleTrack) -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Subtitles"
    for col, (name, width) in enumerate(zip(HEADERS, WIDTHS, strict=True), start=1):
        _text(ws, 1, col, name)
        ws.cell(row=1, column=col).font = Font(bold=True)
        ws.column_dimensions[get_column_letter(col)].width = width
    ws.freeze_panes = "A2"
    wrap = Alignment(wrap_text=True, vertical="top")
    fps = track.frame_rate
    for row, c in enumerate(track.cues, start=2):
        ws.cell(row=row, column=1, value=row - 1)
        _text(ws, row, 2, timecode.timecode(c.start_ms, fps))
        _text(ws, row, 3, timecode.timecode(c.end_ms, fps))
        ws.cell(row=row, column=4, value=round((c.end_ms - c.start_ms) / 1000, 3))
        ws.cell(row=row, column=5, value=round(rules.cps(c, track.preset), 1))
        _text(ws, row, 6, "\n".join(c.lines))
        _text(ws, row, 7, "\n".join(line for line in c.translation or [] if line.strip()))
        for col in TEXT_COLUMNS[2:]:
            ws.cell(row=row, column=col).alignment = wrap

    info = wb.create_sheet("About")
    lim = track.preset.limits
    facts = [*about(doc, track), ("Media", doc.media.name),
             ("Limits", f"{lim.max_chars_per_line} characters x {lim.max_lines} lines, "
                        f"{lim.min_duration_ms / 1000:g}-{lim.max_duration_ms / 1000:g} s, {lim.max_cps:g} CPS, "
                        f"gap {lim.min_gap_frames} frames")]  # fmt: skip
    for row, (k, v) in enumerate(facts, start=1):
        _text(info, row, 1, k)
        _text(info, row, 2, v)
    info.column_dimensions["A"].width, info.column_dimensions["B"].width = 14, 80

    created = track.created_utc.replace(tzinfo=None)
    wb.properties.created = wb.properties.modified = created
    wb.properties.creator = "TalkSubs"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
