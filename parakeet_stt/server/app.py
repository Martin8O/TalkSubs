"""The FastAPI app: REST for actions, one WebSocket for live state, and the window's page."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, WebSocket
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import ClientDisconnect
from starlette.types import ASGIApp, Scope

from parakeet_stt import __version__, blobserve, media, models
from parakeet_stt.cancel import CancelToken
from parakeet_stt.server import dialogs, logscrub, shell, storage, uploads
from parakeet_stt.server.editing import EditError, Opened
from parakeet_stt.server.hub import Hub, Presence
from parakeet_stt.server.messages import (
    AddJobRequest,
    AddLinkRequest,
    CheckRequest,
    CheckResult,
    DialogRequest,
    DialogResult,
    ErrorResponse,
    ExportConflict,
    ExportRequest,
    ExportResult,
    FileSource,
    Job,
    ResegmentRequest,
    SaveTrackRequest,
    Settings,
    SettingsView,
    Status,
    TrackSaved,
    TrackView,
    UploadSource,
    WordsView,
)
from parakeet_stt.server.runner import ExportExists, JobError, Runner
from parakeet_stt.server.security import LocalOnly
from parakeet_stt.server.settings import SettingsError, SettingsStore, check_choice
from parakeet_stt.server.store import Store, StoreError
from parakeet_stt.vault import Role, Vault, VaultError

log = logging.getLogger("parakeet_stt.server")

DEFAULT_PORT = 8754
LOCKED = ("The work kept in this app cannot be opened here: it was locked by another Windows account or on another "
          "computer, its key is missing, or it is damaged.")  # fmt: skip
_STREAM_BLOCK = 256 * 1024
UPLOAD_PATH = "/api/uploads"
# the editor sends whole tracks (a 3 h lecture is ~0.5 MB of cues)
TRACK_PATHS = re.compile(r"/api/jobs/[0-9a-f]{12}/tracks/[a-z0-9-]{1,64}(/check)?")
TRACK_BODY_LIMIT = 16 * 1024 * 1024
SETTINGS_PATH = re.compile(r"/api/settings")
# a settings document at its allowed maximum (a 200 000-character glossary, 20 own and 50 removed types) fits
SETTINGS_BODY_LIMIT = 1024 * 1024
# the page's files: extension -> media type (nothing else under static/ is served)
PAGE_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
              ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
              ".ico": "image/x-icon", ".woff2": "font/woff2"}  # fmt: skip


@dataclass
class ServerConfig:
    data_dir: Path
    port: int = DEFAULT_PORT
    worker_cmd: Sequence[str] | None = None
    presence: Presence | None = None  # only the app launcher watches for its window closing
    kill_after_s: float = 10.0
    busy_retry_s: float = 5.0
    memory_retry_s: float = 120.0
    gpu_check: Callable[[], str | None] | None = None  # default: is the machine-wide GPU lock free?
    allow_local_http_links: bool = False  # tests only: links to 127.0.0.1 over plain http
    dialog: Callable[[dialogs.Kind], list[str]] = field(default=dialogs.run)  # tests replace the Windows dialog
    reveal: Callable[[list[Path]], None] = field(default=shell.reveal)
    upload_limit: Callable[[], int] | None = None  # default: free space on the data drive minus a margin
    peer_check: Callable[[Scope], bool] | None = None  # default: the connection's process runs as our Windows user
    key_entropy: bytes | None = None  # tests: a different value stands for another Windows account's key
    # what this PC runs the model on; default: asked in a short-lived process (none with a stand-in worker)
    machine_check: Callable[[], dict[str, Any] | None] | None = None
    # the speech model's files: here? and how to download them (default: the pinned files; tests replace both)
    models_present: Callable[[], bool] | None = None
    models_fetch: Callable[[models.ProgressFn, CancelToken], None] | None = None
    # the download package's graphics-card support: here? and how to download it (default: cardpack.py)
    card_present: Callable[[], bool] | None = None
    card_fetch: Callable[[models.ProgressFn, CancelToken], None] | None = None
    # the Whisper engine's model: here? and how to download it (default: the pinned files, models.py)
    whisper_present: Callable[[], bool] | None = None
    whisper_fetch: Callable[[models.ProgressFn, CancelToken], None] | None = None


@dataclass
class Server:
    asgi: ASGIApp  # what uvicorn runs: the app behind the localhost checks
    app: FastAPI
    hub: Hub
    state: _State

    @property
    def runner(self) -> Runner:
        assert self.state.runner is not None, "the store is locked"
        return self.state.runner


@dataclass
class _State:
    """The app's store: open (a vault and its runner) or locked (neither, until the user starts fresh)."""

    vault: Vault | None = None
    runner: Runner | None = None


