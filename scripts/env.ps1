# Dot-source before any raw tool call:  . .\scripts\env.ps1
# Points every tool cache, model store and temp folder into the project so nothing lands on C:.
# Keep this list identical to scripts/env.sh and parakeet_stt/env.py (a test checks).
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$cache = Join-Path $root '.cache'
$models = Join-Path $root 'models'

$vars = [ordered]@{
  PARAKEET_STT_ROOT        = $root
  HF_HOME                  = Join-Path $cache 'huggingface'
  HF_HUB_CACHE             = Join-Path $models 'hf'
  HF_XET_CACHE             = Join-Path $cache 'hf-xet'
  HF_HUB_DISABLE_TELEMETRY = '1'
  HF_HUB_DISABLE_SYMLINKS_WARNING = '1'
  TORCH_HOME               = Join-Path $cache 'torch'
  TORCHINDUCTOR_CACHE_DIR  = Join-Path $cache 'torchinductor'
  TRITON_CACHE_DIR         = Join-Path $cache 'triton'
  UV_CACHE_DIR             = Join-Path $cache 'uv'
  UV_PYTHON_INSTALL_DIR    = Join-Path $cache 'uv-python'
  UV_PYTHON_DOWNLOADS      = 'never'
  UV_TOOL_DIR              = Join-Path $cache 'uv-tools'
  PIP_CACHE_DIR            = Join-Path $cache 'pip'
  TEMP                     = Join-Path $cache 'tmp'
  TMP                      = Join-Path $cache 'tmp'
  TMPDIR                   = Join-Path $cache 'tmp'
  XDG_CACHE_HOME           = Join-Path $cache 'xdg'
  CUDA_CACHE_PATH          = Join-Path $cache 'cuda'
  CUDA_CACHE_MAXSIZE       = '4294967296'
  ORT_TENSORRT_CACHE_PATH  = Join-Path $cache 'ort-trt'
  ORT_TENSORRT_ENGINE_CACHE_PATH = Join-Path $cache 'ort-trt'
  NUMBA_CACHE_DIR          = Join-Path $cache 'numba'
  MPLCONFIGDIR             = Join-Path $cache 'matplotlib'
  npm_config_cache         = Join-Path $cache 'npm'
}
foreach ($k in $vars.Keys) {
  Set-Item -Path "Env:$k" -Value $vars[$k]
  if ($vars[$k] -like "$root*" -and -not (Test-Path -LiteralPath $vars[$k])) {
    New-Item -ItemType Directory -Force -Path $vars[$k] | Out-Null
  }
}
