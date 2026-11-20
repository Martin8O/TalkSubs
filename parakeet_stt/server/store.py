"""The queue file (`<data>/queue.json`): versioned, written atomically, only when a job changes state."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from parakeet_stt.project import write_atomically
from parakeet_stt.server.messages import QUEUE_SCHEMA_VERSION, Job, QueueFile
from parakeet_stt.vault import Role, Vault, VaultError

QUEUE_FILE = "queue.json"

# version n -> function turning a version-n dict into a version-(n+1) dict (none yet)
MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {}


class StoreError(Exception):
    """The queue file cannot be read; the message is for the user."""


class Store:
    def __init__(self, data_dir: Path, vault: Vault) -> None:
        self.path = data_dir / QUEUE_FILE
        self.vault = vault

    def load(self) -> list[Job]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.vault.read(self.path, Role.QUEUE).decode("utf-8"))
        except VaultError as exc:
            raise StoreError(f"The queue file {self.path} cannot be opened: {exc}") from None
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StoreError(f"The queue file {self.path} is damaged ({exc}).") from None
        version = raw.get("schema_version") if isinstance(raw, dict) else None
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise StoreError(f"The queue file {self.path} has no valid schema_version.")
        if version > QUEUE_SCHEMA_VERSION:
            raise StoreError(f"The queue file {self.path} was written by a newer version of TalkSubs.")
        while version < QUEUE_SCHEMA_VERSION:
            raw = MIGRATIONS[version](raw)
            version += 1
            raw["schema_version"] = version
        try:
            return QueueFile.model_validate(raw).jobs
        except ValidationError as exc:
            raise StoreError(f"The queue file {self.path} is damaged: {exc.errors()[0]['msg']}") from None

    def save(self, jobs: list[Job]) -> None:
        # live progress and the preview's state are not part of the stored state (the preview is found on disk)
        stored = QueueFile(jobs=[j.model_copy(update={"progress": None, "preview": None}) for j in jobs])
        text = stored.model_dump_json(indent=1) + "\n"
        write_atomically({self.path: self.vault.seal(text.encode("utf-8"), Role.QUEUE)})
