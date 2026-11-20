"""The glossary: wrong forms of terms and names put right in the subtitle text, the recognised words untouched."""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from parakeet_stt import glossary, presets, project, subtitles
from parakeet_stt.glossary import Entry
from parakeet_stt.segment import build_track
from parakeet_stt.transcript import Document, Segment, Word
from tests import factories
from tests.conftest import MakeClip, key_of
from tests.test_cli_export import _project
from tests.test_server import ORIGIN, add, make_client, state_of, wait


def words(text: str, step: int = 500) -> list[Word]:
    return [Word(text=t, start_ms=i * step, end_ms=i * step + 400, conf=0.9) for i, t in enumerate(text.split())]


def texts(ws: list[Word]) -> str:
    return " ".join(w.text for w in ws)


SAMPLE = """
[[term]]
write = "Nietzsche"
wrong = ["Neechee", "Neechy", "Nichee"]

[[term]]
write = "Marie Curie"
wrong = ["Mary Query"]

[[term]]
write = "Las Vegas"
wrong = ["Lasvegas"]

[[term]]
write = "Kyoto"
wrong = ["Kyo To"]

[[term]]
write = "Schrödinger"
also = ["Schrodinger"]
wrong = ["shredinger"]

[[term]]
write = "qubit"
wrong = ["Kubit"]

[[term]]
write = "Bath"
wrong = ["Baath"]
fix_case = false  # "bath" is an ordinary English word too

[[term]]
write = "Dostoevsky"
wrong = ["Dostoyefsky"]
"""


def _sample() -> glossary.Glossary:
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "sample.toml"
        path.write_text(SAMPLE, encoding="utf-8")
        return glossary.builtin(path)


GL = _sample()  # a list as one field would have it, in the built-in file's format


def test_the_app_comes_with_no_terms_and_the_file_format_loads() -> None:
    assert glossary.builtin().entries == ()  # a general tool: every user lists the names of their own field
    assert glossary.builtin().apply(words("the Neechee came")) == words("the Neechee came")
    assert {"Nietzsche", "Marie Curie", "Schrödinger", "Bath"} <= {e.term for e in GL.entries}
    assert len(GL.digest()) == 16 and GL.digest() != glossary.builtin().digest()


@pytest.mark.parametrize(
    ("said", "written"),
    [
        ("the Neechee came", "the Nietzsche came"),
        ("two Neechees, and", "two Nietzsches, and"),  # a plural
        ("the Neechee's teacher", "the Nietzsche's teacher"),  # a possessive
        ("Nichees.", "Nietzsches."),
        ("by Mary Query, in", "by Marie Curie, in"),  # two words for two
        ("the Lasvegas trip", "the Las Vegas trip"),  # one word for two
        ("we read shredinger now", "we read Schrödinger now"),
        ("my Kubit state", "my qubit state"),  # a lower-case term stays small inside a sentence ...
        ("Kubit state", "Qubit state"),  # ... and gets a capital where a sentence starts
        ("done. Kubit state", "done. Qubit state"),
        ("the nietzsche said", "the Nietzsche said"),  # a name written small gets its capital
        ("the schrodinger", "the Schrodinger"),  # so does another right spelling
        ("a hot bath", "a hot bath"),  # an ordinary word that is also a term: left alone
        ("(Dostoyefsky)", "(Dostoevsky)"),  # punctuation around it kept
        ("Nietzsche and Dostoevsky", "Nietzsche and Dostoevsky"),  # already right
        ("they said Mary. Query came", "they said Mary. Query came"),  # not across a sentence end
    ],
)
def test_wrong_forms_are_put_right(said: str, written: str) -> None:
    assert texts(GL.apply(words(said))) == written


def test_times_stay_inside_the_replaced_words_and_the_input_is_unchanged() -> None:
    src = words("visit Lasvegas daily")
    out = GL.apply(src)
    assert texts(out) == "visit Las Vegas daily"
    a, b = out[1], out[2]
    assert (a.start_ms, b.end_ms) == (src[1].start_ms, src[1].end_ms)
    assert a.start_ms <= a.end_ms == b.start_ms <= b.end_ms
    assert texts(src) == "visit Lasvegas daily"  # nothing changed in place
    two = words("the Kyo To trip")  # two words for one: it spans both
    one = GL.apply(two)
    assert texts(one) == "the Kyoto trip" and (one[1].start_ms, one[1].end_ms) == (
        two[1].start_ms,
        two[2].end_ms,
    )


