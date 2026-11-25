"""Command line: `python -m parakeet_stt transcribe <file> -o <dir>` and `export <project> -o <dir>`."""

from __future__ import annotations

import argparse
import ctypes
import json
import signal
import sys
import time
from ctypes import wintypes
from pathlib import Path
from types import FrameType
from typing import TextIO

from pydantic import ValidationError

from parakeet_stt import (
    __version__,
    blobserve,
    engines,
    export,
    glossary,
    media,
    models,
    pipeline,
    plain_layout,
    presets,
    project,
    subtitles,
    vault,
    whisperx_import,
)
from parakeet_stt.cancel import Cancelled, CancelToken
from parakeet_stt.engines import whisper
from parakeet_stt.engines.base import EngineError
from parakeet_stt.pipeline import Progress, transcribe_file

EXIT_OK, EXIT_USAGE, EXIT_ENGINE, EXIT_CANCELLED = 0, 2, 3, 130


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="parakeet-stt", description="Lecture audio/video to a timed English transcript.")
    sub = p.add_subparsers(dest="command", required=True)
    t = sub.add_parser("transcribe", help="transcribe one media file")
    t.add_argument("file", type=Path)
    t.add_argument("-o", "--out", type=Path, required=True, help="output folder")
    t.add_argument(
        "--engine", default=engines.DEFAULT, choices=engines.names(),
        help="parakeet (default, fast) or, where installed, whisper (the language can be set with --language)",
    )  # fmt: skip
    t.add_argument(
        "--model", default="v2", choices=list(models.SPEECH_MODELS),
        help="speech model: v2 (English only, default) or v3 (multilingual: 25 European languages incl. Czech, German)",
    )  # fmt: skip
    t.add_argument(
        "--language", metavar="CODE",
        help="whisper only: the spoken language (de, cs, pl, ru, es, ... or auto); Parakeet detects it itself",
    )  # fmt: skip
    t.add_argument(
        "--whisper-model", default=whisper.DEFAULT_MODEL, choices=list(models.WHISPER_MODELS),
        help=f"whisper only: the model (default {whisper.DEFAULT_MODEL}; tiny is for quick checks)",
    )  # fmt: skip
    t.add_argument(
        "--batch", type=int, default=1, choices=(1, 2), help="speech windows per GPU step (2 = faster, ~7 GB)"
    )
    t.add_argument("--progress-log", type=Path, help="write every progress event as JSON lines (diagnostics)")
    _subtitle_args(t)
    e = sub.add_parser("export", help="make subtitle files from a project file (no GPU)")
    e.add_argument("project", type=Path)
    e.add_argument("-o", "--out", type=Path, help="output folder (default: next to the project file)")
    e.add_argument(
        "--text", action="append", choices=["original", "translation"],
        help="what the files hold: the cues' own text and/or their translation (repeatable; default original)",
    )  # fmt: skip
    _subtitle_args(e)
    r = sub.add_parser("rechunk", help="make subtitle cues from a WhisperX JSON (no GPU): its words, this app's rules")
    r.add_argument("whisperx_json", type=Path, help="the <name>.json WhisperX wrote")
    r.add_argument("media", type=Path, help="the media file it was made from (its length is checked)")
    r.add_argument("-o", "--out", type=Path, required=True, help="output folder")
    _subtitle_args(r)
    s = sub.add_parser("schema", help="print the transcript JSON Schema, or --write it to schema/")
    s.add_argument("--write", action="store_true")
    return p


