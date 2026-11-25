"""Everything the server and the window say to each other, plus the persisted queue.

These models are the contract with the web UI: `scripts/gen_types.py` turns them into
`web/src/api/contract.gen.ts`, and the gate fails when the two drift apart. Grow additively.
"""

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel

from parakeet_stt import models, presets
from parakeet_stt.engines import DEFAULT as DEFAULT_ENGINE
from parakeet_stt.pipeline import Phase
from parakeet_stt.presets import Preset
from parakeet_stt.rules import Violation
from parakeet_stt.transcript import Cue, SubtitleTrack, Word

APP_ID: Final = "parakeet-stt"
QUEUE_SCHEMA_VERSION: Final = 1
SETTINGS_SCHEMA_VERSION: Final = 3  # 2: the user's own subtitle types; 3: their order

# "fetching": a link job's sound is being downloaded (on the CPU); it is "queued" for the GPU once it is in
JobState = Literal["fetching", "queued", "running", "done", "failed", "cancelled"]
FINISHED: Final[frozenset[str]] = frozenset({"done", "failed", "cancelled"})


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FileSource(_Model):
    """A media file on this machine, used where it is."""

    kind: Literal["file"] = "file"
    path: str


class UploadSource(_Model):
    """A file dropped onto the window: the page cannot say where it lives, so the app keeps its own copy."""

    kind: Literal["upload"] = "upload"
    upload_id: str = Field(pattern=r"^[0-9a-f]{12}$")  # the copy lives in <data>/uploads/<upload_id>/
    path: str  # the copy
    original_name: str  # for display and for naming the subtitle files
    # the app keeps only the sound of a video; the subtitles keep the video's frame grid (ADR 0031)
    had_video: bool = False
    frame_rate: str | None = None  # the picture's exact rate, "num/den" (e.g. "30000/1001")


class LinkSource(_Model):
    """A recording behind a download link. Only its sound is fetched and kept (sealed, in the upload folder); the
    link itself lives sealed beside it and never appears in a message - a presigned link's signature is a secret."""

    kind: Literal["link"] = "link"
    upload_id: str = Field(pattern=r"^[0-9a-f]{12}$")  # the sound lives in <data>/uploads/<upload_id>/
    path: str  # where the fetched sound is (or will be)
    shown: str  # the link without its secret parts, for display: "host/…/file.mp4"
    had_video: bool = False  # known once the sound is in
    frame_rate: str | None = None


JobSource = Annotated[FileSource | UploadSource | LinkSource, Field(discriminator="kind")]


JobPhase = Literal["fetch"] | Phase  # "fetch": a link job's download (before the pipeline's own phases)


class JobProgress(_Model):
    phase: JobPhase
    fraction: float = Field(ge=0.0, le=1.0)  # of the whole job, never decreasing
    media_done_s: float
    media_total_s: float
    eta_s: float | None = None  # unknown until the job has run long enough to judge


PreviewState = Literal["waiting", "making", "ready", "failed"]


class JobPreview(_Model):
    """The small copy of the media the window plays, and its waveform. Live only: rebuilt from disk on every start."""

    state: PreviewState
    kind: Literal["video", "audio"] | None = None  # known once it is ready
    fraction: float = Field(default=0.0, ge=0.0, le=1.0)  # while making
    media_done_s: float = 0.0
    media_total_s: float = 0.0
    error: str | None = None  # why it failed
    peaks_per_s: int | None = None  # waveform resolution (min/max pairs per second) once ready


class Job(_Model):
    id: str = Field(pattern=r"^[0-9a-f]{12}$")
    name: str  # what the user sees: the media file name
    source: JobSource
    state: JobState = "queued"
    created_at: float  # unix seconds
    started_at: float | None = None
    finished_at: float | None = None
    attempts: int = 0  # starts so far; a job interrupted again and again stops being retried
    error: str | None = None  # why it failed
    note: str | None = None  # something to know about a job that succeeded (e.g. subtitles could not be made)
    project_file: str | None = None  # file name inside the job's folder once done
    progress: JobProgress | None = None  # live only: always stored as null in the queue file
    engine: str = DEFAULT_ENGINE  # the recognition engine chosen when the job was added
    exported: list[str] = []  # full paths of the subtitle files the last export wrote
    preview: JobPreview | None = None  # live only: always stored as null in the queue file
    media_s: float | None = None  # the recording's length, once known (when added; a link's after its download)


class QueueFile(_Model):
    schema_version: int = QUEUE_SCHEMA_VERSION
    jobs: list[Job] = []


# Why the queue waits: the GPU is held by another transcription, its memory is full (another program), or the disk
# is too full to save a finished transcript.
PausedKind = Literal["gpu_busy", "gpu_memory", "disk_full"]


