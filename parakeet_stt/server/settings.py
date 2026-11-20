"""The user's settings in `<data>/settings.json`: defaults when missing, checked on every change."""

from __future__ import annotations

import getpass
import json
import logging
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from parakeet_stt import engines, export, glossary, presets
from parakeet_stt import vault as vault_mod
from parakeet_stt.project import write_atomically
from parakeet_stt.server.messages import SETTINGS_SCHEMA_VERSION, PresetInfo, Settings, SettingsView
from parakeet_stt.vault import Role, Vault, VaultError

log = logging.getLogger("parakeet_stt.server")
MAX_OWN_TYPES = 20
MAX_RETIRED_TYPES = 50

SETTINGS_FILE = "settings.json"
DEFAULT_OUTPUT_SUBDIR = "subtitles"
LOCKED_SAVE = "Settings cannot be saved while the app's work is locked."


class SettingsError(Exception):
    """The settings cannot be read or are not acceptable; the message is for the user."""


def is_plain_local_dir(text: str) -> bool:
    """An absolute folder on a drive of this machine (no device or long-path namespace prefixes, no UNC share)."""
    if text.startswith(("\\\\", "//")):
        return False
    p = Path(text)
    return p.is_absolute() and len(p.drive) == 2 and p.drive[1] == ":"


class SettingsStore:
    def __init__(self, data_dir: Path, vault: Vault | None) -> None:
        self.data_dir = data_dir
        self.path = data_dir / SETTINGS_FILE
        self.vault = vault  # None while the app's store is locked: defaults are shown, nothing is saved

    def load(self) -> Settings:
        if self.vault is None or not self.path.exists():
            return Settings()
        try:
            raw = json.loads(self.vault.read(self.path, Role.SETTINGS).decode("utf-8"))
        except VaultError as exc:
            raise SettingsError(f"The settings file {self.path} cannot be opened: {exc}") from None
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SettingsError(f"The settings file {self.path} is damaged ({exc}).") from None
        version = raw.get("schema_version") if isinstance(raw, dict) else None
        if isinstance(version, int) and version > SETTINGS_SCHEMA_VERSION:
            raise SettingsError(f"The settings file {self.path} was written by a newer version of TalkSubs.")
        try:
            settings = Settings.model_validate(raw)
        except ValidationError as exc:
            raise SettingsError(f"The settings file {self.path} is damaged: {exc.errors()[0]['msg']}") from None
        if settings.engine not in engines.names():  # an engine an earlier version offered (1.0.0: whisper)
            settings = settings.model_copy(update={"engine": Settings().engine})
        return settings

    def save(self, settings: Settings) -> Settings:
        settings = settings.model_copy(update={"schema_version": SETTINGS_SCHEMA_VERSION})  # an older file moves up
        check(settings)
        if settings.output_dir is not None and not vault_mod.plain_allowed(Path(settings.output_dir), self.data_dir):
            raise SettingsError(
                "That folder is inside the app's own encrypted store; choose another one (or the app's own folder)."
            )
        if self.vault is None:
            raise SettingsError(LOCKED_SAVE)
        text = settings.model_dump_json(indent=1) + "\n"
        write_atomically({self.path: self.vault.seal(text.encode("utf-8"), Role.SETTINGS)})
        return settings

    def output_dir(self, settings: Settings) -> Path:
        return Path(settings.output_dir) if settings.output_dir else self.data_dir / DEFAULT_OUTPUT_SUBDIR

    def book(self) -> dict[str, presets.Preset]:
        """The types subtitles can be made with: built-in and the user's own (built-in only while the settings cannot
        be read). A removed type is not in it: its subtitles made before open and save with their own snapshot."""
        try:
            return presets.book(self.load().custom_presets)
        except SettingsError:
            return presets.book()

    def known(self) -> dict[str, presets.Preset]:
        """Every type a save may name: the book plus the removed types."""
        try:
            return book_of(self.load())
        except SettingsError:
            return presets.book()

    def job_types(self) -> list[presets.Preset]:
        """The user's own types a new transcription makes subtitles for: the ticked ones (others are made when
        they are first opened or saved)."""
        try:
            s = self.load()
        except SettingsError:
            return []
        return [p for p in s.custom_presets if p.id in s.export_presets]

    def view(self, settings: Settings) -> SettingsView:
        reg = presets.registry()
        kinds: dict[str, Literal["custom", "retired"]] = {p.id: "custom" for p in settings.custom_presets}
        kinds |= {p.id: "retired" for p in settings.retired_presets}
        return SettingsView(
            settings=settings,
            output_dir_default=str(self.data_dir / DEFAULT_OUTPUT_SUBDIR),
            engines=engines.names(),
            presets=[
                PresetInfo(
                    id=p.id,
                    kind=kinds.get(p.id, "builtin"),
                    name=p.name,
                    description=p.description,
                    max_chars_per_line=p.limits.max_chars_per_line,
                    max_lines=p.limits.max_lines,
                    max_cps=p.limits.max_cps,
                    max_duration_ms=p.limits.max_duration_ms,
                )
                for p in ordered(list(book_of(settings).values()), settings.type_order)
            ],
            formats=list(export.FORMATS),
            account=_account(),
            glossary_builtin=glossary.as_text(glossary.builtin().entries),
            standards=list(presets.standards().values()),
            builtin_presets=list(reg.values()),
        )

    def own_glossary(self) -> str:
        """The user's own list of terms as typed ("" while locked or when the settings cannot be read)."""
        try:
            return self.load().glossary_text
        except SettingsError:
            log.warning("the settings cannot be read; the built-in list of terms is used alone")
            return ""


