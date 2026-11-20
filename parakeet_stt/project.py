"""The project file (`<name>.pstt.json`): save atomically, load with version checks and migrations."""

from __future__ import annotations

import errno
import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from parakeet_stt import vault as vault_mod
from parakeet_stt.transcript import SCHEMA_VERSION, Document
from parakeet_stt.vault import Role, Vault, VaultError

SUFFIX = ".pstt.json"


def _v1_to_v2(raw: dict[str, Any]) -> dict[str, Any]:
    """Version 2 added subtitle tracks; a version-1 file has none."""
    return {**raw, "subtitles": []}


def _v2_to_v3(raw: dict[str, Any]) -> dict[str, Any]:
    """Version 3 added translations (cue.translation, track.translation_language); a version-2 file has none."""
    return raw


# version n -> function turning a version-n dict into a version-(n+1) dict
MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {1: _v1_to_v2, 2: _v2_to_v3}


class ProjectError(Exception):
    """The project file cannot be opened; the message is for the user."""


# Windows: ERROR_HANDLE_DISK_FULL, ERROR_DISK_FULL; everywhere: ENOSPC and the quota variant
_DISK_FULL_WINERRORS = (39, 112)
_DISK_FULL_ERRNOS = (errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC))


def disk_full(exc: OSError) -> bool:
    """Whether a failed write means the drive (or the user's quota on it) is full."""
    return getattr(exc, "winerror", None) in _DISK_FULL_WINERRORS or exc.errno in _DISK_FULL_ERRNOS


def write_atomically(files: Mapping[Path, str | bytes]) -> None:
    """Write every file to a temporary sibling first, then swap them all in.

    A crash or cancel before the swap leaves the old files (or none) - never a half-written one.
    Text is written as UTF-8 exactly as given (line ends included); bytes as they are.
    """
    temps: list[tuple[Path, Path]] = []
    try:
        for path, content in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.tmp")
            temps.append((tmp, path))  # before writing: a full disk mid-write must not leave a partial temp
            with tmp.open("wb") as fh:
                fh.write(content.encode("utf-8") if isinstance(content, str) else content)
                fh.flush()
                os.fsync(fh.fileno())
        for tmp, path in temps:
            _replace(tmp, path)
    finally:
        for tmp, _ in temps:
            tmp.unlink(missing_ok=True)


def _replace(tmp: Path, path: Path) -> None:
    vault_mod.replace_retrying(tmp, path)


def dumps(doc: Document) -> str:
    return doc.model_dump_json(indent=1) + "\n"


def stored(doc: Document, vault: Vault | None) -> str | bytes:
    """The file content of a project: sealed when it belongs to the app's own store (`vault` given)."""
    return vault.seal(dumps(doc).encode("utf-8"), Role.PROJECT) if vault is not None else dumps(doc)


def save(doc: Document, path: Path, vault: Vault | None = None) -> None:
    write_atomically({path: stored(doc, vault)})


def _text(path: Path, vault: Vault | None) -> bytes:
    """The plain bytes of a project file. With a vault (the app's own files) only a sealed file is accepted;
    without one, a sealed file is opened with the key of the data folder it lies in (the CLI)."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise ProjectError(f"Project file not found: {path}") from None
    if vault is None and not vault_mod.sealed_bytes(raw):
        return raw
    try:
        v = vault or vault_mod.locate(path)
        if v is None:
            raise ProjectError(f"{path.name} is locked by TalkSubs, and its key is not beside it.")
        return v.unseal(raw, Role.PROJECT)
    except VaultError as exc:
        raise ProjectError(f"{path.name}: {exc}") from None


def load(path: Path, vault: Vault | None = None) -> Document:
    try:
        raw = json.loads(_text(path, vault).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProjectError(f"{path.name} is not a project file (not valid JSON: {exc}).") from None
    if not isinstance(raw, dict):
        raise ProjectError(f"{path.name} is not a project file.")
    version = raw.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ProjectError(f"{path.name} is not a project file (no valid schema_version).")
    if version > SCHEMA_VERSION:
        raise ProjectError(
            f"{path.name} was made by a newer version of TalkSubs (format {version}; this one reads up to "
            f"{SCHEMA_VERSION}). Update the app to open it."
        )
    while version < SCHEMA_VERSION:
        raw = MIGRATIONS[version](raw)
        version += 1
        raw["schema_version"] = version
    try:
        return Document.model_validate(raw)
    except ValidationError as exc:
        raise ProjectError(
            f"{path.name} is damaged: {exc.error_count()} problem(s), first: {exc.errors()[0]['msg']}"
        ) from None


def schema() -> dict[str, Any]:
    return Document.model_json_schema()


SCHEMA_FILE = Path(__file__).resolve().parent.parent / "schema" / "transcript.schema.json"


def schema_text() -> str:
    return json.dumps(schema(), indent=1, ensure_ascii=False) + "\n"
