from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from parakeet_stt import env, presets


def test_both_presets_are_there_with_the_agreed_rules() -> None:
    assert presets.names() == ["cz-template", "de-template", "en-subtitles"]
    assert presets.DEFAULT in presets.names()
    cz, en = presets.get("cz-template"), presets.get("en-subtitles")
    for p in (cz, en):
        lim = p.limits
        assert (lim.max_chars_per_line, lim.max_lines, lim.max_duration_ms, lim.min_gap_frames) == (42, 2, 7000, 2)
    assert (cz.limits.max_cps, en.limits.max_cps) == (17.0, 20.0)
    # the two differ in reading speed (and their names) only
    a, b = cz.model_dump(), en.model_dump()
    for d in (a, b):
        d.pop("id"), d.pop("name"), d.pop("description")
        d["limits"].pop("max_cps")
    assert a == b


def _copy(tmp_path: Path) -> Path:
    shutil.copy(presets.PRESETS_DIR / "cz-template.toml", tmp_path / "cz-template.toml")
    return tmp_path / "cz-template.toml"


def test_a_typo_in_a_preset_names_the_file_and_the_key(tmp_path: Path) -> None:
    path = _copy(tmp_path)
    path.write_text(path.read_text(encoding="utf-8").replace("max_lines =", "max_line ="), encoding="utf-8")
    with pytest.raises(presets.PresetError, match=r"cz-template\.toml: limits\.max_line"):
        presets.load_preset(path)


def test_preset_id_must_equal_its_file_name(tmp_path: Path) -> None:
    path = _copy(tmp_path).rename(tmp_path / "other.toml")
    with pytest.raises(presets.PresetError, match="must equal the file name"):
        presets.load_preset(path)


def test_adding_a_preset_is_adding_a_file(tmp_path: Path) -> None:
    path = _copy(tmp_path)
    text = path.read_text(encoding="utf-8").replace('id = "cz-template"', 'id = "my-rules"')
    (tmp_path / "my-rules.toml").write_text(text.replace("max_cps = 17.0", "max_cps = 15.0"), encoding="utf-8")
    reg = presets.registry(tmp_path)
    assert set(reg) == {"cz-template", "my-rules"} and reg["my-rules"].limits.max_cps == 15.0


def test_unknown_preset_lists_the_ones_there_are() -> None:
    with pytest.raises(presets.PresetError, match="available: cz-template, de-template, en-subtitles"):
        presets.get("nope")


def test_inconsistent_limits_are_refused(tmp_path: Path) -> None:
    path = _copy(tmp_path)
    path.write_text(path.read_text(encoding="utf-8").replace("min_duration_ms = 1000", "min_duration_ms = 9000"),
                    encoding="utf-8")  # fmt: skip
    with pytest.raises(presets.PresetError, match="min_duration_ms"):
        presets.load_preset(path)


def test_digest_follows_every_rule_value() -> None:
    p = presets.get("cz-template")
    lang = presets.language("en")
    raw = p.model_dump()
    raw["segmenter"]["cost_plain"] = 11.0
    changed = presets.Preset.model_validate(raw)
    assert presets.digest(p, lang) != presets.digest(changed, lang)
    fewer = lang.model_copy(update={"no_break_after": lang.no_break_after - {"the"}})
    assert presets.digest(p, lang) != presets.digest(p, fewer)
    for name in ("fillers", "capital_always"):  # the cue words are tidied with these lists too
        other = lang.model_copy(update={name: getattr(lang, name) | {"zzz"}})
        assert presets.digest(p, lang) != presets.digest(p, other), name


def test_digest_is_the_same_in_every_process() -> None:
    # word lists are sets, whose order changes with the string hash seed of each run
    code = "from parakeet_stt import presets as p; print(p.digest(p.get('cz-template'), p.language('en')))"
    seen = set()
    for seed in ("1", "2", "3"):
        run_env = {**os.environ, "PYTHONHASHSEED": seed}
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=run_env, cwd=env.ROOT,
                             check=True, timeout=60)  # fmt: skip
        seen.add(out.stdout.strip())
    assert len(seen) == 1


def test_language_words_must_be_lower_case(tmp_path: Path) -> None:
    (tmp_path / "xx.toml").write_text('break_before = ["And"]\nno_break_after = []\nnot_names = []\n', encoding="utf-8")
    with pytest.raises(presets.PresetError, match="lower case"):
        presets.language("xx", tmp_path)
