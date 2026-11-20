"""Subtitle exporters: one track of one document -> the bytes of one file.

Every exporter takes the document (for the run facts) and a track (cues, preset, frame grid). File headers
carry the app version, engine model, preset and rule fingerprint - never the media's folder or the machine.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Final

from parakeet_stt.export import ass, srt, txt, vtt, xlsx
from parakeet_stt.transcript import Document, SubtitleTrack

Exporter = Callable[[Document, SubtitleTrack], bytes]

FORMATS: Final[dict[str, Exporter]] = {
    "srt": srt.render,
    "vtt": vtt.render,
    "ass": ass.render,
    "txt": txt.render,
    "xlsx": xlsx.render,
}


def file_name(stem: str, preset_id: str, fmt: str, name: str = "", lang: str = "") -> str:
    """`<stem>.<type>.<fmt>`; a user's own type is named by its name (plain letters) and the end of its id, so two
    own types never share a file name and a person can still tell them apart."""
    if preset_id.startswith("my-") and name:
        plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
        slug = re.sub(r"[^a-z0-9]+", "-", plain).strip("-")[:40] or "type"
        kind = f"{slug}-{preset_id[-4:]}"
    else:
        kind = preset_id
    return f"{stem}.{kind}.{lang}.{fmt}" if lang else f"{stem}.{kind}.{fmt}"


def export(
    doc: Document, track: SubtitleTrack, fmt: str, out_dir: Path, stem: str, *, name: str = "", lang: str = ""
) -> tuple[Path, bytes]:
    path = out_dir / file_name(stem, track.preset_id, fmt, name or track.preset.name, lang)
    return path, FORMATS[fmt](doc, track)
