"""Exporters: byte-exact golden files, independent re-parsing, and the spreadsheet read back."""

from __future__ import annotations

import io
import os
from pathlib import Path

import pysubs2
import pytest
from openpyxl import load_workbook

from parakeet_stt import export
from parakeet_stt.export import txt
from parakeet_stt.transcript import Cue, FrameRate
from tests import factories

GOLDEN = Path(__file__).parent / "golden"
TEXT_FORMATS = ("srt", "vtt", "ass", "txt")


def _render(fmt: str) -> bytes:
    return export.FORMATS[fmt](factories.document(), factories.track())


@pytest.mark.parametrize("fmt", TEXT_FORMATS)
def test_matches_the_golden_file_byte_for_byte(fmt: str) -> None:
    path = GOLDEN / f"talk.cz-template.{fmt}"
    if os.environ.get("PSTT_REGEN_GOLDEN") == "1":  # deliberate regeneration only; review the diff afterwards
        path.write_bytes(_render(fmt))
        pytest.skip("golden file rewritten; review the diff, then run again without the switch")
    assert _render(fmt) == path.read_bytes()


def test_srt_and_txt_are_windows_text_vtt_and_ass_plain_utf8() -> None:
    for fmt in ("srt", "txt"):
        data = _render(fmt)
        assert data.startswith(b"\xef\xbb\xbf") and b"\r\n" in data and b"\n" not in data.replace(b"\r\n", b"")
    for fmt in ("vtt", "ass"):
        data = _render(fmt)
        assert not data.startswith(b"\xef\xbb\xbf") and b"\r" not in data


@pytest.mark.parametrize("fmt", ["srt", "vtt", "ass"])
def test_an_independent_reader_gets_the_same_cues_back(fmt: str) -> None:
    track = factories.track()
    # pysubs2 keeps the CR of CRLF files in the text; every player drops it
    text = _render(fmt).decode("utf-8-sig").replace("\r\n", "\n")
    subs = pysubs2.SSAFile.from_string(text, format_=fmt)
    tolerance = 10 if fmt == "ass" else 0  # ASS stores centiseconds
    assert len(subs) == len(track.cues)
    for ev, c in zip(subs, track.cues, strict=True):
        assert abs(ev.start - c.start_ms) <= tolerance and abs(ev.end - c.end_ms) <= tolerance
        if fmt == "ass":
            assert ev.text.replace("\\{", "{").replace("\\}", "}").split("\\N") == c.lines
        elif fmt == "srt":
            assert ev.plaintext.split("\n") == c.lines or "<b>" in c.lines[0]  # pysubs2 strips the bold tag
    assert subs[1].plaintext == "- Yes."


def test_vtt_escapes_markup_characters() -> None:
    text = _render("vtt").decode("utf-8")
    assert "=1+1 &amp; &lt;b&gt;bold&lt;/b&gt; {brace}" in text


def test_txt_rows_parse_back_with_frame_timecodes() -> None:
    track = factories.track(fps=FrameRate(num=30000, den=1001))
    rows = txt.render(factories.document(), track).decode("utf-8-sig").split("\r\n")
    assert rows[-1] == ""
    start, end, text = rows[0].split("\t")
    assert (start, end) == ("00:00:01:00", "00:00:03:00")
    assert text.split(txt.LINE_BREAK) == track.cues[0].lines


def test_xlsx_keeps_every_text_exactly_as_text() -> None:
    wb = load_workbook(io.BytesIO(_render("xlsx")))
    ws = wb["Subtitles"]
    assert [c.value for c in ws[1]] == ["#", "Start", "End", "Duration (s)", "CPS", "English", "Translation"]
    row2, row3, row4 = ws[2], ws[3], ws[4]
    assert (row2[1].value, row2[1].data_type) == ("00:00:01:00", "s")  # not turned into a time
    assert row2[5].value == "Of course, the mind is open\nlike space – naïve café."  # noqa: RUF001 - en dash on purpose
    assert (row3[5].value, row4[5].value) == ("- Yes.", "=1+1 & <b>bold</b> {brace}")
    assert row4[5].data_type == "s"  # not a formula
    assert row2[3].value == 2.0 and row2[6].value in ("", None)
    about = {str(r[0].value): str(r[1].value) for r in wb["About"].iter_rows()}
    assert about["Preset"].startswith("cz-template") and about["Rules"] == "0123456789abcdef"


def test_xlsx_drops_characters_excel_cannot_store() -> None:
    track = factories.track(cues=[Cue(id=1, start_ms=1000, end_ms=3000, lines=["bell\x07 rings"])])
    wb = load_workbook(io.BytesIO(export.FORMATS["xlsx"](factories.document(), track)))
    assert wb["Subtitles"]["F2"].value == "bell rings"


@pytest.mark.parametrize("fmt", [*TEXT_FORMATS, "xlsx"])
def test_exports_say_how_they_were_made_but_not_where_or_on_what(fmt: str) -> None:
    doc = factories.document()
    data = _render(fmt)
    if fmt == "xlsx":
        wb = load_workbook(io.BytesIO(data))
        data = "\n".join(str(c.value) for row in wb["About"].iter_rows() for c in row).encode()
    assert doc.media.source.encode() not in data and b"lectures" not in data
    assert b"CUDA" not in data and b"NVIDIA" not in data
    if fmt in ("vtt", "ass", "xlsx"):
        assert b"TalkSubs 0.1.0" in data and b"0123456789abcdef" in data


def test_file_names_carry_the_preset() -> None:
    assert export.file_name("talk", "en-subtitles", "srt") == "talk.en-subtitles.srt"
