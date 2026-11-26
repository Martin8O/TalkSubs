"""Whisper through faster-whisper (CTranslate2): the second engine, for recordings Parakeet reads in the wrong language.

Parakeet v3 detects the language itself and cannot be told one; on lectures it switches into English in the middle
of German sentences. Whisper takes the language as a setting, so a translator can fix it per recording. It runs on
the same speech windows as Parakeet (Silero VAD), one window at a time, with Whisper's own word times; everything
after recognition (word lists, subtitle rules, export) is shared.

faster-whisper is optional: the engine is registered only when it is installed (`engines/__init__.py`), so an
installation without it never offers a choice that can only fail (docs/adr.md 0040).
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import sys
from collections.abc import Iterable
from typing import Any

import numpy as np
import numpy.typing as npt

from parakeet_stt import gpu, machine, models
from parakeet_stt.cancel import CancelToken
from parakeet_stt.engines.base import (
    EngineBusy,
    EngineError,
    EngineInfo,
    EngineOutOfMemory,
    ProgressFn,
    TimedSegment,
    TimedWord,
)
from parakeet_stt.vad import Window

DEFAULT_MODEL = "large-v3"  # the keys of `models.WHISPER_MODELS`
SAMPLE_RATE = 16_000
BEAM_SIZE = 5
# Whisper's prompt holds 224 tokens; a longer word list is cut here (the list still corrects the text afterwards).
MAX_PROMPT_CHARS = 600
# The languages a translator can choose; None = Whisper detects it per window.
LANGUAGES = ("bg", "cs", "da", "de", "el", "en", "es", "fi", "fr", "he", "hr", "hu", "it", "ja", "lt", "lv", "nl",
             "pl", "pt", "ro", "ru", "sk", "sl", "sv", "uk", "zh")  # fmt: skip
# Whisper splits hyphenated words and gives the second half its own entry ("Mahayana", "-Buddhisten").
_JOINERS = ("-", "\u2010", "\u2011", "\u2013")
# Whisper can loop on a non-speech sound (a bell: "ding ding ding ..." hundreds of times in six seconds). A word
# repeated more often than this in a row is cut back to this many; real speech repeats a word a few times at most.
MAX_SAME_WORD_RUN = 4
_OUT_OF_MEMORY = ("out of memory", "CUBLAS_STATUS_ALLOC_FAILED", "cudaErrorMemoryAllocation")
OUT_OF_MEMORY_MESSAGE = (
    "The graphics card has no memory left for the Whisper model; another program is probably using it. "
    "Close it, or wait: the queue tries again by itself."
)


def normalise_language(language: str | None) -> str | None:
    """ "de", "DE", "de-AT" -> "de"; "", "auto", None -> None (detect). Unknown codes are an error for the user."""
    if language is None:
        return None
    code = language.strip().lower().replace("_", "-").split("-")[0]
    if code in ("", "auto"):
        return None
    if code not in LANGUAGES:
        raise EngineError(f"Whisper does not offer the language {language!r} here; use one of {', '.join(LANGUAGES)}.")
    return code


def prompt_from_terms(terms: Iterable[str]) -> str | None:
    """The word list's terms as Whisper's prompt (it then prefers these spellings); None when there are none."""
    out: list[str] = []
    length = 0
    for term in dict.fromkeys(t.strip() for t in terms if t.strip()):  # in order, each once
        if length + len(term) + 2 > MAX_PROMPT_CHARS:
            break
        out.append(term)
        length += len(term) + 2
    return ", ".join(out) or None


def available() -> bool:
    """Whether faster-whisper is installed (checked without importing it)."""
    return importlib.util.find_spec("faster_whisper") is not None


class WhisperEngine:
    name = "whisper"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        language: str | None = None,
        prompt: str | None = None,
        cpu_threads: int | None = None,
        beam_size: int = BEAM_SIZE,
        **_other: object,  # options of other engines (Parakeet's batch_size) do not apply here
    ) -> None:
        if model not in models.WHISPER_MODELS:
            raise EngineError(f"Unknown Whisper model {model!r}; use one of {', '.join(models.WHISPER_MODELS)}.")
        if cpu_threads is not None and cpu_threads < 1:
            raise EngineError(f"The processor needs at least one thread, not {cpu_threads}.")
        self.model = model
        self.language = normalise_language(language)
        self.prompt = (prompt or "").strip()[:MAX_PROMPT_CHARS] or None
        self.cpu_threads = cpu_threads
        self.beam_size = beam_size
        self._whisper: Any = None
        self._device = ""
        self._compute = ""
        self._threads = 0
        self._lock = gpu.GpuLock()

    def load(self) -> None:
        if self._whisper is not None:
            return
        try:
            self._lock.acquire()
        except gpu.GpuBusy as exc:
            raise EngineBusy(str(exc)) from None
        try:
            from faster_whisper import WhisperModel  # optional and heavy: imported only when used

            repo = models.WHISPER_MODELS[self.model]
            try:
                models.fetch(repos=(repo,))
            except models.ModelFetchError as exc:
                raise EngineError(str(exc)) from None
            m = machine.detect()
            if m.device == "cuda":
                gpu.cuda_libraries()
                self._device, self._compute = "cuda", "float16"
            else:
                self._device, self._compute = "cpu", "int8"
                self._threads = self.cpu_threads or machine.cpu_threads(m)
            try:
                self._whisper = WhisperModel(
                    str(models.folder(repo)), device=self._device, compute_type=self._compute,
                    cpu_threads=self._threads, local_files_only=True,
                )  # fmt: skip
            except Exception as exc:  # CTranslate2 raises RuntimeError / ValueError; the user needs the message
                if _out_of_memory(exc):
                    raise EngineOutOfMemory(OUT_OF_MEMORY_MESSAGE) from exc
                print(f"whisper load failed: {exc!r}", file=sys.stderr, flush=True)
                raise EngineError(f"The Whisper model could not be loaded ({exc.__class__.__name__}).") from exc
        except BaseException:
            self._lock.release()
            raise

    def close(self) -> None:
        self._whisper = None
        self._device = self._compute = ""
        self._threads = 0
        self._lock.release()

    @property
    def on_cpu(self) -> bool:
        return self._device == "cpu"

    def describe(self) -> EngineInfo:
        options: dict[str, str | int | float] = {
            "language": self.language or "auto", "beam_size": self.beam_size, "compute_type": self._compute,
        }  # fmt: skip
        if self.on_cpu:
            options["cpu_threads"] = self._threads
        if self.prompt:
            options["prompt_chars"] = len(self.prompt)
        return EngineInfo(
            engine=self.name,
            model=models.WHISPER_MODELS[self.model].pinned,
            provider="CUDA" if self._device == "cuda" else "CPU",
            options=options,
            versions={"faster-whisper": _version("faster-whisper"), "ctranslate2": _version("ctranslate2")},
        )

    def transcribe(
        self,
        audio: npt.NDArray[np.float32],
        windows: Iterable[Window],
        on_progress: ProgressFn,
        cancel: CancelToken,
    ) -> list[TimedSegment]:
        if self._whisper is None:
            raise EngineError("Engine not loaded.")
        out: list[TimedSegment] = []
        for w in windows:
            cancel.raise_if_cancelled()
            try:
                out.append(TimedSegment(w, self._window_words(audio[w.start : w.end], w.start / SAMPLE_RATE)))
            except Exception as exc:
                if _out_of_memory(exc):
                    raise EngineOutOfMemory(OUT_OF_MEMORY_MESSAGE) from exc
                raise
            on_progress(w.end_s)
        return out

    def _window_words(self, chunk: npt.NDArray[np.float32], offset_s: float) -> list[TimedWord]:
        segments, _info = self._whisper.transcribe(
            chunk,
            language=self.language,
            task="transcribe",  # never translate, also when the language is detected
            beam_size=self.beam_size,
            word_timestamps=True,
            vad_filter=False,  # the windows are already speech (Silero VAD)
            condition_on_previous_text=False,  # windows are independent; avoids repeating a wrong line
            initial_prompt=self.prompt,
        )
        words: list[TimedWord] = []
        for seg in segments:  # a generator: recognition happens while iterating
            for word in seg.words or ():
                text = word.word.strip()
                if not text:
                    continue
                p = float(word.probability)
                conf = p if 0 <= p <= 1 else None
                start, end = offset_s + float(word.start), offset_s + float(word.end)
                if words and text[0] in _JOINERS:  # "Mahayana" "-Buddhisten": one word, as it is written
                    prev = words[-1]
                    confs = [c for c in (prev.conf, conf) if c is not None]
                    joined_conf = min(confs) if confs else None
                    words[-1] = TimedWord(prev.text + text, prev.start_s, max(prev.end_s, end), joined_conf)
                    continue
                words.append(TimedWord(text, start, end, conf))
        return collapse_repeats(words)


def collapse_repeats(words: list[TimedWord], max_run: int = MAX_SAME_WORD_RUN, longest: int = 3) -> list[TimedWord]:
    """At most `max_run` copies in a row of the same word or the same run of up to `longest` words ("ding",
    "ding dong"), compared without case and punctuation; the rest of such a loop is dropped."""
    keys = [w.text.strip(".,!?;:…\"'").lower() for w in words]
    same = [0] * (longest + 1)  # per period n: how many words in a row equal the word n places before them
    out: list[TimedWord] = []
    for i, w in enumerate(words):
        drop = False
        for n in range(1, longest + 1):
            same[n] = same[n] + 1 if i >= n and keys[i] and keys[i] == keys[i - n] else 0
            if same[n] > n * (max_run - 1):  # this word starts or continues copy number max_run + 1
                drop = True
        if not drop:
            out.append(w)
    return out


def _version(dist: str) -> str:
    try:
        return importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return "?"


def _out_of_memory(exc: BaseException) -> bool:
    text = str(exc)
    return any(mark in text for mark in _OUT_OF_MEMORY)