def _subtitle_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--preset", action="append", choices=[*presets.names(), "all"],
        help=f"subtitle rules; repeat for several, 'all' for every one (default {presets.DEFAULT})",
    )  # fmt: skip
    p.add_argument(
        "--glossary", action="append", type=Path, metavar="FILE",
        help="a list of your own terms (one per line, as in Settings: 'Term = wrong1, wrong2'); repeatable, later wins",
    )  # fmt: skip
    p.add_argument(
        "--layout", default="standard", choices=("standard", "plain"),
        help="'plain': write <name>.srt and a WhisperX-shaped <name>.json (first preset only) instead of the "
        "<name>.<preset>.<format> files",
    )  # fmt: skip
    p.add_argument(
        "--format", default="srt", type=_formats,
        help=f"comma-separated: {','.join(export.FORMATS)} or 'all' (default srt)",
    )  # fmt: skip


def _formats(text: str) -> list[str]:
    wanted = list(export.FORMATS) if text == "all" else [f.strip().lower() for f in text.split(",") if f.strip()]
    unknown = [f for f in wanted if f not in export.FORMATS]
    if unknown or not wanted:
        raise argparse.ArgumentTypeError(
            f"unknown format {', '.join(unknown) or text!r}; use {', '.join(export.FORMATS)}"
        )
    return wanted


def _engine_options(args: argparse.Namespace, gl: glossary.Glossary | None) -> dict[str, object]:
    """The chosen engine's options. Whisper also gets the spoken language and the word list's terms as its prompt."""
    if args.engine == "whisper":
        terms = [e.term for e in gl.entries] if gl is not None else []
        return {"model": args.whisper_model, "language": args.language, "prompt": whisper.prompt_from_terms(terms)}
    if args.language:
        raise _UsageError("--language applies to --engine whisper only: Parakeet detects the language itself")
    return {"batch_size": args.batch, "model": args.model}


def _glossary(files: list[Path] | None) -> glossary.Glossary | None:
    """The built-in glossary plus the files' terms (None: no file given, so the built-in one applies)."""
    if not files:
        return None
    texts = []
    for f in files:
        try:
            texts.append(f.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError) as exc:
            raise glossary.GlossaryError(f"The glossary {f.name} cannot be read: {exc}") from None
    return glossary.current("\n".join(texts))


def _presets(chosen: list[str] | None) -> list[str]:
    if not chosen:
        return [presets.DEFAULT]
    return presets.names() if "all" in chosen else list(dict.fromkeys(chosen))


class _UsageError(Exception):
    """Options that do not go together; the message is for the user."""


class _InsideStore(Exception):
    pass


def _not_inside_store(out_dir: Path) -> None:
    """Plain files never go where the app keeps its sealed work (its own export folder is fine)."""
    if not vault.plain_allowed(out_dir):
        raise _InsideStore(f"{out_dir} is inside the app's own store; choose another folder.")


def _vault_of(path: Path) -> vault.Vault | None:
    """The key a project file must be (re)written with: its store's, when it is sealed or lies in a store's
    sealed part (a plain file an older version left there); None for a project of its own."""
    if vault.is_sealed(path) or not vault.plain_allowed(path.parent):
        return vault.locate(path)
    return None


def _stem(name: str) -> str:
    return name.removesuffix(project.SUFFIX) if name.endswith(project.SUFFIX) else name.rsplit(".", 1)[0]


class _ProgressPrinter:
    def __init__(self, stream: TextIO, log: TextIO | None) -> None:
        self.stream, self.log = stream, log
        self.last_pct = -1
        self.last_phase = ""

    def __call__(self, p: Progress) -> None:
        if self.log is not None:
            self.log.write(json.dumps({"t": time.time(), "phase": p.phase, "fraction": p.fraction,
                                       "media_done_s": p.media_done_s}) + "\n")  # fmt: skip
        pct = int(p.fraction * 100)
        if pct > self.last_pct or p.phase != self.last_phase:
            self.last_pct, self.last_phase = pct, p.phase
            self.stream.write(f"{pct:3d}%  {p.phase:<9} {p.media_done_s:7.0f} / {p.media_total_s:.0f} s\n")
            self.stream.flush()


