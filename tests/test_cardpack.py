"""The download package's graphics-card support: downloaded on request, only what the app uses unpacked, counted only
once whole and of these pins, then put first on the module path."""

from __future__ import annotations

import hashlib
import io
import sys
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from parakeet_stt import cardpack, models

PLENTY = 10**12


def wheel(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


ORT = wheel({
    "onnxruntime/__init__.py": b"# the card one\n",
    "onnxruntime/capi/onnxruntime_providers_cuda.dll": b"cuda provider",
    "onnxruntime/capi/onnxruntime_providers_tensorrt.dll": b"never used",
    "onnxruntime_gpu-1.23.2.dist-info/METADATA": b"Name: onnxruntime-gpu",
    "onnxruntime_gpu-1.23.2.dist-info/RECORD": b"lists files left out",
})  # fmt: skip
CUDNN = wheel({
    "nvidia/cudnn/bin/cudnn64_9.dll": b"cudnn",
    "nvidia/cudnn/include/cudnn.h": b"a header",
    "nvidia/cudnn/bin/something_else.dll": b"not loaded by the app",
    "nvidia_cudnn_cu12-9.19.0.56.dist-info/licenses/License.txt": b"NVIDIA licence",
    "../outside.txt": b"must never be written",
})  # fmt: skip
FILES = {"https://x/ort.whl": ORT, "https://x/cudnn.whl": CUDNN}


@pytest.fixture
def pack(monkeypatch: pytest.MonkeyPatch) -> None:
    wheels = tuple(cardpack.Wheel(u, models.Pin(len(d), hashlib.sha256(d).hexdigest())) for u, d in FILES.items())
    monkeypatch.setattr(cardpack, "WHEELS", wheels)


class Response:
    def __init__(self, data: bytes, status: int) -> None:
        self._buf, self.status = io.BytesIO(data), status

    def read(self, n: int, /) -> bytes:
        return self._buf.read(n)


@contextmanager
def site(url: str, have: int) -> Iterator[Response]:
    yield Response(FILES[url][have:], 206 if have else 200)


def test_the_pack_is_downloaded_unpacked_and_put_first(
    tmp_path: Path, pack: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[int, int]] = []
    assert not cardpack.present(tmp_path)
    cardpack.fetch(lambda d, t: seen.append((d, t)), root=tmp_path, opener=site, free_space=lambda _: PLENTY)
    where = cardpack.site(tmp_path)
    assert cardpack.present(tmp_path) and seen[-1] == (len(ORT) + len(CUDNN),) * 2
    files = {p.relative_to(where).as_posix() for p in where.rglob("*") if p.is_file()}
    assert files == {
        "READY.json",
        "onnxruntime/__init__.py",
        "onnxruntime/capi/onnxruntime_providers_cuda.dll",
        "onnxruntime_gpu-1.23.2.dist-info/METADATA",
        "nvidia/cudnn/bin/cudnn64_9.dll",
        "nvidia_cudnn_cu12-9.19.0.56.dist-info/licenses/License.txt",
    }  # no TensorRT, no RECORD, no header, no DLL the app does not load, nothing outside
    assert not (tmp_path / "card" / "outside.txt").exists() and not (tmp_path / "outside.txt").exists()
    assert not (tmp_path / "card" / "downloads").exists()
    monkeypatch.setattr(sys, "path", ["python311.zip", "Lib", r"Lib\site-packages"])
    assert cardpack.activate(tmp_path) and sys.path == ["python311.zip", "Lib", str(where), r"Lib\site-packages"]
    (where / "nvidia" / "cudnn" / "bin" / "cudnn64_9.dll").write_bytes(b"")  # emptied later (a power cut, a scanner)
    assert not cardpack.present(tmp_path)  # every file is checked at its size


def test_a_half_unpacked_or_other_pack_never_counts(tmp_path: Path, pack: None) -> None:
    where = cardpack.site(tmp_path)
    (where / "onnxruntime").mkdir(parents=True)
    assert not cardpack.present(tmp_path) and not cardpack.activate(tmp_path)  # no marker: not whole
    (where / "READY.json").write_text('{"pins": "another version", "files": {"x": 1}}', encoding="utf-8")
    assert not cardpack.present(tmp_path)


def test_a_left_wheel_of_the_right_size_but_other_content_is_refused(tmp_path: Path, pack: None) -> None:
    downloads = tmp_path / "card" / "downloads"
    downloads.mkdir(parents=True)
    swapped = bytearray(CUDNN)
    swapped[-1] ^= 0xFF  # same size, other bytes (as if put there between two runs)
    (downloads / "cudnn.whl").write_bytes(bytes(swapped))
    with pytest.raises(models.ModelFetchError) as caught:
        cardpack.fetch(root=tmp_path, opener=site, free_space=lambda _: PLENTY)
    assert caught.value.kind == "damaged" and not cardpack.present(tmp_path)
    assert not (cardpack.site(tmp_path) / "nvidia").exists()  # nothing of it was unpacked
    cardpack.fetch(root=tmp_path, opener=site, free_space=lambda _: PLENTY)  # the next attempt downloads it afresh
    assert cardpack.present(tmp_path)


def test_too_little_disk_space_is_said_before_anything_downloads(tmp_path: Path, pack: None) -> None:
    with pytest.raises(models.ModelFetchError) as caught:
        cardpack.fetch(root=tmp_path, opener=site, free_space=lambda _: 10**9)
    assert caught.value.kind == "no_space" and "graphics-card support" in str(caught.value)
    assert not list((tmp_path / "card" / "downloads").glob("*"))


def test_the_pins_name_the_files_the_app_loads() -> None:
    from parakeet_stt.gpu import NVIDIA_DLLS

    libs = {lib for lib, _ in NVIDIA_DLLS}
    names = " ".join(w.name for w in cardpack.WHEELS)
    assert all(lib in names for lib in libs)  # every library the app loads comes from one of the pinned wheels
    assert cardpack.WHEELS[0].name.startswith("onnxruntime_gpu-1.23.2-cp311")
    assert all(w.url.startswith("https://files.pythonhosted.org/") for w in cardpack.WHEELS)