Device = Literal["cuda", "cpu"]
# Why the model runs where it does: "card" = a usable NVIDIA card; the others say why the processor does it.
DeviceReason = Literal[
    "card",
    "no_driver",
    "no_card",
    "card_too_old",
    "card_too_small",
    "no_card_support",
    "card_pack_missing",
    "driver_too_old",
]


class MachineInfo(_Model):
    """What this PC runs the model on, and how fast: a job takes about fixed_s + media_s * per_s seconds."""

    device: Device
    reason: DeviceReason
    card: str | None = None  # the NVIDIA card's name, when one was found
    cpu_threads: int | None = None  # processor: the thread count in use (the speed test's fastest)
    fixed_s: float | None = None  # None: not measured yet
    per_s: float | None = None
    measured_by: Literal["test", "jobs"] | None = None  # the speed test, or finished transcriptions
    measuring: bool = False  # the speed test is running now
    measure_waiting: bool = False  # it was asked for and runs after the transcription in progress


# "absent": not downloaded and waiting for the user (only the graphics-card support; the model starts by itself)
ModelsState = Literal["checking", "absent", "downloading", "ready", "failed"]


class ModelsInfo(_Model):
    """The speech model's files: downloaded once, on the first start; the queue waits until they are in."""

    state: ModelsState
    done_bytes: int = Field(default=0, ge=0)  # on disk so far (a resumed download starts at what was there)
    total_bytes: int = Field(default=0, ge=0)
    eta_s: float | None = None  # unknown until the download has run long enough to judge
    error: str | None = None  # why it failed (a sentence for the user, in English)
    error_kind: models.ErrorKind | None = None  # the same as a fixed code, for the window's own text
    need_bytes: int | None = None  # error_kind "no_space": what the download needs, and what the drive has free
    free_bytes: int | None = None


class Status(_Model):
    """The whole state, sent on every (re)connect and from GET /api/status."""

    app: Literal["parakeet-stt"] = APP_ID
    app_version: str
    boot_id: str  # changes on every server start, so a client can tell a restart from lost messages
    seq: int  # the last event number included in this snapshot
    paused_reason: str | None = None  # why the queue is not starting the next job (GPU used elsewhere)
    paused_kind: PausedKind | None = None  # the same as a fixed code, for the window's own text
    jobs: list[Job]
    # the work kept in the app cannot be opened (its key is missing or belongs to another Windows account);
    # nothing runs until the user starts fresh (the old work is moved aside, never deleted)
    storage_locked: bool = False
    machine: MachineInfo | None = None  # None until the app has looked at this PC (a moment after it starts)
    models: ModelsInfo | None = None
    # the download package's graphics-card support (offered when machine.reason is "card_pack_missing")
    card_pack: ModelsInfo | None = None
    # the Whisper engine's model: downloaded when the user chooses Whisper (only where the engine is installed)
    whisper_model: ModelsInfo | None = None


class SnapshotEvent(_Model):
    type: Literal["snapshot"] = "snapshot"
    seq: int
    status: Status


class JobEvent(_Model):
    type: Literal["job"] = "job"
    seq: int
    job: Job


class ProgressEvent(_Model):
    type: Literal["progress"] = "progress"
    seq: int
    job_id: str
    progress: JobProgress


class PreviewEvent(_Model):
    type: Literal["preview"] = "preview"
    seq: int
    job_id: str
    preview: JobPreview | None


class PausedEvent(_Model):
    type: Literal["paused"] = "paused"
    seq: int
    reason: str | None
    kind: PausedKind | None = None


class MachineEvent(_Model):
    type: Literal["machine"] = "machine"
    seq: int
    machine: MachineInfo


class ModelsEvent(_Model):
    type: Literal["models"] = "models"
    seq: int
    models: ModelsInfo


class CardPackEvent(_Model):
    type: Literal["card_pack"] = "card_pack"
    seq: int
    card_pack: ModelsInfo


class WhisperModelEvent(_Model):
    type: Literal["whisper_model"] = "whisper_model"
    seq: int
    whisper_model: ModelsInfo


class RemovedEvent(_Model):
    type: Literal["removed"] = "removed"
    seq: int
    job_id: str


ServerEvent = Annotated[
    SnapshotEvent
    | JobEvent
    | ProgressEvent
    | PreviewEvent
    | PausedEvent
    | RemovedEvent
    | MachineEvent
    | ModelsEvent
    | CardPackEvent
    | WhisperModelEvent,
    Field(discriminator="type"),
]


class ServerMessage(RootModel[ServerEvent]):
    """One WebSocket frame from the server (named, so the generated types have a single union to switch on)."""


class AddJobRequest(_Model):
    path: str = Field(min_length=1, max_length=4096)


class AddLinkRequest(_Model):
    url: str = Field(min_length=1, max_length=8192)