def test_a_form_is_not_matched_across_a_long_pause() -> None:
    gap = glossary.MAX_INNER_PAUSE_MS + 1
    ws = [Word(text="Kyo", start_ms=0, end_ms=300), Word(text="To", start_ms=300 + gap, end_ms=600 + gap)]
    assert texts(GL.apply(ws)) == "Kyo To"


def test_the_users_terms_win_and_can_say_a_word_is_right() -> None:
    mine = GL.with_user(glossary.parse_text("Curie = Kurie\nDostoyefsky\n~Big Bang\nNietzsche = Nitzshe"))
    assert texts(mine.apply(words("Kurie and Nitzshe and Neechee"))) == "Curie and Nietzsche and Nietzsche"
    assert texts(mine.apply(words("the Dostoyefsky novel"))) == "the Dostoyefsky novel"  # its rule dropped
    assert texts(GL.apply(words("the Dostoyefsky novel"))) == "the Dostoevsky novel"
    assert texts(mine.apply(words("a big bang"))) == "a big bang"  # ~: case left alone
    with_case = GL.with_user(glossary.parse_text("Big Bang"))
    assert texts(with_case.apply(words("a big bang"))) == "a Big Bang"
    over = GL.with_user([Entry("nietzsche", (), (), fix_case=False)])  # the user's own word beats the built-in rule
    assert texts(over.apply(words("the nietzsche"))) == "the nietzsche"
    assert mine.digest() != GL.digest()


@pytest.mark.parametrize(
    ("text", "why"),
    [("= Neechee", "Line 1"), ("ok\nNietzsche =", "Line 2"), ("X = ., !", "no letters"),
     ("Nietzsche = " + " ".join(["w"] * (glossary.MAX_WORDS + 1)), "longer than")],
)  # fmt: skip
def test_the_users_list_says_which_line_is_wrong(text: str, why: str) -> None:
    with pytest.raises(glossary.GlossaryError, match=why):
        glossary.parse_text(text)


def test_notes_and_the_built_in_text_form() -> None:
    assert glossary.parse_text("# my notes\n\nCurie = Kurie\n") == [Entry("Curie", (), ("Kurie",))]
    again = glossary.parse_text(glossary.as_text(GL.entries))
    assert [(e.term, e.also, e.wrong, e.fix_case) for e in again] == [
        (e.term, e.also, e.wrong, e.fix_case) for e in GL.entries
    ]


def _doc(text: str) -> Document:
    ws = words(text, step=700)
    seg = Segment(start_ms=ws[0].start_ms, end_ms=ws[-1].end_ms, words=ws)
    end = ws[-1].end_ms + 3000
    return Document(app_version="0.1.0", media=factories.media(duration_ms=end, decoded_ms=end),
                    run_config=factories.run_config(), segments=[seg])  # fmt: skip


def test_cues_carry_the_terms_and_the_document_keeps_what_was_recognised() -> None:
    doc = _doc("The old texts on Neechee and Mary Query are in the library.")
    track = build_track(doc, presets.get("cz-template"), gl=GL)
    text = " ".join(" ".join(c.lines) for c in track.cues)
    assert "Nietzsche" in text and "Marie Curie" in text and "Neechee" not in text
    assert "Neechee" in " ".join(w.text for w in doc.words())  # ADR 0007: engine output untouched


def test_a_changed_glossary_remakes_untouched_tracks_and_keeps_edited_ones() -> None:
    doc, _ = subtitles.ensure_tracks(_doc("The Neechee taught Curie today."), ["cz-template", "en-subtitles"], GL)
    mine = GL.with_user([Entry("Kurie", (), ("Curie",))])
    edited = doc.track("en-subtitles")
    assert edited is not None
    doc = doc.with_track(edited.model_copy(update={"edited": True}))
    again, reports = subtitles.ensure_tracks(doc, ["cz-template", "en-subtitles"], mine)
    by_id = {r.track.preset_id: r for r in reports}
    assert not by_id["cz-template"].reused and by_id["en-subtitles"].reused
    assert "Kurie" in " ".join(" ".join(c.lines) for c in by_id["cz-template"].track.cues)
    _, same = subtitles.ensure_tracks(again, ["cz-template"], mine)
    assert same[0].reused  # the same glossary again: nothing to remake


def test_a_track_made_before_the_glossary_is_remade_but_an_edited_one_keeps_its_rules() -> None:
    from parakeet_stt.server import editing

    doc, _ = subtitles.ensure_tracks(_doc("The old house stood today."), ["cz-template"])
    track = doc.track("cz-template")
    assert track is not None and "+" in track.digest
    old = track.model_copy(update={"digest": presets.rules_part(track.digest)})  # as stored before this version
    _, rep = subtitles.ensure_tracks(doc.with_track(old), ["cz-template"])
    assert not rep[0].reused
    edited = old.model_copy(update={"edited": True})
    assert not editing._opened(edited, doc.with_track(edited)).preset_changed  # not "the rules changed"


