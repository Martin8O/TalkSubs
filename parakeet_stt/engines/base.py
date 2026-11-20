"""What every recognition engine provides, and the registry that names them."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np
import numpy.typing as npt

from parakeet_stt.cancel import CancelToken
from parakeet_stt.vad import Window


class EngineError(Exception):
    """The engine cannot run; the message is for the user."""


class EngineBusy(EngineError):
    """Another job on this machine holds the GPU; the same request can succeed once it is released."""


class EngineOutOfMemory(EngineError):
    """The graphics card had no memory left (usually another program holds it); a later try can succeed."""


class EngineUnavailable(EngineError):
    """The engine is known but cannot be used in this installation."""


@dataclass(frozen=True)
class TimedWord:
    """Absolute media times in seconds, as the engine heard them (not yet clamped to the window it belongs to)."""

    text: str
    start_s: float
    end_s: float
    conf: float | None


@dataclass(frozen=True)
class TimedSegment:
    """The words recognised in one speech window, in time order."""

    window: Window
    words: list[TimedWord]


@dataclass(frozen=True)
class EngineInfo:
    engine: str
    model: str
    provider: str  # the execution provider actually running the model, e.g. CUDAExecutionProvider
    options: dict[str, str | int | float] = field(default_factory=dict)
    versions: dict[str, str] = field(default_factory=dict)


ProgressFn = Callable[[float], None]  # absolute media position (seconds) up to which all audio is recognised


class Engine(Protocol):
    name: str

    def load(self) -> None:
        """Load the model (may download it once). Raises EngineError / EngineUnavailable."""

    def describe(self) -> EngineInfo:
        """What actually runs; valid after load()."""

    def close(self) -> None:
        """Release the model and its GPU memory (and the GPU lock). Safe to call twice."""

    def transcribe(
        self,
        audio: npt.NDArray[np.float32],
        windows: Iterable[Window],
        on_progress: ProgressFn,
        cancel: CancelToken,
    ) -> list[TimedSegment]:
        """Recognise each window; report progress after each batch; stop with Cancelled when asked."""


EngineFactory = Callable[..., Engine]
_REGISTRY: dict[str, EngineFactory] = {}


def register(name: str, factory: EngineFactory) -> None:
    _REGISTRY[name] = factory


def names() -> list[str]:
    return sorted(_REGISTRY)


def create(name: str, **options: object) -> Engine:
    try:
        factory = _REGISTRY[name]
    except KeyError:
        raise EngineError(f"Unknown engine {name!r}. Available: {', '.join(names())}.") from None
    return factory(**options)
