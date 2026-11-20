"""Environment doctor: judges the environment it INHERITED, then proves the GPU stack works.

Run through scripts/doctor.ps1 (or directly with the venv interpreter) from a shell that has
dot-sourced scripts/env.ps1 / sourced scripts/env.sh. It never sets variables itself, so a shell
that skipped the env script fails here instead of quietly writing caches to the system drive.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from parakeet_stt.env import VARS, Fixed  # noqa: E402 - the one list; importing it applies nothing

failures: list[str] = []


def ok(msg: str) -> None:
    print(f"OK    {msg}")


def warn(msg: str) -> None:
    print(f"WARN  {msg}")


def fail(msg: str) -> None:
    print(f"FAIL  {msg}")
    failures.append(msg)


def same_path(a: str, b: Path) -> bool:
    try:
        return Path(a).resolve() == b.resolve()
    except OSError:
        return False


def check_env() -> None:
    inherited = {k.upper(): v for k, v in os.environ.items()}  # as received; Windows names ignore case
    bad = []
    for name, want in VARS.items():
        val = inherited.get(name.upper())
        if val is None:
            bad.append(f"{name} missing")
        elif isinstance(want, Fixed):
            if val != want.value:
                bad.append(f"{name}={val!r} (want {want.value!r})")
        elif not same_path(val, ROOT / want.path):
            bad.append(f"{name}={val} (want {ROOT / want.path})")
    if bad:
        fail("inherited environment: " + "; ".join(bad) + "  -> run '. .\\scripts\\env.ps1' first")
    else:
        ok(f"inherited environment: all {len(VARS)} variables point into the project")


def check_python() -> None:
    venv = ROOT / ".venv"
    if same_path(sys.prefix, venv) and sys.version_info[:2] == (3, 11):
        ok(f"python {sys.version.split()[0]} from the project venv")
    else:
        fail(f"python {sys.version.split()[0]} at {sys.prefix} (want 3.11 from {venv})")


def check_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        exe = shutil.which(tool)
        if not exe:
            fail(f"{tool} not found on PATH")
            continue
        out = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=30, check=False)
        first = out.stdout.splitlines()[0] if out.stdout else "(no output)"
        if out.returncode == 0:
            ok(f"{tool}: {first[:70]}")
        else:
            fail(f"{tool} -version exited {out.returncode}")


def check_torch() -> None:
    try:
        import torch
    except Exception as exc:  # noqa: BLE001 - report any import failure verbatim
        fail(f"torch import: {exc!r}")
        return
    ver = torch.__version__
    if not ver.endswith("+cu128"):
        fail(f"torch {ver} is not the +cu128 build")
    if not torch.cuda.is_available():
        fail(f"torch {ver}: CUDA not available")
        return
    cap = torch.cuda.get_device_capability(0)
    a = torch.arange(16, dtype=torch.float32, device="cuda").reshape(4, 4)
    got = float((a @ a).sum().item())
    want = float((torch.arange(16.0).reshape(4, 4) @ torch.arange(16.0).reshape(4, 4)).sum().item())
    if got != want:
        fail(f"torch GPU matmul wrong: {got} != {want}")
    else:
        ok(f"torch {ver} CUDA {torch.version.cuda} on {torch.cuda.get_device_name(0)} sm_{cap[0]}{cap[1]}: kernel ran")
    free, total = torch.cuda.mem_get_info()
    msg = f"GPU memory free {free / 2**30:.1f} of {total / 2**30:.1f} GiB"
    (warn if free < 4 * 2**30 else ok)(msg + (" (other programs are using the GPU)" if free < 4 * 2**30 else ""))


def check_mode() -> str:
    """Where the app will run the speech model, and why; returns "cuda" or "cpu"."""
    from parakeet_stt import machine

    m = machine.detect()
    cores = f"{m.performance_cores} fast of {m.physical_cores} processor cores"
    if m.device == "cuda":
        ok(f"mode: graphics card ({m.card or 'NVIDIA, driver did not answer'}); {cores}")
        return "cuda"
    hidden = os.environ.get("CUDA_VISIBLE_DEVICES", "").strip() == "-1"
    why = f"{m.reason}, card hidden on purpose" if hidden else m.reason
    msg = f"mode: processor ({why}); {cores}, {machine.cpu_threads(m)} threads"
    # the graphics-card build without a usable card, and nobody hid it: on this PC that means a driver problem
    (ok if hidden or m.reason in ("no_card_support", "card_too_old", "card_too_small") else warn)(msg)
    return "cpu"


def check_onnxruntime(provider: str = "CUDAExecutionProvider") -> None:
    try:
        import numpy as np
        import onnx
        from onnx import TensorProto, helper

        from parakeet_stt import gpu

        ort = gpu.onnxruntime()  # the one place that loads torch first, then onnxruntime
    except Exception as exc:  # noqa: BLE001
        fail(f"onnxruntime import: {exc!r}")
        return
    ort.set_default_logger_severity(3)
    ort.preload_dlls()
    if provider not in ort.get_available_providers():
        fail(f"onnxruntime {ort.__version__}: {provider} not listed")
        return
    x = helper.make_tensor_value_info("x", TensorProto.FLOAT, [4, 4])
    y = helper.make_tensor_value_info("y", TensorProto.FLOAT, [4, 4])
    graph = helper.make_graph([helper.make_node("MatMul", ["x", "x"], ["y"])], "doctor", [x], [y])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 9
    opts = ort.SessionOptions()
    opts.log_severity_level = 3
    try:
        sess = ort.InferenceSession(model.SerializeToString(), opts, providers=[provider])
    except Exception as exc:  # noqa: BLE001
        fail(f"onnxruntime {provider} session: {exc!r}")
        return
    used = sess.get_providers()
    data = np.arange(16, dtype=np.float32).reshape(4, 4)
    out = sess.run(None, {"x": data})[0]
    if used[0] != provider:
        fail(f"onnxruntime fell back to {used[0]} (CUDA DLLs not loadable)")
    elif not np.allclose(out, data @ data):
        fail(f"onnxruntime {provider} result wrong")
    else:
        ok(f"onnxruntime {ort.__version__} (onnx {onnx.__version__}): {provider} ran a graph")


def check_disk() -> None:
    free_c = shutil.disk_usage("C:\\").free / 2**30
    free_p = shutil.disk_usage(ROOT).free / 2**30
    (warn if free_c < 10 else ok)(f"C: free {free_c:.1f} GiB (nothing of ours goes there)")
    (fail if free_p < 20 else ok)(f"project drive free {free_p:.1f} GiB")


def main() -> int:
    check_env()
    check_python()
    check_ffmpeg()
    if failures:
        # Touching the GPU with a wrong environment writes the CUDA kernel cache to the system drive,
        # so the GPU checks only run once the environment is right.
        print("SKIP  GPU checks (fix the environment first)")
    elif check_mode() == "cuda":
        check_torch()
        check_onnxruntime()
    else:
        print("SKIP  graphics-card checks (processor mode)")
        check_onnxruntime("CPUExecutionProvider")
    check_disk()
    if failures:
        print(f"DOCTOR: RED ({len(failures)} failure(s))")
        return 1
    print("DOCTOR: GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
