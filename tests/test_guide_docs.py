"""The step-by-step guide lives in the window's string table and, for sharing, in docs/guide.md and docs/navod.md:
every title and step of the window must stand word for word, in the same order, in the matching file, and the file
has no topic of its own, so the two never drift apart."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STRINGS = (ROOT / "web" / "src" / "strings.ts").read_text(encoding="utf-8")


def guide_texts(nth: int) -> list[str]:
    """The topics' titles and steps of the n-th `guide: { ... }` block of the string table (0 = English, 1 = Czech).
    The block's own heading and intro belong to the page, not the file; the topics' ids are plain lower-case words."""
    starts = [m.start() for m in re.finditer(r"\n {4}guide: \{\n", STRINGS)]
    assert len(starts) == 2, "one guide in each language"
    block = STRINGS[starts[nth] : STRINGS.index("\n    },\n", starts[nth])]
    found = [json.loads(m.group(0)) for m in re.finditer(r'"(?:[^"\\]|\\.)*"', block)]
    return [t for t in found[2:] if not re.fullmatch(r"[a-z]+", t)]


@pytest.mark.parametrize(("nth", "doc"), [(0, "guide.md"), (1, "navod.md")])
def test_the_shared_guide_says_word_for_word_what_the_window_says(nth: int, doc: str) -> None:
    text = (ROOT / "docs" / doc).read_text(encoding="utf-8")
    texts = guide_texts(nth)
    assert len(texts) > 40  # the matcher found the guide, not nothing
    missing = [t for t in texts if t not in text]
    assert not missing, f"{doc} differs from the window's guide: {missing[:3]}"
    # each title as its heading, each step as a numbered line: their places must follow the window's order
    body = text[text.index("\n## ") :]
    places = []
    for t in texts:
        heading, step = f"\n## {t}\n", f". {t}\n"
        places.append(body.index(heading) if heading in body else body.index(step))
    assert places == sorted(places), f"{doc} has the topics or steps in another order than the window"
    headings = re.findall(r"\n## (.+)\n", body)
    assert all(h in texts for h in headings), f"{doc} has a topic the window does not have"
