"""The built page's one inline style (the waveform's shadow-root block) is exactly what the policy allows."""

from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

from parakeet_stt.server.security import WAVEFORM_STYLE_HASH

ASSETS = Path(__file__).resolve().parent.parent / "parakeet_stt" / "server" / "static" / "assets"
# `<style${nonce ? ... : ``}>...</style>` inside a template literal of the bundled waveform component
STYLE = re.compile(r"<style\$\{(?:[^{}]|\{[^{}]*\})*\}>(.*?)</style>", re.S)


def test_the_waveform_style_block_matches_the_policy_hash() -> None:
    blocks = [m.group(1) for f in sorted(ASSETS.glob("*.js")) for m in STYLE.finditer(f.read_text(encoding="utf-8"))]
    assert len(blocks) == 1, "expected the one style block of the waveform component in the built page"
    text = blocks[0].encode("ascii").decode("unicode_escape")  # as the browser holds it: escapes resolved
    digest = "sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode()
    assert digest == WAVEFORM_STYLE_HASH, f"the waveform style changed: allow {digest} in security.py"