# ---- through the app and the worker -------------------------------------------------------------------------------


@pytest.fixture
def data(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture
def client(data: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("FAKE_WORKER_PROJECT", str(_project(tmp_path / "proj")))
    with make_client(data, kill_after_s=1.0) as c:
        yield c


def _put(c: TestClient, **changes: Any) -> Any:
    s = c.get("/api/settings").json()["settings"]
    return c.put("/api/settings", json={**s, **changes}, headers=ORIGIN)


def test_own_terms_are_kept_as_typed_checked_and_used_everywhere(
    client: TestClient, tmp_path: Path, make_clip: MakeClip
) -> None:
    view = client.get("/api/settings").json()
    assert view["settings"]["glossary_text"] == "" and view["glossary_builtin"] == ""  # none built in
    r = _put(client, glossary_text="ok\n= Neechee")
    assert r.status_code == 400 and "Line 2" in r.json()["error"]
    doc = project.load(_project(tmp_path / "look"))
    word = next(w.text for w in doc.words() if w.text.isalpha() and len(w.text) > 4)
    mine = f"# my list\n\nNietzsche = {word}\n"
    assert _put(client, glossary_text=mine, output="folder", output_dir=str(tmp_path)).status_code == 200
    assert client.get("/api/settings").json()["settings"]["glossary_text"] == mine  # notes and blank lines kept
    job = add(client, make_clip("talk.wav", seconds=1))
    wait(lambda: state_of(client, job["id"]) == "done")
    view = client.get(f"/api/jobs/{job['id']}/tracks/cz-template").json()
    cue_text = " ".join(" ".join(c["lines"]) for c in view["track"]["cues"])
    assert "Nietzsche" in cue_text and not view["preset_changed"]
    said = client.get(f"/api/jobs/{job['id']}/words").json()["words"]
    assert "Nietzsche" in [w["text"].strip(".,?") for w in said]  # the editor gets the words the cues have
    r = client.post(f"/api/jobs/{job['id']}/export", json={"presets": ["cz-template"], "formats": ["srt"]},
                    headers=ORIGIN)  # fmt: skip
    assert r.status_code == 200, r.text
    assert "Nietzsche" in Path(r.json()["files"][0]).read_text(encoding="utf-8")
    # edit, then change the list: the edited track stays and is not said to have changed rules; start over uses it
    cues = view["track"]["cues"]
    body = {"base_revision": view["revision"], "cues": cues[1:], "next_cue_id": view["track"]["next_cue_id"]}
    r = client.put(f"/api/jobs/{job['id']}/tracks/cz-template", headers=ORIGIN, json=body)
    assert r.status_code == 200, r.text
    assert _put(client, glossary_text=f"Kierkegaard = {word}\n").status_code == 200
    again = client.get(f"/api/jobs/{job['id']}/tracks/cz-template").json()
    assert again["track"]["cues"] == cues[1:] and not again["preset_changed"]
    fresh = client.post(f"/api/jobs/{job['id']}/tracks/cz-template/resegment", headers=ORIGIN,
                        json={"base_revision": again["revision"]}).json()  # fmt: skip
    assert "Kierkegaard" in " ".join(" ".join(c["lines"]) for c in fresh["track"]["cues"])


def test_the_worker_makes_the_first_tracks_with_the_users_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_clip: MakeClip
) -> None:
    from parakeet_stt import pipeline
    from parakeet_stt.cancel import CancelToken
    from parakeet_stt.server import worker

    doc = _doc("The Neechee met Kurie today.")

    def fake(*_a: object, **_k: object) -> pipeline.Result:
        return pipeline.Result(doc, pipeline.RunStats())

    monkeypatch.setattr(pipeline, "transcribe_file", fake)
    key = key_of(tmp_path)
    buf = io.StringIO()
    spec = worker.JobSpec(source=str(make_clip("x.wav", seconds=1)), out_dir=str(tmp_path / "out"), stem="x",
                          engine="parakeet", data_dir=str(tmp_path), glossary_text="Curie = Kurie")  # fmt: skip
    worker.run(spec, worker._Out(buf), CancelToken())
    assert json.loads(buf.getvalue().splitlines()[-1])["t"] == "done"
    made = project.load(tmp_path / "out" / "x.pstt.json", key)
    text = " ".join(" ".join(c.lines) for t in made.subtitles for c in t.cues)
    assert "Curie" in text and "Kurie" not in text