class ErrorResponse(_Model):
    error: str  # a sentence for the user


OutputPlace = Literal["beside_source", "folder"]
Language = Literal["en", "cs"]
Look = Literal["light", "dark"]


class Settings(_Model):
    """The user's choices, kept in <data>/settings.json (not in the browser, which can be reset)."""

    schema_version: int = SETTINGS_SCHEMA_VERSION
    engine: str = DEFAULT_ENGINE
    export_presets: list[str] = Field(default_factory=presets.names)  # every built-in type
    export_formats: list[str] = Field(default_factory=lambda: ["srt"])
    output: OutputPlace = "beside_source"  # dropped files have no known folder: they always use output_dir
    output_dir: str | None = None  # None: <data>/subtitles
    language: Language = "en"  # the window's language
    # the spoken language for the Whisper engine ("auto": Whisper detects it); Parakeet detects it itself
    speech_language: str = Field(default="auto", max_length=8)
    look: Look = "light"  # the window's colours
    # the user's own terms and names, one per line as typed (notes included); see `glossary.parse_text`
    glossary_text: str = Field(default="", max_length=200_000)
    # the user's own subtitle types, and the ones they removed (kept so subtitles made with them still open and save)
    custom_presets: list[Preset] = Field(default_factory=list)  # at most 20 (checked with the settings)
    retired_presets: list[Preset] = Field(default_factory=list)  # at most 50
    # the order the types are listed in everywhere (ids; a type not named comes after, in its own order)
    type_order: list[str] = Field(default_factory=list, max_length=200)


class PresetInfo(_Model):
    id: str
    kind: Literal["builtin", "custom", "retired"] = "builtin"  # retired: removed by the user, still opens its subtitles
    name: str
    description: str
    max_chars_per_line: int  # the limits, so the window can describe a preset in its own language
    max_lines: int
    max_cps: float
    max_duration_ms: int


class SettingsView(_Model):
    """GET /api/settings: the stored choices plus what can be chosen."""

    settings: Settings
    output_dir_default: str  # the app's own folder, used while output_dir is None
    engines: list[str]
    presets: list[PresetInfo]
    formats: list[str]
    account: str = ""  # the Windows account whose key locks the app's work (for the Settings page's note)
    glossary_builtin: str = ""  # the terms that come with the app, in the same text form (shown, not editable)
    standards: list[Preset] = Field(default_factory=list)  # published standards to start an own type from
    builtin_presets: list[Preset] = Field(default_factory=list)  # the built-in types in full, to copy from


ExportText = Literal["original", "translation"]  # the cues' own text, or their translation


class ExportRequest(_Model):
    presets: list[str]  # checked against the registry like the settings (none empty, none twice)
    formats: list[str]
    replace: bool = False  # overwrite subtitle files that already exist
    texts: list[ExportText] = Field(default_factory=lambda: list[ExportText](["original"]), min_length=1)


class ExportResult(_Model):
    folder: str
    files: list[str]
    skipped: list[str] = Field(default_factory=list)  # types with no translated cue, when the translation was asked


class ExportConflict(_Model):
    """409 from export: these files exist; ask the user, then send the request again with replace = true."""

    error: str
    existing: list[str]


class DialogRequest(_Model):
    kind: Literal["files", "folder"]


class DialogResult(_Model):
    paths: list[str]  # empty: the user closed the dialog


# ---- the subtitle editor -----------------------------------------------------------------------------------------


class TrackView(_Model):
    """GET /api/jobs/<id>/tracks/<preset>: one subtitle track to edit, with what the rule checker finds in it."""

    track: SubtitleTrack
    revision: str  # send it back with a save; a save on an older revision is refused (409)
    media_end_ms: int  # cues may not end later than this
    violations: list[Violation]
    preset_changed: bool  # an edited track whose preset changed since: it keeps (and is checked by) its own rules


class WordsView(_Model):
    """GET /api/jobs/<id>/words: every recognised word, in order; GET /api/jobs/<id>/tracks/<preset>/words: the
    words as that type's cues have them (fillers dropped, capitals tidied), so a split finds where a word starts."""

    words: list[Word]


class CheckRequest(_Model):
    cues: list[Cue]  # what the editor shows now; nothing is saved


class CheckResult(_Model):
    violations: list[Violation]


class SaveTrackRequest(_Model):
    base_revision: str = Field(min_length=1, max_length=64)
    cues: list[Cue]
    next_cue_id: int = Field(ge=1)
    # the language the cues' translations are in (kept with the track; None keeps what the track has)
    translation_language: str | None = Field(default=None, pattern=r"^[a-z]{2,3}$")


class TrackSaved(_Model):
    revision: str
    next_cue_id: int
    violations: list[Violation]


class ResegmentRequest(_Model):
    base_revision: str = Field(min_length=1, max_length=64)
