"""Files dropped onto the window: streamed, sealed, into `<data>/uploads/<id>/source<ext>`, never under a name
from the request.

The page cannot say where a dropped file lives, so the app keeps its own copy for as long as the job is listed.
The user's file name is kept only for display and, made safe, for naming the subtitle files.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import time
import unicodedata
import uuid
from collections.abc import AsyncIterator, Iterable
from pathlib import Path
from typing import Final
from urllib.parse import unquote

from parakeet_stt.vault import Role, SealedWriter, Vault, replace_retrying

UPLOADS_DIR: Final = "uploads"
# leave this much free on the data drive for the job's own files (sound, checkpoints, preview) and for Windows; a
# larger margin refused even a small file on a laptop with about 2 GB free
SPARE_BYTES: Final = 1_000_000_000
MAX_UPLOAD_BYTES: Final = 64 * 1024**3
WRITE_BLOCK: Final = 1024 * 1024
MAX_NAME_CHARS: Final = 180
MEDIA_EXTENSIONS: Final = (".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".wmv", ".mpg", ".mpeg", ".ts", ".mts",
                           ".mp3", ".m4a", ".wav", ".flac", ".aac", ".ogg", ".opus", ".wma")  # fmt: skip
_RESERVED: Final = frozenset(
    {"con", "prn", "aux", "nul", "conin$", "conout$", "clock$"}
    | {f"{dev}{n}" for dev in ("com", "lpt") for n in [*"123456789", "¹", "²", "³"]}
)
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_ID = re.compile(r"^[0-9a-f]{12}$")


class UploadError(Exception):
    """The upload cannot be accepted; the message is for the user."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def free_bytes(data_dir: Path) -> int | None:
    """Free space on the drive that holds the data folder; None when it cannot be read."""
    probe = data_dir
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free
    except OSError:
        return None


def limit(data_dir: Path) -> int:
    """The largest upload that still leaves SPARE_BYTES free on the data drive."""
    free = free_bytes(data_dir)
    if free is None:
        return 0
    return max(0, min(free - SPARE_BYTES, MAX_UPLOAD_BYTES))


def room(data_dir: Path) -> dict[str, int | None]:
    """What a refused upload tells the window: the free space now and the margin the app keeps."""
    return {"free_bytes": free_bytes(data_dir), "spare_bytes": SPARE_BYTES}


def safe_name(name: str) -> str:
    """A file name Windows accepts and that cannot name a device: format characters, path parts and
    forbidden characters removed, no trailing dots or spaces, and a reserved device name made harmless."""
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if unicodedata.category(ch) not in ("Cf", "Cc", "Cs", "Co", "Cn"))
    name = _BAD_CHARS.sub("_", name).strip().rstrip(". ")
    if len(name) > MAX_NAME_CHARS:
        stem, dot, ext = name.rpartition(".")
        name = (stem[: MAX_NAME_CHARS - len(ext) - 1] + dot + ext) if dot and len(ext) <= 8 else name[:MAX_NAME_CHARS]
        name = name.rstrip(". ")
    if name.split(".", 1)[0].rstrip(" ").lower() in _RESERVED:
        name = "_" + name
    return name or "media"


def stem_of(name: str) -> str:
    """The subtitle file stem for a media name: the safe name without its extension."""
    safe = safe_name(name)
    stem = safe.rsplit(".", 1)[0] if "." in safe else safe
    stem = stem.rstrip(". ")
    if stem.split(".", 1)[0].rstrip(" ").lower() in _RESERVED:
        stem = "_" + stem
    return stem or "transcript"


def parse_name(header: str | None) -> tuple[str, str]:
    """(display name, lower-case extension) from the percent-encoded X-File-Name header."""
    if not header:
        raise UploadError("The file name is missing.")
    try:
        raw = unquote(header, encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        raise UploadError("The file name is not readable.") from None
    name = safe_name(raw)
    ext = Path(name).suffix.lower()
    if ext not in MEDIA_EXTENSIONS:
        raise UploadError(f"{name} is not an audio or video file.", 415)
    return name, ext


def folder(data_dir: Path, upload_id: str) -> Path:
    """The folder of one upload, built only from our own id (never from request text, never resolved)."""
    if not _ID.match(upload_id):
        raise ValueError(f"not an upload id: {upload_id!r}")
    return data_dir / UPLOADS_DIR / upload_id


async def receive(
    data_dir: Path, chunks: AsyncIterator[bytes], length: int, ext: str, vault: Vault
) -> tuple[str, Path]:
    """Write exactly `length` bytes, sealed, to a new upload folder; (upload id, file). Removes the folder on any
    failure."""
    upload_id = uuid.uuid4().hex[:12]
    target_dir = folder(data_dir, upload_id)
    part = target_dir / "source.part"
    final = target_dir / f"source{ext}"
    target_dir.mkdir(parents=True)
    try:
        fh = await asyncio.to_thread(SealedWriter, part, vault, Role.SOURCE)
        try:
            total, block = 0, bytearray()
            async for chunk in chunks:
                total += len(chunk)
                if total > length:
                    raise UploadError("The file is longer than its stated size.")
                block += chunk
                if len(block) >= WRITE_BLOCK:
                    await asyncio.to_thread(fh.write, bytes(block))
                    block.clear()
            if block:
                await asyncio.to_thread(fh.write, bytes(block))
            if total != length:
                raise UploadError("The file did not arrive completely.")
            await asyncio.to_thread(fh.close)
        finally:
            await asyncio.to_thread(fh.abort)
        await asyncio.to_thread(replace_retrying, part, final)
    except BaseException:
        await asyncio.to_thread(shutil.rmtree, target_dir, True)
        raise
    return upload_id, final


def keep_sound_only(path: Path, vault: Vault) -> tuple[Path, str | None]:
    """A dropped video becomes its sound (sealed, beside it) and the video copy is deleted; sound stays as it is.
    Returns (the file to use, the video's frame rate "num/den" or None for sound). Raises media.MediaError when the
    file is not readable media, OSError when the video copy cannot be deleted."""
    from parakeet_stt import blobserve, media, sound

    with blobserve.media_input(path, vault) as source:
        info = media.probe(source)
        if not info.has_video:
            return path, None
        target = path.with_name(sound.SOUND_FILE)
        sound.keep(info, target, vault)
        if source.failed():
            target.unlink(missing_ok=True)
            raise media.MediaError(f"{path.name} could not be read completely.")
    rate = info.frame_rate
    for attempt in range(10):  # a scanner can hold the copy for a moment
        try:
            path.unlink()
            break
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.2)
    return target, f"{rate.numerator}/{rate.denominator}" if rate else ""


def sweep_one(data_dir: Path, upload_id: str) -> None:
    shutil.rmtree(folder(data_dir, upload_id), ignore_errors=True)


def sweep(data_dir: Path, keep: Iterable[str]) -> list[str]:
    """Delete every upload folder no job refers to (a stop mid-upload leaves its partial copy). Returns their ids."""
    base = data_dir / UPLOADS_DIR
    if not base.is_dir():
        return []
    wanted = set(keep)
    removed = []
    for entry in base.iterdir():
        if entry.name not in wanted:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)
            removed.append(entry.name)
    return removed
