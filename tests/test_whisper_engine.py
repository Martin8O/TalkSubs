"""The Whisper engine: options, the word list as prompt, and how Whisper's words become timed words."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pytest

from parakeet_stt import engines
from parakeet_stt.cancel import CancelToken
from parakeet_stt.engines import whisper
from parakeet_stt.engines.base import EngineError, EngineOutOfMemory
from parakeet_stt.vad import Window

SR = 16_000


@dataclass
class _Word:
    word: str
    start: float
    end: float
    probability: float


@dataclass
class _Segment:
    words: list[_Word]


@dataclass
class _FakeWhisper:
    """Stands in for faster_whisper.WhisperModel: records the calls, returns fixed words per window."""

    per_call: list[list[_Word]]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def transcribe(self, audio: Any, **kw: Any) -> tuple[Any, Any]:
        self.calls.append({"samples": len(audio), **kw})
        words = self.per_call[len(self.calls) - 1]
        return iter([_Segment(words)]), None


def _engine(fake: _FakeWhisper, **options: Any) -> whisper.WhisperEngine:
    eng = whisper.WhisperEngine(**options)
    eng._whisper = fake  # loaded, without a model on disk
    eng._device, eng._compute = "cpu", "int8"
    return eng


def test_languages_are_normalised_and_auto_means_detect() -> None:
    assert whisper.normalise_language("de") == "de"
    assert whisper.normalise_language("DE-at") == "de"
    assert whisper.normalise_language("cs_CZ") == "cs"
    for detect in (None, "", "auto", " AUTO "):
        assert whisper.normalise_language(detect) is None
    with pytest.raises(EngineError, match="does not offer the language"):
        whisper.normalise_language("tlh")


def test_the_word_list_becomes_a_short_prompt_each_term_once() -> None:
    assert whisper.prompt_from_terms([]) is None
    assert whisper.prompt_from_terms(["Karmapa", " Bardo ", "Karmapa", ""]) == "Karmapa, Bardo"
    long = whisper.prompt_from_terms([f"Term{i:04d}" for i in range(500)])
    assert long is not None and len(long) <= whisper.MAX_PROMPT_CHARS


def test_words_get_absolute_times_and_whisper_is_told_the_language() -> None:
    fake = _FakeWhisper([
        [_Word(" Das", 0.10, 0.30, 0.9), _Word(" ist", 0.32, 0.50, 0.8), _Word("  ", 0.5, 0.5, 0.1)],
        [_Word(" Karmapa.", 1.00, 1.60, 1.2)],  # an out-of-range probability is not kept
    ])  # fmt: skip
    eng = _engine(fake, language="de", prompt="Karmapa, Bardo")
    audio = np.zeros(SR * 20, dtype=np.float32)
    windows = [Window(SR * 2, SR * 6, SR * 2, SR * 6), Window(SR * 10, SR * 15, SR * 10, SR * 15)]
    progress: list[float] = []
    segs = eng.transcribe(audio, windows, progress.append, CancelToken())
    first, second = segs[0].words, segs[1].words
    assert [w.text for w in first] == ["Das", "ist"]  # empty words dropped, spaces stripped
    assert first[0].start_s == pytest.approx(2.10) and first[1].end_s == pytest.approx(2.50)
    assert second[0].start_s == pytest.approx(11.0) and second[0].conf is None
    assert progress == [6.0, 15.0]
    call = fake.calls[0]
    assert call["samples"] == SR * 4
    assert call["language"] == "de" and call["task"] == "transcribe"
    assert call["word_timestamps"] is True and call["vad_filter"] is False
    assert call["initial_prompt"] == "Karmapa, Bardo"


def test_a_hyphenated_word_split_by_whisper_is_one_word_again() -> None:
    fake = _FakeWhisper(
        [[_Word(" Mahayana", 0.0, 0.4, 0.9), _Word("-Buddhisten", 0.4, 0.9, 0.7), _Word(" seid", 1.0, 1.2, 0.8)]]
    )
    eng = _engine(fake)
    seg = eng.transcribe(
        np.zeros(SR * 2, dtype=np.float32), [Window(0, SR * 2, 0, SR * 2)], lambda _s: None, CancelToken()
    )
    words = seg[0].words
    assert [w.text for w in words] == ["Mahayana-Buddhisten", "seid"]
    assert words[0].end_s == pytest.approx(0.9) and words[0].conf == pytest.approx(0.7)


def test_without_a_language_whisper_detects_it() -> None:
    fake = _FakeWhisper([[_Word(" Hola", 0.0, 0.3, 0.9)]])
    eng = _engine(fake)
    eng.transcribe(np.zeros(SR * 2, dtype=np.float32), [Window(0, SR, 0, SR)], lambda _s: None, CancelToken())
    assert fake.calls[0]["language"] is None and fake.calls[0]["initial_prompt"] is None


def test_a_memory_shortage_on_the_card_is_reported_as_such() -> None:
    class _Oom:
        def transcribe(self, *_a: Any, **_k: Any) -> Any:
            raise RuntimeError("CUDA failed with error out of memory")

    eng = whisper.WhisperEngine()
    eng._whisper, eng._device = _Oom(), "cuda"
    with pytest.raises(EngineOutOfMemory):
        eng.transcribe(np.zeros(SR, dtype=np.float32), [Window(0, SR, 0, SR)], lambda _s: None, CancelToken())


def test_describe_names_the_pinned_model_and_the_options() -> None:
    eng = _engine(_FakeWhisper([]), language="pl", prompt="Gampopa")
    info = eng.describe()
    assert info.engine == "whisper"
    assert info.model.startswith("Systran/faster-whisper-large-v3@")
    assert info.options["language"] == "pl" and info.options["prompt_chars"] == len("Gampopa")


def test_unknown_model_and_options_of_other_engines() -> None:
    with pytest.raises(EngineError, match="Unknown Whisper model"):
        whisper.WhisperEngine(model="huge")
    whisper.WhisperEngine(batch_size=2)  # Parakeet's option is accepted and ignored


def test_the_engine_is_offered_only_when_faster_whisper_is_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    assert ("whisper" in engines.names()) == whisper.available()
    monkeypatch.setattr("importlib.util.find_spec", lambda _name: None)
    assert whisper.available() is False


def test_cli_passes_language_model_and_word_list_to_whisper_only() -> None:
    from parakeet_stt import cli, glossary

    gl = glossary.builtin().with_user(glossary.parse_text("Karmapa = Kamapa\nGampopa\n"))
    args = cli._parser().parse_args(
        ["transcribe", "x.mp3", "-o", "out", "--engine", "whisper", "--language", "cs", "--whisper-model", "tiny"]
    )
    opts = cli._engine_options(args, gl)
    assert opts["model"] == "tiny" and opts["language"] == "cs"
    assert "Karmapa" in str(opts["prompt"]) and "Gampopa" in str(opts["prompt"])
    parakeet = cli._parser().parse_args(["transcribe", "x.mp3", "-o", "out"])
    assert cli._engine_options(parakeet, gl) == {"batch_size": 1, "model": "v2"}
    with pytest.raises(cli._UsageError, match="whisper only"):
        cli._engine_options(cli._parser().parse_args(["transcribe", "x.mp3", "-o", "out", "--language", "de"]), gl)


def test_the_worker_gives_whisper_the_language_and_the_users_terms() -> None:
    from parakeet_stt import machine
    from parakeet_stt.server.worker import JobSpec, _engine_options

    cpu = machine.Machine("cpu", "no_card", None, None, 8, 8)

    def spec(engine: str, glossary_text: str = "Karmapa = Kamapa\n", **more: Any) -> JobSpec:
        return JobSpec(source="a.mp3", out_dir="o", stem="a", data_dir="d", engine=engine,
                       glossary_text=glossary_text, **more)  # fmt: skip

    opts = _engine_options(spec("whisper", speech_language="de", cpu_threads=4), cpu)
    assert opts == {"cpu_threads": 4, "language": "de", "prompt": "Karmapa"}
    assert _engine_options(spec("parakeet"), cpu) is None
    broken = spec("whisper", glossary_text="= nothing")
    assert _engine_options(broken, cpu) == {"language": None, "prompt": None}  # an unusable list: no prompt


def test_settings_accept_known_spoken_languages_only() -> None:
    from parakeet_stt.server.messages import Settings
    from parakeet_stt.server.settings import SettingsError, check

    check(Settings(speech_language="auto"))
    check(Settings(speech_language="pl"))
    with pytest.raises(SettingsError, match="spoken language"):
        check(Settings(speech_language="xx"))
