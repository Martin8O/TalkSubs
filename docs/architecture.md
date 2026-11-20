# Architecture

## Pipeline
```
file / link ──► media: probe (exact fps, duration) + decode to 16 kHz mono
            ──► vad: all speech windows first (≤25 s, padded; joins resolved so no audio is skipped or doubled)
            ──► engine: words with start/end times, confidence, punctuation, casing
            ──► document: words owned by one window each, times clamped; saved as `<name>.pstt.json`
            ──► glossary: term corrections (times preserved)
            ──► segment: words → cues by preset (chars, lines, reading speed, duration, gaps, line breaks)
            ──► export: SRT · WebVTT · ASS · timecoded TXT · XLSX (times quantised to frames)
```

## Components
| part | role |
|---|---|
| `parakeet_stt/transcript.py` | The transcript document — the single contract shared by pipeline, server, editor and exporters. Versioned (`schema_version`; every shape change bumps it and adds a migration in `project.py`). Segments and words are engine output and never edited. JSON Schema in `schema/` (`python -m parakeet_stt schema --write`). |
| `parakeet_stt/project.py` | Project file: atomic save, readable refusal of newer or damaged files, migrations from older versions. |
| `parakeet_stt/vault.py` | The encrypted store (ADR 0027): the DPAPI-wrapped key of a data folder and the sealed file format; whole-file and streaming read/write; where plain files may go. |
| `parakeet_stt/sound.py`, `parakeet_stt/ffpipe.py` | Keeping only the sound of a recording, sealed (ADR 0031); one ffmpeg pass on a pipe with progress and an instant cancel (shared with the preview maker). |
| `parakeet_stt/blobserve.py` | ffmpeg reading a sealed source (ADR 0028): a one-file range server for the run, `media_input` picking it or the plain path. |
| `parakeet_stt/engines/` | Engine registry. Each engine implements one protocol: `load` / `describe` / `close` / `transcribe(audio, windows, on_progress, cancel)`; progress is the media position fully recognised. |
| `parakeet_stt/pipeline.py` | One file → one document, in phases load → decode → speech → recognise with progress from real work, cooperative cancel; recognition in slices of 32 windows. |
| `parakeet_stt/checkpoint.py` | A running job's progress (ADR 0033): speech windows and finished slices, sealed in the job folder, used again only when code, engine, settings and sound are the same. |
| `parakeet_stt/gpu.py` | The only importer of onnxruntime (torch first, when installed), and the machine-wide lock (one transcription at a time, card or processor). |
| `parakeet_stt/models.py` | The model files the default engine needs (ADR 0037): two Hugging Face repositories pinned to a commit, every file with its size and SHA-256; `present()` is a few `stat` calls; `fetch()` is our own resumable download (`<name>.part`, HTTP Range, fingerprint, rename) with byte progress and failure codes. Engine and speech detector load from the pinned folder, never asking the network. |
| `parakeet_stt/machine.py` | Card or processor, and why (ADR 0035): asked of the NVIDIA driver and Windows directly, imports neither torch nor onnxruntime; processor cores by kind; background priority for a processor-mode worker. |
| `parakeet_stt/speed.py`, `parakeet_stt/benchmark.py` | The time estimate (ADR 0036): start-up + cost per second of recording per way of running, seeded by the speed test, then from finished jobs; sealed `speed.json`. |
| `presets/`, `parakeet_stt/presets.py` | Subtitle rule presets as data files (every limit, segmenter weight and severity) plus language word lists; typed and fingerprinted. |
| `parakeet_stt/glossary.py`, `presets/glossary/` | Terms and names put right in cue text (ADR 0032): built-in list + the user's own text from Settings; fingerprint kept as the second part of a track's digest. |
| `parakeet_stt/segment.py` | Words → cues: blocks at long pauses, lowest-cost cue ends and line breaks, timing on the frame grid. |
| `parakeet_stt/rules.py` | Rule checker: every violation of a track, integrity always hard, style severity from the preset; the same function feeds the editor's warnings. |
| `parakeet_stt/subtitles.py`, `parakeet_stt/export/` | Tracks for a document (reused while their fingerprint matches; an edited one is always kept, ADR 0023) and the five exporters. |
| `parakeet_stt/server/` | Local app server on 127.0.0.1 (see below). |
| `web/` | Browser UI (React + Vite + TypeScript): queue page with adding, live progress, export, open folder and remove; settings page; the subtitle editor (`#edit/<job>/<preset>`: `src/editor/` holds the operations, undo history, frame grid and the save/check hook; `views/EditorPage`, `Waveform`, `CueList`); CZ/EN and light/dark switches; a Help page (`#help`: how it works, the editor's keys, credits). Built into `parakeet_stt/server/static/` and committed (`scripts/web_check.py build`). `web/src/api/contract.gen.ts` is generated from the server's models (`python scripts/gen_types.py`); every word shown lives in `web/src/strings.ts` (one table per language). |

## App server
```
TalkSubs.bat ─► pythonw -m parakeet_stt app ─► uvicorn on 127.0.0.1:8754 (LocalOnly checks ─► FastAPI)
                                   │                   REST /api/status · /api/settings · /api/dialog · /api/jobs · PUT /api/uploads
                                   │                        /api/jobs/{id}/cancel · export · reveal · remove · media · peaks · words
                                   │                        /api/jobs/{id}/tracks/{preset} (GET/PUT) · …/check · …/resegment
                                   │                   WebSocket /api/events: snapshot, then numbered job/progress/paused/removed frames
                                   ├─► per dialog: python -m parakeet_stt dialog files|folder (owned by the app window)
                                   ├─► app window (Chromium --app, profile in .cache/app-window)
                                   └─► per job: python -m parakeet_stt worker ─► ffmpeg …   (JSON lines over private pipes)
```
| part | role |
|---|---|
| `messages.py` | Every shape exchanged with the window and the persisted queue (the contract; grows additively). |
| `runner.py` | The queue state machine on the event loop: one worker at a time, cancel then kill, GPU-busy and GPU-memory pauses, restart recovery from the checkpoint (ADR 0033-0034). Threads only read pipes. |
| `worker.py` | One job (or the speed test) in its own process: transcribe, add a track per preset, save the project file into `data/jobs/<id>/`, report its timing. |
| `previews.py` + `../preview.py` | The preview maker: two ffmpeg passes write the sound to play (fragmented MP4, sound only) and waveform peaks, sealed, into `data/jobs/<id>/preview/` (CPU only, one at a time, started when a job is added); `GET /api/jobs/<id>/media` (byte ranges, decrypted per request) and `/peaks` (int8 min/max pairs, 100 per second) serve them. The proxy's clock starts at the first audio sample, the same time 0 as the transcript. |
| `editing.py` | The subtitle editor's work on a finished job's project file: open a track, check unsaved cues with the rule checker, save on a revision fingerprint (409 when changed elsewhere), start over; always under the job's project lock in `runner.py` (shared with export and remove). |
| `hub.py` | Live events: snapshot first, numbered frames, bounded buffer per window; `Presence` stops the app 8 s after the last window is gone. |
| `security.py` | Raw ASGI checks on HTTP and WebSocket alike (the connection's Windows user first, then Host, Origin, Sec-Fetch-Site, no proxy headers, JSON writes, sizes, paths) and the response headers (`no-store` on `/api/`). |
| `owner.py` | Traces a connection to its process and that process to its Windows user (refused when in doubt). |
| `access.py` | Protected access list on the app's own folders (data folder, window profile). |
| `storage.py` | The store at start: key made or opened, plain files of an older version sealed once, the locked state and "start fresh"; `plain_files` lists anything plain in a data folder. |
| `logscrub.py` | Hides user file names, paths and URL secrets in everything written to the log. |
| `settings.py` | The user's settings in `data/settings.json`: load, validate, save atomically, the view the window shows. |
| `uploads.py` | Dropped files: name checks, the free-space limit, streaming into `data/uploads/<id>/`, a video reduced to its sound, clean-up and the start sweep. |
| `modelfetch.py` | The speech model's first download in a thread at start (ADR 0037): `Status.models` / `ModelsEvent` with true bytes and time left, nothing in the queue (nor the speed test) runs before it is in, `POST /api/models/fetch` tries again after a failure; a stop leaves the part for the next start. |
| `links.py`, `fetches.py` | Download links: checking a pasted link (https only, shown without its secret), the one-byte check, fetching only its sound into `data/uploads/<id>/`; the fetcher runs one download at a time before the GPU queue. |
| `dialogs.py`, `shell.py` | Native Windows file/folder dialogs in a child process; Explorer showing the exported files. |
| `app.py` | The route table and the built page's exact file list. |
| `launcher.py`, `browser.py`, `winjob.py` | Logging under pythonw, port binding, a second launch only opens a window, message boxes for start failures; finding a Chromium browser; kill-on-close job objects. |

`PARAKEET_STT_DATA` and `PARAKEET_STT_PORT` move the data folder and port (tests and live checks never touch `data/`).

## Environment
`parakeet_stt/env.py` is the one list of variables; every entry point applies it before anything imports torch.
`scripts/env.ps1` / `scripts/env.sh` repeat it for raw shell use (a test keeps all three identical) and point every cache, temporary folder and model store into the application
folder (`.cache/`, `models/`). `scripts/doctor.ps1` judges the environment it was started with, names the mode
(graphics card or processor, and why), then proves that stack actually runs (PyTorch CUDA kernel and ONNX Runtime
CUDA provider, or the CPU provider in processor mode; ffmpeg); with a wrong environment it stops before touching the
GPU, because a GPU start writes the CUDA kernel cache.

## Cross-cutting rules
- Times are stored in milliseconds. Subtitle cue times lie on their track's frame grid (the media's frame rate, or the
  preset's default for audio-only files); `parakeet_stt/timecode.py` is the only place that converts.
- Progress is measured from audio actually processed, never from a timer.
- Every run records its full configuration (engine, model, versions, preset, options) in the project file.
- All model weights and tool caches live inside the application folder.
