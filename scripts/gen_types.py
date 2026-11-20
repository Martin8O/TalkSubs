"""The API contract, generated: `schema/api.schema.json` from the pydantic models, then the TypeScript types.

    python scripts/gen_types.py           write both files
    python scripts/gen_types.py --check   exit 1 if either is stale, or the generated folder holds anything else

Server-to-window shapes use the serialization view (every field is always present); requests use the
validation view (fields with defaults may be left out).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pydantic.json_schema import GenerateJsonSchema, JsonSchemaMode, models_json_schema
from pydantic_core import core_schema

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from parakeet_stt import env  # noqa: E402 - the project root must be importable first

env.apply()

from parakeet_stt.server import messages as m  # noqa: E402
from parakeet_stt.transcript import Document  # noqa: E402

SCHEMA_FILE = ROOT / "schema" / "api.schema.json"
TS_DIR = ROOT / "web" / "src" / "api"
TS_FILE = TS_DIR / "contract.gen.ts"
NODE_SCRIPT = ROOT / "web" / "scripts" / "gen-types.mjs"

OUTGOING: list[type[BaseModel]] = [
    m.ServerMessage,
    m.Status,
    m.Job,
    m.ErrorResponse,
    m.SettingsView,
    m.ExportResult,
    m.ExportConflict,
    m.DialogResult,
    m.TrackView,
    m.WordsView,
    m.CheckResult,
    m.TrackSaved,
    Document,
]  # Settings goes out inside SettingsView and comes back whole in PUT /api/settings
INCOMING: list[type[BaseModel]] = [
    m.AddJobRequest, m.AddLinkRequest, m.ExportRequest, m.DialogRequest, m.CheckRequest, m.SaveTrackRequest,
    m.ResegmentRequest,
]  # fmt: skip


class _AlwaysPresent(GenerateJsonSchema):
    """What the server sends always carries every field, defaults included: mark them required."""

    def field_is_required(
        self,
        field: core_schema.ModelField | core_schema.DataclassField | core_schema.TypedDictField,
        total: bool,
    ) -> bool:
        if self.mode == "serialization":
            return True
        return super().field_is_required(field, total)


def _strip_titles(node: Any, top: bool = True) -> Any:
    """Property titles would each become a named alias type; only the definitions keep their names."""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k == "title" and not top and isinstance(v, str):
                continue
            if k == "$defs":
                out[k] = {name: _strip_titles(d, top=True) for name, d in v.items()}
            elif k == "properties":
                out[k] = {name: _strip_titles(p, top=False) for name, p in v.items()}
            else:
                out[k] = _strip_titles(v, top=False)
        return out
    if isinstance(node, list):
        return [_strip_titles(x, top=False) for x in node]
    return node


def api_schema() -> dict[str, Any]:
    pairs: list[tuple[type[BaseModel], JsonSchemaMode]] = [(t, "serialization") for t in OUTGOING]
    pairs += [(t, "validation") for t in INCOMING]
    _, schema = models_json_schema(pairs, schema_generator=_AlwaysPresent)
    full = {"title": "ApiContract", "description": "Every shape the server and the window exchange.",
            "type": "object", "additionalProperties": False, **schema}  # fmt: skip
    return dict(_strip_titles(full))


def schema_text() -> str:
    return json.dumps(api_schema(), indent=1, ensure_ascii=False, sort_keys=False) + "\n"


def ts_text(schema_json: str) -> str:
    if not (ROOT / "web" / "node_modules" / "json-schema-to-typescript").is_dir():
        raise SystemExit("the type generator is not installed: run `npm ci` in web/ (with scripts/env.ps1 loaded)")
    try:
        out = subprocess.run(
            ["node", str(NODE_SCRIPT)], input=schema_json.encode("utf-8"), capture_output=True,
            cwd=ROOT / "web", timeout=120, check=False,
        )  # fmt: skip
    except FileNotFoundError:
        raise SystemExit("Node.js is not installed or not on PATH (needed to generate the TypeScript types)") from None
    if out.returncode != 0:
        raise SystemExit(f"type generator failed:\n{out.stderr.decode('utf-8', 'replace')}")
    return out.stdout.decode("utf-8").replace("\r\n", "\n")


def stale_files(schema: str, ts: str) -> list[str]:
    """Generated files whose committed content differs from what the models produce, or that nothing produces."""
    stale = []
    if not SCHEMA_FILE.is_file() or SCHEMA_FILE.read_text(encoding="utf-8") != schema:
        stale.append(str(SCHEMA_FILE.relative_to(ROOT)))
    if not TS_FILE.is_file() or TS_FILE.read_text(encoding="utf-8") != ts:
        stale.append(str(TS_FILE.relative_to(ROOT)))
    extra = sorted(p for p in TS_DIR.glob("*.gen.ts") if p != TS_FILE) if TS_DIR.is_dir() else []
    return stale + [f"{p.relative_to(ROOT)} (not produced by the generator)" for p in extra]


def main(argv: list[str]) -> int:
    check = "--check" in argv
    schema = schema_text()
    ts = ts_text(schema)
    if not check:
        SCHEMA_FILE.write_text(schema, encoding="utf-8", newline="\n")
        TS_DIR.mkdir(parents=True, exist_ok=True)
        TS_FILE.write_text(ts, encoding="utf-8", newline="\n")
        print(f"wrote {SCHEMA_FILE.relative_to(ROOT)} and {TS_FILE.relative_to(ROOT)}")
        return 0
    stale = stale_files(schema, ts)
    if stale:
        print("stale generated contract: " + ", ".join(stale) + " - run `python scripts/gen_types.py` and commit")
        return 1
    print("generated contract is fresh")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