class _Page:
    """The built page (web/ → static/): an exact list of files, read again whenever index.html is asked for,
    so a rebuild with new file names is served without a restart. Nothing is looked up by request text."""

    def __init__(self) -> None:
        self.root = Path(str(resources.files("parakeet_stt.server").joinpath("static")))
        self.files: dict[str, Path] = {}
        self.scan()

    def scan(self) -> None:
        found = {}
        if self.root.is_dir():
            for p in self.root.rglob("*"):
                if p.is_file() and not p.is_symlink() and p.suffix.lower() in PAGE_TYPES:
                    found[p.relative_to(self.root).as_posix()] = p
        self.files = found

    def response(self, name: str) -> Response:
        if name == "index.html":
            self.scan()
        path = self.files.get(name)
        if path is None:
            return _error(404, "Not found.")
        cache = "public, max-age=31536000, immutable" if name.startswith("assets/") else "no-store"
        return Response(path.read_bytes(), media_type=PAGE_TYPES[path.suffix.lower()], headers={"cache-control": cache})


def _open(cfg: ServerConfig, hub: Hub) -> tuple[Vault | None, Runner | None]:
    """Open the store (made and sealed on the first start); on any doubt about its key, stay locked (None, None).

    A queue file that opens with the key but is damaged or from a newer version raises StoreError: that is not
    a key problem, and the start stops with a sentence about that one file.
    """
    opened = storage.open_store(cfg.data_dir, entropy=cfg.key_entropy)
    if opened.vault is None:
        return None, None
    Store(cfg.data_dir, opened.vault).load()
    prefs = SettingsStore(cfg.data_dir, opened.vault)
    runner = Runner(cfg.data_dir, hub, opened.vault, cfg.worker_cmd, kill_after_s=cfg.kill_after_s,
                    busy_retry_s=cfg.busy_retry_s, memory_retry_s=cfg.memory_retry_s, gpu_check=cfg.gpu_check,
                    allow_local_http_links=cfg.allow_local_http_links, own_glossary=prefs.own_glossary,
                    speech_language=prefs.speech_language,
                    preset_book=prefs.book, job_types=prefs.job_types,
                    machine_check=cfg.machine_check, models_present=cfg.models_present,
                    models_fetch=cfg.models_fetch, card_present=cfg.card_present,
                    card_fetch=cfg.card_fetch, whisper_present=cfg.whisper_present,
                    whisper_fetch=cfg.whisper_fetch)  # fmt: skip
    return opened.vault, runner


