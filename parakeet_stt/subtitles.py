"""Subtitle tracks for a document and the files made from them - shared by the command line and the server."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from parakeet_stt import export, presets, rules
from parakeet_stt.glossary import Glossary
from parakeet_stt.presets import Preset
from parakeet_stt.segment import build_track
from parakeet_stt.transcript import Document, SubtitleTrack


class SubtitleError(Exception):
    """A request about subtitles cannot be done; the message is for the user."""


@dataclass(frozen=True)
class TrackReport:
    track: SubtitleTrack
    reused: bool
    violations: list[rules.Violation]

    def line(self) -> str:
        s = rules.summary(self.violations)
        hard = ", ".join(f"{k} {v}" for k, v in s["hard"].items()) or "none"
        soft = ", ".join(f"{k} {v}" for k, v in s["soft"].items()) or "none"
        how = ("kept, edited" if self.track.edited else "kept") if self.reused else "made"
        return f"{self.track.preset_id}: {len(self.track.cues)} cues ({how}); rule breaks - hard: {hard}; soft: {soft}"


def ensure_tracks(
    doc: Document, preset_ids: Sequence[str], gl: Glossary | None = None, book: Mapping[str, Preset] | None = None
) -> tuple[Document, list[TrackReport]]:
    """A track for every preset: the stored one if it still matches its preset, else a fresh one.

    A track a person edited is always kept as it is, even if its preset changed since: it is checked and
    exported with the rule values it was made with (its own snapshot; the word lists are today's). Only the
    editor's "start over" remakes it. A track whose type no longer exists (the user removed their own type) is kept
    and exported with its own snapshot; `book` holds the types (default: the built-in ones).
    """
    reports = []
    types = book if book is not None else presets.registry()
    for pid in preset_ids:
        stored = doc.track(pid)
        preset = types.get(pid)
        if preset is None:
            if stored is None:
                raise presets.PresetError(
                    f"The subtitle type {pid!r} does not exist (any more), and this lecture has no subtitles made "
                    "with it."
                )
            lang = presets.language(stored.preset.language)
            reports.append(TrackReport(stored, True, rules.check_track(stored, doc.media.decoded_ms, lang)))
            continue
        lang = presets.language(preset.language)
        if stored is not None and (stored.edited or stored.digest == presets.track_digest(preset, lang, gl)):
            track, reused = stored, True
        else:
            track, reused = build_track(doc, preset, lang, gl), False
            doc = doc.with_track(track)
        reports.append(TrackReport(track, reused, rules.check_track(track, doc.media.decoded_ms, lang)))
    return doc, reports


def files(  # noqa: PLR0917 - every caller passes the same five, positionally
    doc: Document,
    preset_ids: Sequence[str],
    formats: Sequence[str],
    out_dir: Path,
    stem: str,
    book: Mapping[str, Preset] | None = None,
) -> dict[Path, bytes]:
    """The files of the tracks; an own type's files are named by its current name (from `book`), so a renamed type
    names its files the new way even where its track keeps the old snapshot."""
    out = {}
    for pid in preset_ids:
        track = doc.track(pid)
        if track is None:
            raise SubtitleError(f"no {pid!r} subtitles in this project")
        name = book[pid].name if book is not None and pid in book else track.preset.name
        for fmt in formats:
            path, data = export.export(doc, track, fmt, out_dir, stem, name=name)
            out[path] = data
    return out


TRANSLATION_DEFAULT_LANGUAGE = "cs"  # the same default as the editor's (web/src/views/EditorPage.tsx)


def translated(track: SubtitleTrack) -> SubtitleTrack | None:
    """The track as its translation: the translated cues with their translation as their text (a cue not translated
    yet is left out, never shown in English among the translation); None when nothing is translated."""
    # blank lines typed inside a translation would break a subtitle block in two: they are left out of the files
    cues = [
        c.model_copy(update={"lines": [line for line in c.translation if line.strip()]})
        for c in track.cues
        if c.translation
    ]
    return track.model_copy(update={"cues": cues}) if cues else None


def files_for(  # noqa: PLR0917 - the export's own arguments, positionally like `files`
    doc: Document,
    preset_ids: Sequence[str],
    formats: Sequence[str],
    texts: Sequence[str],
    out_dir: Path,
    stem: str,
    book: Mapping[str, Preset] | None = None,
) -> tuple[dict[Path, bytes], list[str]]:
    """The files of the chosen texts ("original", "translation") of the tracks, and the types skipped because none of
    their cues is translated. A spreadsheet holds both texts, so it is written once, with the original."""
    out = files(doc, preset_ids, formats, out_dir, stem, book) if "original" in texts else {}
    skipped = []
    if "translation" in texts:
        for pid in preset_ids:
            track = doc.track(pid)
            view = translated(track) if track is not None else None
            if track is None or view is None:
                # not reported only when its spreadsheet (which holds both texts) was written with the original
                if not ("original" in texts and all(f == "xlsx" for f in formats)):
                    skipped.append(pid)
                continue
            name = book[pid].name if book is not None and pid in book else track.preset.name
            lang = track.translation_language or TRANSLATION_DEFAULT_LANGUAGE
            for fmt in formats:
                if fmt == "xlsx":
                    if "original" not in texts:
                        path, data = export.export(doc, track, fmt, out_dir, stem, name=name)
                        out[path] = data
                    continue
                path, data = export.export(doc, view, fmt, out_dir, stem, name=name, lang=lang)
                out[path] = data
    return out, skipped
