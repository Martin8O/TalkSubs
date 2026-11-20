"""The generated API contract: the committed schema matches the models, and the freshness check sees drift."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from parakeet_stt.env import ROOT


def _gen() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gen_types", ROOT / "scripts" / "gen_types.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["gen_types"] = module
    spec.loader.exec_module(module)
    return module


def test_committed_api_schema_matches_the_models() -> None:
    gen = _gen()
    assert gen.SCHEMA_FILE.read_text(encoding="utf-8") == gen.schema_text(), (
        "API contract changed: run `python scripts/gen_types.py` and commit both generated files with the change"
    )


def test_freshness_check_sees_changed_content() -> None:
    gen = _gen()
    schema = gen.SCHEMA_FILE.read_text(encoding="utf-8")
    ts = gen.TS_FILE.read_text(encoding="utf-8")
    assert gen.stale_files(schema, ts) == []
    assert gen.stale_files(schema, ts + "export type Extra = 1;\n") == ["web\\src\\api\\contract.gen.ts"]
    assert gen.stale_files(schema.replace('"Job"', '"Jobb"', 1), ts) == ["schema\\api.schema.json"]


def test_every_server_message_is_in_the_contract() -> None:
    ts = (ROOT / "web" / "src" / "api" / "contract.gen.ts").read_text(encoding="utf-8")
    for name in ("ServerMessage", "SnapshotEvent", "JobEvent", "ProgressEvent", "PausedEvent", "Status", "Job",
                 "JobProgress", "AddJobRequest", "ErrorResponse", "Document", "SubtitleTrack", "Cue"):  # fmt: skip
        assert f"export interface {name} " in ts or f"export type {name} =" in ts, name
    assert Path(ROOT / "schema" / "api.schema.json").is_file()
