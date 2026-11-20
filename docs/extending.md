# Extending

## Add a subtitle preset
Copy a file in `presets/`, give it a new `id` equal to its file name, and change the values (limits, timing,
segmenter weights, severities). No code change is needed. A new *kind* of setting needs a field in
`parakeet_stt/presets.py` with a default, because saved projects keep a copy of their preset.

## Add a speech recognition engine
Implement the engine protocol in `parakeet_stt/engines/` (audio window in → timed words out, progress callback),
register it in the engine registry, and measure it on your own evaluation recordings (`data/eval/`, not
published) before making it a default.

## Add an export format
Add a module to `parakeet_stt/export/` with `render(document, track) -> bytes`, register it in `FORMATS`, and
add a golden-file test plus a round-trip test (export → parse back with an independent reader → same cues).
