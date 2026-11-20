# Changelog

All notable changes to this project are listed here, one entry per version.

## 1.1.0 — 2026-11-20

### New
- A download-and-run package for any Windows PC: unzip, double-click `TalkSubs.bat`. It runs the speech model on the
  processor, brings its own Python runtime and FFmpeg, and downloads the speech model on the first start.
- The app is now called TalkSubs.
- Translate in the editor: a column beside the English subtitles for their translation, with its line length and
  reading speed; saved as its own subtitle files (for example `lecture.cz-template.cs.srt`) and in the Excel table.
- Your own subtitle types in Settings, started from a published guideline (Netflix-style, TED-style, the Czech
  translators' guidelines and more) or as a copy, with every value adjustable; a removed type keeps its subtitles.
- Type a subtitle's start or end time by hand.
- Every button explains itself when you point at it; "Keyboard" is now "Controls" and lists the mouse too.
- A step-by-step guide on the Help page (topics beside their steps), also in `docs/guide.md` and `docs/navod.md`.
- Two subtitle types after Czech lecture subtitling practice (40 characters, 15 or 17 characters per second).
- Put the subtitle types in your own order (drag or arrows in Settings); every list follows it.
- Leaving Settings with unsaved changes asks first.
- On a PC with an NVIDIA graphics card the app offers to download the card's support once (1.7 GB, NVIDIA's
  official files): transcription then runs on the card, about twice as fast (more on a PC with a slower
  processor), with the same subtitles. The download goes on after an interruption; a damaged file is noticed and
  offered again.
- MIT licence.
- Help → About names the author, with a link to the author's website.

### Changed
- The app no longer comes with a list of terms: list the names and terms of your own field in Settings
  (Terms and names); the app puts them right in the subtitles.
- The app uses the FFmpeg shipped beside it when there is one, before any FFmpeg on PATH.
- Settings no longer offers Whisper, which was never installed; Parakeet is the one speech recogniser.
- Subtitles leave out "uh", "um" and similar sounds (a preset setting, on by default; the transcript keeps them).
- Words in the middle of a sentence no longer start with a capital just because a pause came before them.
- The subtitle editor shows the current position in the recording in large figures next to Play, with Stop and
  10-second jumps (also the ← and → keys).
- Remove asks first and says what will be deleted and what stays, for a chosen file, a dropped file and a link.
- The Help page explains what happens inside the app, and its security and privacy in detail.
- A cleaner editor: the tools in one bar (Undo and Redo, a translation switch), the subtitle's length and reading
  speed coloured by the rules, Delete set apart, and a volume slider for sound-only recordings.
- Hover tips open above the control, so they never cover what you type.

### Fixed
- In the editor, the play cursor could run ahead of the waveform when the listening copy of a long recording came
  out incomplete; an incomplete listening copy is now made again instead of being used.
- A dropped file was refused as "not enough disk space" on a drive with about 2 GB free: the app now keeps 1 GB free
  for its work (was 2 GB), and the message says how much the file takes and how much is free.
- Links: the website's certificate is now checked for every part of the download, and a link must lead to one
  recording file (not a playlist that names further addresses); a site that answers very slowly no longer holds up
  the other links.
- The app window no longer opens onto a server of another Windows account that took the app's port first.
- Settings with a long list of terms or many own types can be saved again.
- Settings saved by 1.0.0 with Whisper chosen now open with Parakeet instead of refusing every change.
- "Leave without saving" in Settings now really drops the unsaved changes.
- Splitting a subtitle in the editor keeps each word's exact time also where "uh" or "um" was left out.
- A left-out "uh?" or "um." keeps its question mark or full stop after a comma or a closing quote.
- A damaged graphics-card support file now always lets that run go on on the processor.
- The download package: starts from a folder whose name has brackets (for example "TalkSubs (1)"), says to unpack
  the zip when started inside it, installs every library checked against its pinned fingerprint, and includes the
  licence texts of the window's libraries (THIRD-PARTY-NOTICES.txt).

## 1.0.0 — 2026-11-19

The first complete version: a local app that turns English lectures into timed English subtitles for translators.

### Adding lectures
- Add audio or video files with the Windows file dialog, by dropping them on the window, or by pasting a download
  link. Of every video only the sound is kept; subtitles keep the video's own frame rate.
- A link is downloaded on the processor while the graphics card works on other lectures; an expired link or a
  dropped connection is reported, never taken for a shorter lecture.

### Transcription
- Word-timed speech recognition with NVIDIA Parakeet TDT 0.6B v2 and speech detection: on silence and noise it
  writes nothing rather than inventing text.
- First start: the speech model (2.5 GB) is downloaded once, with true progress and the time left; an interrupted
  download goes on where it stopped, every file is checked, and afterwards the app works without the internet.
  Without a connection the app says so and offers "Try again".
- A queue that runs one lecture at a time, with live progress, a time estimate for every waiting lecture, and
  cancel at any time.
- Any PC: without a usable NVIDIA card the app transcribes on the processor (same result, slower) and says so and
  why. A short speed test measures the PC on the first start; finished lectures refine the estimate.
- Long lectures (3-5 hours and more): a transcription interrupted by closing the app, a crash, a full graphics card
  or a full disk goes on where it stopped, with the same result; the queue waits and tries again by itself.
- Terms and names: a glossary puts terms and names right in the subtitles (your own list in Settings); subtitles you edited are never changed.

### Subtitles
- Two kinds: "Czech translation template" (up to 17 characters per second, room for the longer translation) and
  "English subtitles" (up to 20). Both: at most 42 characters × 2 lines, 1-7 s per subtitle, a gap of at least
  2 frames, good line breaks, and a rule checker that lists every problem.
- Save as SRT, WebVTT, ASS, text with times, or an Excel table with an empty Translation column; save again with
  other choices without a new transcription.
- Subtitle editor: player and waveform with draggable subtitle edges; edit, split, join, insert, delete, move by
  one frame; live rule warnings, undo and redo, keyboard shortcuts, automatic saving; start a track over.

### The app
- A window in English or Czech, light or dark, with a Help page: how it works, the two kinds of subtitles, file
  types, privacy, the editor's keys, and the models' credits and licences.
- The app's own icon on the taskbar.

### Privacy
- Everything runs on your computer; the app answers only programs of the Windows user who started it.
- Everything the app keeps for itself is encrypted with a key tied to your Windows account; if that key is missing
  or belongs to another account, the app says so and can start fresh, keeping the old work aside.
- The window keeps no copies of transcripts or media; the log never names your files or shows the secret part of
  a link. Saved subtitles are ordinary files where you choose.

### For developers
- Command line: `python -m parakeet_stt transcribe <file> -o <dir> --preset all --format all`, and
  `export <project>` to make other subtitles again without the GPU.
- Environment scripts that keep every cache and model inside the application folder, an environment check
  (`scripts/doctor.ps1`), and one quality gate (`scripts/gate.ps1`, with `-Canary` to prove it can fail).
