# syntax=docker/dockerfile:1
# Headless TalkSubs CLI for batch jobs: audio in, subtitles/transcript out, the speech model baked in.
#
#   CPU:   docker build -t talksubs:cpu .
#   CUDA:  docker build -t talksubs:cuda \
#            --build-arg BASE=nvidia/cuda:12.6.3-cudnn-runtime-ubuntu22.04 \
#            --build-arg ORT=onnxruntime-gpu==1.23.2 .
#   Run:   docker run --rm [--gpus all] -v "$PWD:/data" talksubs:cpu \
#            transcribe /data/talk.mp3 -o /data/out --model v3 --glossary /data/terms.txt --format srt,vtt,txt
#
# MODELS: which speech models to bake in (space separated keys of models.SPEECH_MODELS); only these work offline.
#   v2 = English only (cannot flip languages), v3 = 25 languages (detects the language itself, can flip mid-sentence).
#   whisper-large-v3 / whisper-tiny = the Whisper engine's model (else fetched on first use, 3 GB for large-v3).
ARG BASE=debian:trixie-slim
FROM ${BASE}

ARG ORT=onnxruntime==1.23.2
ARG MODELS="v2 v3"
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=automatic \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.12.16 /uv /usr/local/bin/uv

# Dependencies of the command line only (no torch: the CUDA base image brings CUDA and cuDNN; no desktop server).
# Versions match pyproject.toml.
RUN uv venv --python 3.11 /opt/venv \
 && uv pip install --python /opt/venv/bin/python \
      "${ORT}" "onnx-asr[hub]==0.12.0" "numpy>=2.2" "pydantic>=2.9,<3" "pysubs2==1.8.1" \
      "openpyxl>=3.1.5" "cryptography==50.0.2" \
 # the Whisper engine without the CPU onnxruntime it asks for (it would replace onnxruntime-gpu; see pyproject.toml)
 && uv pip install --python /opt/venv/bin/python --no-deps "faster-whisper==1.2.1" \
 && uv pip install --python /opt/venv/bin/python "ctranslate2>=4,<5" "tokenizers>=0.13,<1" "av>=11" "tqdm"

WORKDIR /app
COPY pyproject.toml README.md LICENSE THIRD-PARTY-NOTICES.txt ./
COPY presets ./presets
COPY schema ./schema
COPY parakeet_stt ./parakeet_stt

# The pinned model files (SHA-256 checked) go into /app/models/hf, where the app looks (env.py).
RUN python -c "from parakeet_stt import env; env.apply(); from parakeet_stt import models; \
repos = [models.SPEECH_MODELS[m][0] if m in models.SPEECH_MODELS else models.WHISPER_MODELS[m.removeprefix('whisper-')] \
         for m in '${MODELS}'.split()] + [models.VAD]; \
models.fetch(repos=repos)" \
 && chmod -R a+rX /app && chmod -R a+rwX /app/.cache

WORKDIR /data
ENV PYTHONPATH=/app HF_HUB_OFFLINE=1
ENTRYPOINT ["python", "-m", "parakeet_stt"]
CMD ["--help"]
