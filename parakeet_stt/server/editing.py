"""The subtitle editor's work on a finished job's project file: open a track, check cues, save, start over.

Everything here runs in a worker thread under the job's project lock (`Runner.project_lock`), so an export,
a save and a removal of the same job never interleave their read-modify-write of the project file.

A track's revision is a fingerprint of what is stored, not a counter: it survives restarts, and a save whose
base revision is not the stored one is refused (another window or an export changed the file meanwhile).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from parakeet_stt import glossary, presets, project, rules, subtitles, tidy
from parakeet_stt.glossary import Glossary
from parakeet_stt.segment import build_track
from parakeet_stt.transcript import Cue, Document, SubtitleTrack, Word
from parakeet_stt.vault import Vault


class EditError(Exception):
    """An editor request cannot be done; the message is for the user."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class Stale(EditError):
    def __init__(self) -> None:
        super().__init__(
            "These subtitles were changed elsewhere (another window or an export) since they were opened. "
            "Open them again to continue.",
            409,
        )


@dataclass(frozen=True)
class Opened:
    track: SubtitleTrack
    revision: str
    media_end_ms: int
    violations: list[rules.Violation]
    preset_changed: bool  # the preset's rules or word lists changed since this (edited) track was made


def revision(track: SubtitleTrack) -> str:
    body = {"digest": track.digest, "next": track.next_cue_id, "cues": [c.model_dump() for c in track.cues]}
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()[:20]


def _load(vault: Vault, project_file: Path) -> Document:
    try:
        return project.load(project_file, vault)
    except project.ProjectError as exc:
        raise EditError(str(exc), 409) from None


Book = Mapping[str, presets.Preset]


def _types(book: Book | None) -> Book:
    return book if book is not None else presets.registry()


def _opened(track: SubtitleTrack, doc: Document, book: Book | None = None) -> Opened:
    end = doc.media.decoded_ms
    preset = _types(book).get(track.preset_id)
    # the rules only: a changed glossary is not something "start over" has to fix for an edited track; a type that
    # no longer exists has no newer rules (the track keeps its own)
    changed = preset is not None and presets.rules_part(track.digest) != presets.digest(
        preset, presets.language(preset.language)
    )
    return Opened(track, revision(track), end, rules.check_track(track, end), changed)


def open_track(
    vault: Vault, project_file: Path, preset_id: str, *, gl: Glossary | None = None, book: Book | None = None
) -> Opened:
    """The stored track; a missing or outdated untouched one is made now and kept. Edited tracks are never remade,
    and a track whose type the user removed opens as it is."""
    doc = _load(vault, project_file)
    if preset_id not in _types(book) and doc.track(preset_id) is None:
        raise EditError(
            "This subtitle type does not exist (any more), and this lecture has no subtitles made with it.", 404
        )
    try:
        new_doc, reports = subtitles.ensure_tracks(doc, [preset_id], gl, book)
    except (presets.PresetError, subtitles.SubtitleError) as exc:
        raise EditError(str(exc), 409) from None
    if not reports[0].reused:
        project.save(new_doc, project_file, vault)
    return _opened(reports[0].track, new_doc, book)


def words(
    vault: Vault,
    project_file: Path,
    preset_id: str | None = None,
    *,
    gl: Glossary | None = None,
    book: Book | None = None,
) -> list[Word]:
    """The words as the cues of this type have them (tidied by its rules, the glossary applied), so the editor's
    splits line up with the cue text word for word; without a type, as recognised (with the glossary)."""
    doc = _load(vault, project_file)
    track = doc.track(preset_id) if preset_id else None
    preset = track.preset if track is not None else _types(book).get(preset_id or "")
    if preset is None:
        return (gl or glossary.builtin()).apply(doc.words())
    return (gl or glossary.builtin()).apply(tidy.words_for_cues(doc, preset, presets.language(preset.language)))


@dataclass(frozen=True)
class Checker:
    """What the rule checker needs to judge cues the editor has not saved yet."""

    track: SubtitleTrack
    media_end_ms: int

    def check(self, cues: list[Cue]) -> list[rules.Violation]:
        return rules.check(cues, self.track.preset, self.track.frame_rate, self.media_end_ms)


def checker(vault: Vault, project_file: Path, preset_id: str) -> Checker:
    doc = _load(vault, project_file)
    track = doc.track(preset_id)
    if track is None:
        raise EditError("Open these subtitles first.", 409)
    return Checker(track, doc.media.decoded_ms)


def _stored(doc: Document, preset_id: str, base_revision: str) -> SubtitleTrack:
    stored = doc.track(preset_id)
    if stored is None or revision(stored) != base_revision:
        raise Stale
    return stored


def save_track(  # noqa: PLR0917 - the runner calls every editor step the same way, positionally
    vault: Vault,
    project_file: Path,
    preset_id: str,
    base_revision: str,
    cues: list[Cue],
    next_cue_id: int,
    translation_language: str | None = None,
    *,
    gl: Glossary | None = None,
    book: Book | None = None,
) -> Opened:
    """Replace the cues of a stored track (now marked edited); ids are never reused. A translation language given is
    kept with the track."""
    doc = _load(vault, project_file)
    stored = _stored(doc, preset_id, base_revision)
    top = max((c.id for c in cues), default=0)
    fields = {**{n: getattr(stored, n) for n in SubtitleTrack.model_fields}, "cues": cues, "edited": True,
              "next_cue_id": max(stored.next_cue_id, next_cue_id, top + 1),
              "translation_language": translation_language or stored.translation_language}  # fmt: skip
    try:
        new_doc = doc.with_track(SubtitleTrack(**fields))
    except ValidationError as exc:
        raise EditError(f"These subtitles cannot be saved: {exc.errors()[0]['msg']}", 422) from None
    project.save(new_doc, project_file, vault)
    track = new_doc.track(preset_id)
    assert track is not None
    return _opened(track, new_doc, book)


def resegment(
    vault: Vault,
    project_file: Path,
    preset_id: str,
    base_revision: str,
    *,
    gl: Glossary | None = None,
    book: Book | None = None,
) -> Opened:
    """Start the track over from the recognised words with the type as it is now (a removed type: with the track's
    own rules); new cues take new ids."""
    doc = _load(vault, project_file)
    stored = _stored(doc, preset_id, base_revision)
    if any(c.translation for c in stored.cues):  # hours of work the undo history alone cannot keep safe
        raise EditError("These subtitles have a translation, so they cannot be made again from the recording.", 409)
    fresh = build_track(doc, _types(book).get(preset_id) or stored.preset, gl=gl)
    first = stored.next_cue_id
    cues = [c.model_copy(update={"id": first + i}) for i, c in enumerate(fresh.cues)]
    track = fresh.model_copy(update={"cues": cues, "next_cue_id": first + len(cues)})
    new_doc = doc.with_track(SubtitleTrack.model_validate(track.model_dump()))
    project.save(new_doc, project_file, vault)
    out = new_doc.track(preset_id)
    assert out is not None
    return _opened(out, new_doc, book)