def _account() -> str:
    try:
        return getpass.getuser()
    except OSError:
        return ""


def check_choice(
    chosen_presets: list[str], chosen_formats: list[str], book: Mapping[str, presets.Preset] | None = None
) -> None:
    known_presets, known_formats = list(book if book is not None else presets.registry()), list(export.FORMATS)
    bad = [p for p in chosen_presets if p not in known_presets]
    if bad:
        raise SettingsError(f"Unknown subtitle preset {bad[0]!r}. Available: {', '.join(known_presets)}.")
    bad = [f for f in chosen_formats if f not in known_formats]
    if bad:
        raise SettingsError(f"Unknown file type {bad[0]!r}. Available: {', '.join(known_formats)}.")
    if not chosen_presets or not chosen_formats:
        raise SettingsError("Choose at least one preset and one file type.")
    if len(set(chosen_presets)) != len(chosen_presets) or len(set(chosen_formats)) != len(chosen_formats):
        raise SettingsError("A preset or file type is listed twice.")


def ordered(types: list[presets.Preset], order: list[str]) -> list[presets.Preset]:
    """The types in the user's order; a type the order does not name keeps its place after the named ones."""
    rank = {pid: i for i, pid in enumerate(order)}
    return sorted(types, key=lambda p: rank.get(p.id, len(rank)))  # sorted() is stable


def book_of(settings: Settings) -> dict[str, presets.Preset]:
    """Every type the user's subtitles can be opened, checked and saved with (removed own types included)."""
    return presets.book([*settings.custom_presets, *settings.retired_presets])


def check_own_types(settings: Settings) -> None:
    """The user's own types: their own ids (never a built-in one, never twice), a language the app has."""
    if len(settings.custom_presets) > MAX_OWN_TYPES or len(settings.retired_presets) > MAX_RETIRED_TYPES:
        raise SettingsError(f"At most {MAX_OWN_TYPES} own subtitle types (and {MAX_RETIRED_TYPES} removed ones).")
    seen: set[str] = set()
    for p in [*settings.custom_presets, *settings.retired_presets]:
        if not re.fullmatch(presets.CUSTOM_ID, p.id) or p.id in presets.registry():
            raise SettingsError(f"The subtitle type {p.name!r} has an id the app cannot use ({p.id!r}).")
        if p.id in seen:
            raise SettingsError(f"Two subtitle types share the id {p.id!r}.")
        seen.add(p.id)
        if not p.name.strip():
            raise SettingsError("Every subtitle type needs a name.")
        try:
            presets.language(p.language)
        except presets.PresetError:
            raise SettingsError(f"The subtitle type {p.name!r} is for a language the app has no rules for.") from None


def check(settings: Settings) -> None:
    if settings.engine not in engines.names():
        raise SettingsError(f"Unknown engine {settings.engine!r}. Available: {', '.join(engines.names())}.")
    check_own_types(settings)
    check_choice(settings.export_presets, settings.export_formats, presets.book(settings.custom_presets))
    try:
        glossary.parse_text(settings.glossary_text)
    except glossary.GlossaryError as exc:
        raise SettingsError(f"Terms and names: {exc}") from None
    if settings.output_dir is not None:
        if not is_plain_local_dir(settings.output_dir):
            raise SettingsError("Choose a folder on a drive of this computer.")
        if not Path(settings.output_dir).is_dir():
            raise SettingsError(f"There is no folder at {settings.output_dir}.")
