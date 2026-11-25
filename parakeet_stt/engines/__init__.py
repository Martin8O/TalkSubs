"""Recognition engines behind one protocol; `create(name)` picks one."""

from parakeet_stt.engines.base import (
    Engine,
    EngineError,
    EngineInfo,
    EngineUnavailable,
    TimedSegment,
    TimedWord,
    create,
    names,
    register,
)
from parakeet_stt.engines.parakeet import ParakeetEngine
from parakeet_stt.engines.whisper import WhisperEngine
from parakeet_stt.engines.whisper import available as whisper_available

DEFAULT = ParakeetEngine.name
register(ParakeetEngine.name, ParakeetEngine)
if whisper_available():  # optional: offered only where faster-whisper is installed (docs/adr.md 0040)
    register(WhisperEngine.name, WhisperEngine)

__all__ = [
    "DEFAULT",
    "Engine",
    "EngineError",
    "EngineInfo",
    "EngineUnavailable",
    "TimedSegment",
    "TimedWord",
    "create",
    "names",
    "register",
]