def create(cfg: ServerConfig) -> Server:  # noqa: PLR0915 - the route table reads best in one place
    hub = Hub()
    vault_, runner_ = _open(cfg, hub)
    state = _State(vault_, runner_)
    prefs = SettingsStore(cfg.data_dir, state.vault)
    page = _Page()
    single = {"dialog": asyncio.Lock(), "upload": asyncio.Lock(), "fresh": asyncio.Lock()}

    def live() -> Runner:
        if state.runner is None:
            raise JobError(LOCKED, 503)
        return state.runner

    def vault() -> Vault:
        if state.vault is None:
            raise JobError(LOCKED, 503)
        return state.vault

    def status_now() -> Status:
        if state.runner is not None:
            return state.runner.status()
        return Status(app_version=__version__, boot_id=hub.boot_id, seq=hub.seq, jobs=[], storage_locked=True)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if state.runner is not None:
            try:
                state.runner.start()
            except StoreError as exc:
                log.error("%s", exc)
                raise
        if cfg.presence is not None:
            cfg.presence.start(asyncio.get_running_loop())
            hub.on_count = cfg.presence.changed
        yield
        dialogs.close_all()  # an open Windows dialog would keep its thread, and so the app, alive
        if state.runner is not None:
            await state.runner.shutdown()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(ExportExists)
    async def export_exists(_: Request, exc: ExportExists) -> JSONResponse:
        return JSONResponse(ExportConflict(error=str(exc), existing=exc.existing).model_dump(), status_code=409)

    @app.exception_handler(JobError)
    async def job_error(_: Request, exc: JobError) -> JSONResponse:
        return _error(exc.status_code, str(exc))

    @app.exception_handler(EditError)
    async def edit_error(_: Request, exc: EditError) -> JSONResponse:
        return _error(exc.status_code, str(exc))

    @app.exception_handler(SettingsError)
    async def settings_error(_: Request, exc: SettingsError) -> JSONResponse:
        return _error(400, str(exc))

    @app.exception_handler(uploads.UploadError)
    async def upload_error(_: Request, exc: uploads.UploadError) -> JSONResponse:
        return _error(exc.status_code, str(exc))

    @app.exception_handler(dialogs.DialogError)
    async def dialog_error(_: Request, exc: dialogs.DialogError) -> JSONResponse:
        return _error(500, str(exc))

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _error(exc.status_code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def bad_request(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {"msg": "invalid request"}
        return _error(422, f"Invalid request: {first['msg']}")

    _quiet_uvicorn_crashes()

    @app.exception_handler(Exception)
    async def crash(_: Request, exc: Exception) -> JSONResponse:
        if isinstance(exc, (ValidationError, ResponseValidationError)):  # its text quotes the input (transcript text)
            log.error("request failed: %s with %d errors", type(exc).__name__, len(exc.errors()))
        else:
            log.exception("request failed")
        return _error(500, f"Internal error: {exc.__class__.__name__}: {exc}")

    def out_dir_for(job: Job, s: Settings) -> Path:
        if isinstance(job.source, FileSource) and s.output == "beside_source":
            return Path(job.source.path).parent
        folder = prefs.output_dir(s)
        if s.output_dir is None:  # the app's own default folder: make it on first use
            folder.mkdir(parents=True, exist_ok=True)
        return folder

    @app.get("/api/status")
    async def status() -> Status:
        return status_now()

    @app.post("/api/storage/start-fresh", status_code=204)
    async def start_fresh() -> Response:
        """Only while locked: move the old work aside (never deleted) and start with an empty store."""
        if state.runner is not None:
            raise JobError("The app's work is not locked.", 409)
        async with single["fresh"]:
            if state.runner is None:
                await asyncio.to_thread(storage.start_fresh, cfg.data_dir)
                new_vault, new_runner = await asyncio.to_thread(_open, cfg, hub)
                if new_runner is None:
                    raise JobError(LOCKED, 503)
                new_runner.start()  # running before any request can reach it
                prefs.vault, state.vault, state.runner = new_vault, new_vault, new_runner
        return Response(status_code=204)

    @app.get("/api/settings")
    async def get_settings() -> SettingsView:
        return prefs.view(prefs.load())

    @app.put("/api/settings")
    async def put_settings(new: Settings) -> SettingsView:
        return prefs.view(prefs.save(new))

    @app.post("/api/dialog")
    async def dialog(req: DialogRequest) -> DialogResult:
        if single["dialog"].locked():
            raise JobError("A Windows dialog is already open.", 409)
        async with single["dialog"]:
            return DialogResult(paths=await asyncio.to_thread(cfg.dialog, req.kind))

    @app.post("/api/jobs", status_code=201)
    async def add_job(req: AddJobRequest) -> Job:
        return await live().add(FileSource(path=req.path), engine=prefs.load().engine)

    @app.post("/api/links", status_code=201)
    async def add_link(req: AddLinkRequest) -> Job:
        return live().add_link(req.url, engine=prefs.load().engine)

    @app.put(UPLOAD_PATH, status_code=201)
    async def upload(request: Request) -> Job:
        if single["upload"].locked():
            raise JobError("Another file is being copied; wait until it is done.", 409)
        runner = live()
        async with single["upload"]:
            name, ext = uploads.parse_name(request.headers.get("x-file-name"))
            logscrub.register(name)
            length = int(request.headers["content-length"])  # LocalOnly made sure it is there and within the limit
            engine = prefs.load().engine
            try:
                upload_id, path = await uploads.receive(cfg.data_dir, request.stream(), length, ext, vault())
            except ClientDisconnect:  # the page cancelled it; the partial copy is already gone
                log.info("an upload was cancelled")
                raise JobError("The copy was cancelled.") from None
            try:  # the app keeps only the sound of a video (ADR 0031)
                path, rate = await asyncio.to_thread(uploads.keep_sound_only, path, vault())
            except (media.MediaError, VaultError, OSError):
                await asyncio.to_thread(uploads.sweep_one, cfg.data_dir, upload_id)
                raise uploads.UploadError(f"{name} could not be read as audio or video.", 422) from None
            source = UploadSource(upload_id=upload_id, path=str(path), original_name=name, had_video=rate is not None,
                                  frame_rate=rate or None)  # fmt: skip
            try:
                return await runner.add(source, engine=engine)
            except JobError as exc:
                await asyncio.to_thread(uploads.sweep_one, cfg.data_dir, upload_id)
                log.info("a dropped file was refused: %s", exc)  # the detail names the app's copy, not the user's file
                raise uploads.UploadError(f"{name} could not be read as audio or video.", 422) from None

    @app.post("/api/models/fetch", status_code=204)
    async def fetch_models() -> None:
        live().fetch_models_again()

    @app.post("/api/card/fetch", status_code=204)
    async def fetch_card_pack() -> None:
        live().fetch_card_pack()

    @app.post("/api/whisper/fetch", status_code=204)
    async def fetch_whisper_model() -> None:
        live().fetch_whisper_model()

    @app.post("/api/speed/measure", status_code=204)
    async def measure_speed() -> None:
        live().measure_speed()

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str) -> Job:
        return live().cancel(job_id)

    @app.post("/api/jobs/{job_id}/export")
    async def export_job(job_id: str, req: ExportRequest) -> ExportResult:
        check_choice(req.presets, req.formats, prefs.known())
        job = live().job(job_id)
        if job.state != "done":
            raise JobError("Only a finished transcription can be exported.", 409)
        return await live().export(job_id, req, out_dir_for(job, prefs.load()))

    @app.post("/api/jobs/{job_id}/reveal", status_code=204)
    async def reveal_job(job_id: str) -> Response:
        files = live().exported_files(job_id)
        try:
            await asyncio.to_thread(cfg.reveal, files)
        except OSError as exc:
            raise JobError(f"Explorer could not show the files: {exc}", 500) from None
        return Response(status_code=204)

    @app.post("/api/jobs/{job_id}/remove", status_code=204)
    async def remove_job(job_id: str) -> Response:
        await live().remove(job_id)
        return Response(status_code=204)

    @app.api_route("/api/jobs/{job_id}/media", methods=["GET", "HEAD"], response_model=None)
    async def preview_media(job_id: str, request: Request) -> Response:
        path, _, _ = live().preview_files(job_id)  # the path comes from the job record only
        kind = "audio/mp4"  # sound only (ADR 0031); not the registry's guess; ranges are served
        # LocalOnly marks every /api/ answer no-store
        return await asyncio.to_thread(_ranges, vault(), path, kind, request.headers.get("range"),
                                       request.method == "HEAD")  # fmt: skip

    @app.api_route("/api/jobs/{job_id}/peaks", methods=["GET", "HEAD"], response_model=None)
    async def preview_peaks(job_id: str, request: Request) -> Response:
        _, path, _ = live().preview_files(job_id)
        try:
            data = await asyncio.to_thread(vault().read, path, Role.PREVIEW_PEAKS)
        except (OSError, VaultError):
            raise JobError("The preview is damaged; remove the job and add the file again.", 500) from None
        if request.method == "HEAD":
            return Response(media_type="application/octet-stream", headers={"content-length": str(len(data))})
        return Response(data, media_type="application/octet-stream")

    def track_view(o: Opened) -> TrackView:
        return TrackView(track=o.track, revision=o.revision, media_end_ms=o.media_end_ms, violations=o.violations,
                         preset_changed=o.preset_changed)  # fmt: skip

    @app.get("/api/jobs/{job_id}/words")
    async def job_words(job_id: str) -> WordsView:
        return WordsView(words=await live().words(job_id))

    @app.get("/api/jobs/{job_id}/tracks/{preset_id}/words")
    async def track_words(job_id: str, preset_id: str) -> WordsView:
        return WordsView(words=await live().words(job_id, preset_id))

    @app.get("/api/jobs/{job_id}/tracks/{preset_id}")
    async def open_track(job_id: str, preset_id: str) -> TrackView:
        return track_view(await live().open_track(job_id, preset_id))

    @app.post("/api/jobs/{job_id}/tracks/{preset_id}/check")
    async def check_track(job_id: str, preset_id: str, req: CheckRequest) -> CheckResult:
        return CheckResult(violations=await live().check_cues(job_id, preset_id, req.cues))

    @app.put("/api/jobs/{job_id}/tracks/{preset_id}")
    async def save_track(job_id: str, preset_id: str, req: SaveTrackRequest) -> TrackSaved:
        o = await live().save_track(job_id, preset_id, req)
        return TrackSaved(revision=o.revision, next_cue_id=o.track.next_cue_id, violations=o.violations)

    @app.post("/api/jobs/{job_id}/tracks/{preset_id}/resegment")
    async def resegment_track(job_id: str, preset_id: str, req: ResegmentRequest) -> TrackView:
        return track_view(await live().resegment(job_id, preset_id, req.base_revision))

    @app.websocket("/api/events")
    async def events(ws: WebSocket) -> None:
        await hub.serve(ws, status_now)

    @app.get("/", include_in_schema=False)
    async def index() -> Response:
        return page.response("index.html")

    @app.get("/{name:path}", include_in_schema=False)
    async def page_file(name: str) -> Response:
        return page.response(name)

    upload_limit = cfg.upload_limit or (lambda: uploads.limit(cfg.data_dir))
    asgi = LocalOnly(app, cfg.port, upload_path=UPLOAD_PATH, upload_limit=upload_limit,
                     upload_room=lambda: uploads.room(cfg.data_dir), peer_check=cfg.peer_check,
                     body_limits=[(TRACK_PATHS, TRACK_BODY_LIMIT), (SETTINGS_PATH, SETTINGS_BODY_LIMIT)])  # fmt: skip
    return Server(asgi=asgi, app=app, hub=hub, state=state)


class _NoSecondCrashLog(logging.Filter):
    """Starlette raises a request's error again after the app's own handler has logged it (with the input text
    left out); uvicorn would then log the whole traceback, input text and all, a second time."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not str(record.msg).startswith("Exception in ASGI application")


_NO_SECOND_CRASH_LOG = _NoSecondCrashLog()


def _quiet_uvicorn_crashes() -> None:
    logger = logging.getLogger("uvicorn.error")
    if _NO_SECOND_CRASH_LOG not in logger.filters:
        logger.addFilter(_NO_SECOND_CRASH_LOG)


def _error(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(ErrorResponse(error=message).model_dump(), status_code=status_code)


def _ranges(v: Vault, path: Path, media_type: str, range_header: str | None, head: bool) -> Response:
    """A sealed file served like a plain one: 200 / 206 / 416, decrypting only the bytes asked for.

    The first block is decrypted before the headers go out, so a damaged file is an error, not a cut-off stream.
    """
    try:
        reader = v.reader(path, Role.PREVIEW_MEDIA)
    except (OSError, VaultError):
        return _error(500, "The preview is damaged; remove the job and add the file again.")
    size = reader.size
    rng = blobserve.parse_range(range_header, size)
    if rng is False:
        reader.close()
        return Response(status_code=416, headers={"content-range": f"bytes */{size}"})
    start, end = rng if isinstance(rng, tuple) else (0, size - 1)
    length = max(0, end - start + 1)
    headers = {"accept-ranges": "bytes", "content-length": str(length)}
    if isinstance(rng, tuple):
        headers["content-range"] = f"bytes {start}-{end}/{size}"
    status = 206 if isinstance(rng, tuple) else 200
    if head:
        reader.close()
        return Response(status_code=status, media_type=media_type, headers=headers)
    try:
        first = reader.read_at(start, min(length, _STREAM_BLOCK))
    except VaultError:
        reader.close()
        return _error(500, "The preview is damaged; remove the job and add the file again.")

    def body() -> Iterator[bytes]:
        try:
            yield first
            pos = start + len(first)
            while pos <= end:
                piece = reader.read_at(pos, min(_STREAM_BLOCK, end - pos + 1))
                if not piece:
                    return
                yield piece
                pos += len(piece)
        finally:
            reader.close()

    return StreamingResponse(body(), status_code=status, media_type=media_type, headers=headers)
