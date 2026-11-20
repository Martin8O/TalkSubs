# Source before any raw tool call from Git Bash:  source scripts/env.sh
# Points every tool cache, model store and temp folder into the project so nothing lands on C:.
# Keep this list identical to scripts/env.ps1 and parakeet_stt/env.py (a test checks).
_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# pwd -W (Git Bash) gives E:/... which native Windows programs understand; plain pwd elsewhere
_root="$(cd "$_here/.." && { pwd -W 2>/dev/null || pwd; })"
_cache="$_root/.cache"
_models="$_root/models"

export PARAKEET_STT_ROOT="$_root"
export HF_HOME="$_cache/huggingface"
export HF_HUB_CACHE="$_models/hf"
export HF_XET_CACHE="$_cache/hf-xet"
export HF_HUB_DISABLE_TELEMETRY=1
export HF_HUB_DISABLE_SYMLINKS_WARNING=1
export TORCH_HOME="$_cache/torch"
export TORCHINDUCTOR_CACHE_DIR="$_cache/torchinductor"
export TRITON_CACHE_DIR="$_cache/triton"
export UV_CACHE_DIR="$_cache/uv"
export UV_PYTHON_INSTALL_DIR="$_cache/uv-python"
export UV_PYTHON_DOWNLOADS=never
export UV_TOOL_DIR="$_cache/uv-tools"
export PIP_CACHE_DIR="$_cache/pip"
export TEMP="$_cache/tmp"
export TMP="$_cache/tmp"
export TMPDIR="$_cache/tmp"
export XDG_CACHE_HOME="$_cache/xdg"
export CUDA_CACHE_PATH="$_cache/cuda"
export CUDA_CACHE_MAXSIZE=4294967296
export ORT_TENSORRT_CACHE_PATH="$_cache/ort-trt"
export ORT_TENSORRT_ENGINE_CACHE_PATH="$_cache/ort-trt"
export NUMBA_CACHE_DIR="$_cache/numba"
export MPLCONFIGDIR="$_cache/matplotlib"
export npm_config_cache="$_cache/npm"

for _d in "$HF_HOME" "$HF_HUB_CACHE" "$HF_XET_CACHE" "$TORCH_HOME" "$TORCHINDUCTOR_CACHE_DIR" "$TRITON_CACHE_DIR" \
          "$UV_CACHE_DIR" "$UV_PYTHON_INSTALL_DIR" "$UV_TOOL_DIR" "$PIP_CACHE_DIR" "$TEMP" "$XDG_CACHE_HOME" \
          "$CUDA_CACHE_PATH" "$ORT_TENSORRT_CACHE_PATH" "$NUMBA_CACHE_DIR" "$MPLCONFIGDIR" "$npm_config_cache"; do
  mkdir -p "$_d"
done
unset _here _root _cache _models _d
