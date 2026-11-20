"""The app's store at start: its key, the one-time sealing of what an older version left plain, the locked state.

App-managed (every file sealed): `queue.json`, `settings.json`, `speed.json`, `jobs/`, `uploads/`. Plain by design:
the key files, `logs/` (scrubbed), `subtitles/` (the default export folder) and `old-*/` (work moved aside by "start
fresh").
"""

from __future__ import annotations

import contextlib
import logging
import shutil
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from parakeet_stt import preview, project, speed, vault
from parakeet_stt.server import access, store
from parakeet_stt.server.settings import SETTINGS_FILE
from parakeet_stt.vault import Role, Vault, VaultError, VaultLocked

log = logging.getLogger("parakeet_stt.server")

APP_FILES: Final = {store.QUEUE_FILE: Role.QUEUE, SETTINGS_FILE: Role.SETTINGS}
APP_DIRS: Final = ("jobs", "uploads")
# speed.json is moved aside too, but never locks the store: what cannot be read there is simply measured again
MOVED_ASIDE: Final = (*APP_FILES, speed.SPEED_FILE, *APP_DIRS, vault.KEY_FILE, vault.KEY_BACKUP)
_SPARE_BYTES: Final = 512 * 1024**2


@dataclass(frozen=True)
class Opened:
    vault: Vault | None  # None: locked
    left_plain: int = 0  # files that could not be sealed yet (no disk space); tried again at the next start


def app_files(data_dir: Path) -> Iterator[Path]:
    """Every file the app keeps for itself (the ones that must be sealed)."""
    for name in APP_FILES:
        p = data_dir / name
        if p.is_file():
            yield p
    for d in APP_DIRS:
        root = data_dir / d
        if root.is_dir():
            yield from (p for p in root.rglob("*") if p.is_file())


PLAIN_FILES: Final = (vault.KEY_FILE, vault.KEY_BACKUP, access.MARKER)  # the marker is empty
PLAIN_DIRS: Final = ("logs", "subtitles")  # and old-*/


def plain_files(data_dir: Path) -> list[Path]:
    """Every file in the data folder that is not sealed and not plain by design (an allowlist, not a blocklist)."""
    found = []
    for p in sorted(data_dir.rglob("*")) if data_dir.is_dir() else ():
        if not p.is_file():
            continue
        top = p.relative_to(data_dir).parts[0]
        if top in PLAIN_DIRS or top.startswith("old-") or (p.parent == data_dir and p.name in PLAIN_FILES):
            continue
        if not vault.is_sealed(p):
            found.append(p)
    return found


def open_store(data_dir: Path, *, entropy: bytes | None = None) -> Opened:
    """The vault of the data folder (made on the first start), with every plain app file sealed; or locked."""
    data_dir.mkdir(parents=True, exist_ok=True)
    kw = {"entropy": entropy} if entropy is not None else {}
    if not vault.key_exists(data_dir) and any(vault.is_sealed(p) for p in app_files(data_dir)):
        log.error("the app's key is missing but sealed work is there: locked")
        return Opened(None)
    try:
        v = Vault.create_or_open(data_dir, **kw)
    except VaultLocked:
        log.error("the app's key belongs to another Windows account or computer: locked")
        return Opened(None)
    for name, role in APP_FILES.items():  # a key from another data folder unwraps, but opens nothing here
        p = data_dir / name
        if vault.is_sealed(p):
            try:
                v.read(p, role)
            except VaultError:
                log.error("%s does not open with this folder's key: locked", name)
                return Opened(None)
    return Opened(v, seal_plain(data_dir, v))


def seal_plain(data_dir: Path, v: Vault) -> int:
    """Seal every app file an older version wrote plain (idempotent, resumable). Returns how many stay plain."""
    sealed = left = 0
    for name, role in APP_FILES.items():
        p = data_dir / name
        if p.is_file() and not vault.is_sealed(p):
            project.write_atomically({p: v.seal(p.read_bytes(), role)})
            sealed += 1
    jobs, uploads = data_dir / "jobs", data_dir / "uploads"
    for job_dir in sorted(jobs.iterdir()) if jobs.is_dir() else ():
        if job_dir.is_dir():
            try:
                sealed += _seal_job(job_dir, v)
            except OSError as exc:
                left += 1
                log.warning("job %s could not be sealed yet (%s); tried again at the next start", job_dir.name,
                            exc.__class__.__name__)  # fmt: skip
    for up in sorted(uploads.iterdir()) if uploads.is_dir() else ():
        if up.is_dir():
            try:
                done, waiting = _seal_upload(up, v)
            except OSError as exc:
                done, waiting = 0, 1
                log.warning("upload %s could not be sealed yet (%s); tried again at the next start", up.name,
                            exc.__class__.__name__)  # fmt: skip
            sealed, left = sealed + done, left + waiting
    if sealed:
        log.info("sealed %d file(s) an older version had left plain", sealed)
    if left:
        log.warning("%d dropped file(s) stay plain until there is enough free disk space", left)
    return left


def _seal_job(job_dir: Path, v: Vault) -> int:
    sealed = 0
    for p in job_dir.iterdir():
        if not p.is_file() or vault.is_sealed(p):
            continue
        if p.name.endswith(project.SUFFIX):
            project.write_atomically({p: v.seal(p.read_bytes(), Role.PROJECT)})
            sealed += 1
        else:  # a temporary file a crash left (a half-written save, a sealing stopped half-way)
            p.unlink(missing_ok=True)
    old_preview = preview.folder(job_dir)
    old_files = [f for f in old_preview.rglob("*") if f.is_file()] if old_preview.is_dir() else []
    if any(not vault.is_sealed(f) for f in old_files):
        shutil.rmtree(old_preview, ignore_errors=True)  # cheap to make again (on the CPU), sealed
        log.info("job %s: its old preview is made again", job_dir.name)
    shutil.rmtree(job_dir / preview.BUILD_DIR, ignore_errors=True)
    return sealed


def _seal_upload(up: Path, v: Vault) -> tuple[int, int]:
    """(sealed, left plain) for one upload folder; a dropped file is sealed beside itself, then swapped in."""
    sealed = left = 0
    for p in up.iterdir():
        if not p.is_file() or vault.is_sealed(p):
            continue
        if not p.name.startswith("source.") or p.name.endswith(".part"):
            p.unlink(missing_ok=True)  # what a stopped copy or sealing left
        elif _free(p) < p.stat().st_size + _SPARE_BYTES:
            left += 1
        else:
            with v.writer(p, Role.SOURCE) as out, p.open("rb") as src:
                while block := src.read(1024 * 1024):
                    out.write(block)
            sealed += 1
    return sealed, left


def _free(p: Path) -> int:
    try:
        return shutil.disk_usage(p.parent).free
    except OSError:
        return 0


def start_fresh(data_dir: Path) -> Path:
    """Move the locked work aside (never delete it) into `old-<date>/`; the next `open_store` starts empty."""
    target = data_dir / time.strftime("old-%Y%m%d-%H%M%S")
    n = 0
    while target.exists():
        n += 1
        target = data_dir / f"{time.strftime('old-%Y%m%d-%H%M%S')}-{n}"
    target.mkdir()
    for name in MOVED_ASIDE:
        src = data_dir / name
        if src.exists():
            with contextlib.suppress(FileNotFoundError):
                shutil.move(src, target / name)
    log.info("the locked work was moved aside into %s", target.name)
    return target