def _peak_memory_mb() -> float:
    if sys.platform != "win32":
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # kilobytes on Linux, bytes on macOS
        return float(peak) / (2**20 if sys.platform == "darwin" else 1024)
    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]  # fmt: skip

    c = Counters()
    c.cb = ctypes.sizeof(c)
    psapi = ctypes.WinDLL("psapi")
    kernel = ctypes.WinDLL("kernel32")
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(c), c.cb):
        return float("nan")
    return float(c.PeakWorkingSetSize) / 2**20


def _download_models(cancel: CancelToken, repos: tuple[models.Repo, ...] = models.NEEDED) -> None:
    """The first run on a PC: the speech model, with its progress (at most once a second)."""
    last = 0.0

    def show(done: int, total: int) -> None:
        nonlocal last
        now = time.monotonic()
        if now - last >= 1 or done == total:
            last = now
            print(f"\rDownloading the speech model (once): {done / 1e9:.2f} of {total / 1e9:.2f} GB", end="",
                  file=sys.stderr, flush=True)  # fmt: skip

    models.fetch(show, cancel, repos=repos)
    print(file=sys.stderr)


def _transcribe(args: argparse.Namespace) -> int:
    cancel = CancelToken()

    def on_sigint(_sig: int, _frame: FrameType | None) -> None:
        if cancel.cancelled:  # second Ctrl+C: stop at once
            raise KeyboardInterrupt
        print("\nCancelling...", file=sys.stderr, flush=True)
        cancel.cancel()

    previous = signal.signal(signal.SIGINT, on_sigint)
    log = None
    if args.progress_log:
        args.progress_log.parent.mkdir(parents=True, exist_ok=True)
        log = args.progress_log.open("w", encoding="utf-8")
    t0 = time.perf_counter()
    try:
        _not_inside_store(args.out)
        gl = _glossary(args.glossary)  # before the long recognition: a bad list fails at once
        options = _engine_options(args, gl)
        whisper_run = args.engine == "whisper"
        model_repo = models.WHISPER_MODELS[args.whisper_model] if whisper_run else models.SPEECH_MODELS[args.model][0]
        needed = (model_repo, models.VAD)
        if not models.present(needed):
            _download_models(cancel, needed)
        with blobserve.media_input(args.file, _vault_of(args.file)) as src:
            result = transcribe_file(
                args.file,
                engine_name=args.engine,
                engine_options=options,
                on_progress=_ProgressPrinter(sys.stderr, log),
                cancel=cancel,
                source=src,
            )
        stem = _stem(args.file.name)
        out_json = args.out / f"{stem}{project.SUFFIX}"
        if cancel.cancelled:
            raise Cancelled
        try:
            doc, reports = subtitles.ensure_tracks(result.document, _presets(args.preset), gl)
            if args.layout == "plain":
                outputs = plain_layout.files(doc, reports[0].track, gl, args.out, stem)
            else:
                outputs = subtitles.files(doc, [r.track.preset_id for r in reports], args.format, args.out, stem)
        except (subtitles.SubtitleError, presets.PresetError, ValidationError) as exc:
            # the recognition took minutes on the GPU: never lose it to a subtitle problem
            project.write_atomically({out_json: project.dumps(result.document)})
            print(f"Error making the subtitles: {exc}", file=sys.stderr)
            print(f"The transcript was saved: {out_json}", file=sys.stderr)
            return EXIT_USAGE
        # plain layout: a caller collecting every file of the folder gets only the .srt and .json it reads
        keep = {} if args.layout == "plain" else {out_json: project.dumps(doc)}
        project.write_atomically({**keep, **outputs})
    except Cancelled:
        print("Cancelled. Nothing was written.", file=sys.stderr)
        return EXIT_CANCELLED
    except (media.MediaError, project.ProjectError, subtitles.SubtitleError, presets.PresetError, vault.VaultError,
            glossary.GlossaryError, _InsideStore, _UsageError) as exc:  # fmt: skip
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except (EngineError, models.ModelFetchError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_ENGINE
    finally:
        signal.signal(signal.SIGINT, previous)
        if log is not None:
            log.close()
    wall = time.perf_counter() - t0
    words = len(doc.words())
    j = result.stats.joins
    print(
        f"Done: {words} words in {len(doc.segments)} segments, {doc.media.decoded_ms / 1000:.0f} s of audio in "
        f"{wall:.1f} s ({doc.media.decoded_ms / 1000 / wall:.0f}x real time); peak memory {_peak_memory_mb():.0f} MB; "
        f"joins {j.forced_joins} forced / {j.hard_splits} split, {result.stats.words_dropped_at_joins} repeated words "
        f"dropped",
        file=sys.stderr,
    )
    for r in reports:
        print(r.line(), file=sys.stderr)
    if args.layout != "plain":
        print(out_json)
    for path in outputs:
        print(path)
    return EXIT_OK


def _rechunk(args: argparse.Namespace) -> int:
    try:
        _not_inside_store(args.out)
        gl = _glossary(args.glossary)
        data = json.loads(args.whisperx_json.read_text(encoding="utf-8"))
        info = media.probe(args.media)
        doc = whisperx_import.document_from_whisperx(
            data, pipeline._media(info, info.duration_s), app_version=__version__
        )
        stem = _stem(args.media.name)
        doc, reports = subtitles.ensure_tracks(doc, _presets(args.preset), gl)
        if args.layout == "plain":
            outputs = plain_layout.files(doc, reports[0].track, gl, args.out, stem)
        else:
            outputs = subtitles.files(doc, [r.track.preset_id for r in reports], args.format, args.out, stem)
        project.write_atomically(outputs)
    except (
        media.MediaError, project.ProjectError, subtitles.SubtitleError, presets.PresetError, glossary.GlossaryError,
        ValidationError, ValueError, OSError, _InsideStore,
    ) as exc:  # fmt: skip
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    for r in reports:
        print(r.line(), file=sys.stderr)
    for path in outputs:
        print(path)
    return EXIT_OK


def _export(args: argparse.Namespace) -> int:
    try:
        sealed_by = _vault_of(args.project)
        doc = project.load(args.project, sealed_by if vault.is_sealed(args.project) else None)
        out_dir = args.out or args.project.parent
        _not_inside_store(out_dir)
        gl = _glossary(args.glossary)
        new_doc, reports = subtitles.ensure_tracks(doc, _presets(args.preset), gl)
        made, skipped = (plain_layout.files(new_doc, reports[0].track, gl, out_dir, _stem(args.project.name)), []) \
            if args.layout == "plain" else subtitles.files_for(
            new_doc,
            [r.track.preset_id for r in reports],
            args.format,
            args.text or ["original"],
            out_dir,
            _stem(args.project.name),
        )
        for pid in skipped:
            print(f"{pid}: no cue is translated yet, so no translated file", file=sys.stderr)
        outputs: dict[Path, str | bytes] = dict(made)
        if any(not r.reused for r in reports):
            outputs[args.project] = project.stored(new_doc, sealed_by)  # keep the new tracks with the project
        project.write_atomically(outputs)
    except (project.ProjectError, subtitles.SubtitleError, presets.PresetError, vault.VaultError,
            glossary.GlossaryError, _InsideStore) as exc:  # fmt: skip
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    for r in reports:
        print(r.line(), file=sys.stderr)
    for path in outputs:
        print(path)
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "schema":
        text = project.schema_text()
        if args.write:
            project.write_atomically({project.SCHEMA_FILE: text})
            print(project.SCHEMA_FILE)
        else:
            sys.stdout.write(text)
        return EXIT_OK
    if args.command == "export":
        return _export(args)
    if args.command == "rechunk":
        return _rechunk(args)
    if not args.file.is_file():
        print(f"Error: file not found: {args.file}", file=sys.stderr)
        return EXIT_USAGE
    return _transcribe(args)
