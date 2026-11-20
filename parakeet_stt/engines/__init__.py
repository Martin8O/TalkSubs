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

DEFAULT = ParakeetEngine.name
register(ParakeetEngine.name, ParakeetEngine)

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
