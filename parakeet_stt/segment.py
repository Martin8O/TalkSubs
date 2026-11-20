"""Recognised words -> subtitle cues under a preset. Pure, deterministic, CPU only.

1. Blocks: a pause of `block_pause_ms` always ends a cue.
2. Inside a block, dynamic programming picks the cue ends with the lowest total cost (where each cue ends,
   how its lines break, reading speed, too-short cues, one cost per cue). A cue must fit the line limits and
   the duration budget; a word that fits no legal cue goes alone, at a huge cost, and the checker reports it.
3. Timing on the frame grid: start at the frame of the first word (minus the lead-in), end after the last
   word, extended for the minimum duration and the reading speed, kept a minimum gap from the next cue, and
   small gaps closed - without ever going past the maximum duration.
A word belongs to the cue with the latest start at or before the word's start (see `transcript`).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from parakeet_stt import glossary, presets, tidy, timecode
from parakeet_stt.glossary import Glossary
from parakeet_stt.presets import Language, Preset
from parakeet_stt.transcript import Cue, Document, FrameRate, SubtitleTrack, Word

_SENTENCE_END = tidy.SENTENCE_END
_CLAUSE_END = (",", ";", ":", "—", "–", "-")  # noqa: RUF001 - dashes on purpose
_CLOSERS = tidy.CLOSERS


@dataclass(frozen=True, slots=True)
class _W:
    text: str
    start: int
    end: int
    bare: str
    punct: bool  # ends with punctuation of any kind
    sentence_end: bool
    clause_end: bool
    capital: bool


@dataclass(frozen=True, slots=True)
class _Draft:
    first: _W
    last: _W
    lines: list[str]
    chars: int


def track_frame_rate(doc: Document, preset: Preset) -> FrameRate:
    return doc.media.frame_rate or timecode.parse_rate(preset.limits.default_fps)


def build_track(
    doc: Document, preset: Preset, lang: Language | None = None, gl: Glossary | None = None
) -> SubtitleTrack:
    """The cues of a document under a preset; their text has the glossary's terms put right (the built-in glossary
    when none is given); the document's own words stay as recognised."""
    lang = lang or presets.language(preset.language)
    gl = gl or glossary.builtin()
    fps = track_frame_rate(doc, preset)
    cues = Segmenter(preset, lang, fps).cues(gl.apply(tidy.words_for_cues(doc, preset, lang)), doc.media.decoded_ms)
    return SubtitleTrack(
        preset_id=preset.id, preset=preset, digest=presets.track_digest(preset, lang, gl),
        segmenter_version=presets.SEGMENTER_VERSION, frame_rate=fps, created_utc=datetime.now(UTC),
        next_cue_id=len(cues) + 1, cues=cues,
    )  # fmt: skip


class Segmenter:
    def __init__(self, preset: Preset, lang: Language, fps: FrameRate) -> None:
        self.p, self.lang, self.fps = preset, lang, fps
        self.lim, self.w = preset.limits, preset.segmenter
        frame = timecode.frame_ms(fps)
        self.min_gap_ms = math.ceil(self.lim.min_gap_frames * frame)
        # the word span may not use the lead-in nor the frame lost when both edges are rounded
        self.budget_ms = self.lim.max_duration_ms - preset.timing.lead_in_ms - math.ceil(frame)
        self.max_chars = self.lim.max_lines * self.lim.max_chars_per_line + (self.lim.max_lines - 1)

    # --- words ---------------------------------------------------------------------------------------------
    def _prep(self, w: Word) -> _W:
        core = w.text.rstrip(_CLOSERS)
        bare = presets.bare(w.text)
        return _W(
            text=w.text, start=w.start_ms, end=w.end_ms, bare=bare, punct=bool(w.text) and not w.text[-1].isalnum(),
            sentence_end=core.endswith(_SENTENCE_END) and bare not in self.lang.no_break_after,
            clause_end=core.endswith(_CLAUSE_END), capital=w.text[:1].isupper() and bare not in self.lang.not_names,
        )  # fmt: skip

    def break_cost(self, prev: _W, nxt: _W | None) -> float:
        """Cost of a cue or line ending between `prev` and `nxt` (None = the block ends here)."""
        if nxt is None:
            return 0.0
        w = self.w
        if prev.sentence_end:
            return w.cost_sentence_end
        pause = nxt.start - prev.end >= w.pause_hint_ms
        split_name = prev.capital and nxt.capital and not prev.punct
        if (prev.bare in self.lang.no_break_after and not prev.punct) or split_name:
            # grammar outweighs a hesitation: a pause only makes such a place merely poor instead of forbidden
            return w.cost_plain if pause else w.cost_forbidden
        cost = w.cost_plain
        if prev.clause_end:
            cost = min(cost, w.cost_clause)
        if pause:
            cost = min(cost, w.cost_pause)
        if nxt.bare in self.lang.break_before:
            cost = min(cost, w.cost_before_conjunction)
        return cost

    # --- one cue -------------------------------------------------------------------------------------------
    def layout(self, ws: Sequence[_W]) -> tuple[list[str], float] | None:
        """The best line split of these words, or None if they cannot fit the line limits."""
        maxc = self.lim.max_chars_per_line
        text = " ".join(x.text for x in ws)
        if len(text) <= maxc:
            return [text], 0.0
        if self.lim.max_lines == 1:
            return None
        best: tuple[list[str], float] | None = None
        top = -1
        for k in range(1, len(ws)):
            top += len(ws[k - 1].text) + 1
            bottom = len(text) - top - 1
            if top > maxc:
                break
            if bottom > maxc:
                continue
            cost = self.break_cost(ws[k - 1], ws[k]) + self.w.cost_line_imbalance * (top - bottom) ** 2
            if top > bottom:
                cost += self.w.cost_top_heavy
            if best is None or cost < best[1]:
                best = ([text[:top], text[top + 1 :]], cost)
        return best

    def _counted(self, ws: Sequence[_W], chars_with_spaces: int) -> int:
        return chars_with_spaces if self.lim.cps_counts_spaces else sum(len(x.text) for x in ws)

    def cue_cost(self, ws: Sequence[_W], nxt: _W | None, chars: int, starts_sentence: bool = True) -> float:
        start = ws[0].start - self.p.timing.lead_in_ms
        last_end = ws[-1].end
        free_until = nxt.start - self.min_gap_ms if nxt is not None else math.inf
        latest = min(free_until, start + self.lim.max_duration_ms)
        reading_end = max(last_end, min(last_end + self.p.timing.end_hold_ms, latest))
        seconds = max(reading_end - start, 1) / 1000
        cost = self.w.cost_per_cue
        over = chars / seconds - self.lim.max_cps
        if over > 0:
            cost += self.w.cost_cps_over * over**2
        if latest - start < self.lim.min_duration_ms:
            cost += self.w.cost_too_short
        # A cue holding a sentence boundary should hold whole sentences: a piece broken off on either side
        # ("... done. It's" or "mind. We sit ...") costs extra.
        inner = [m for m in range(len(ws) - 1) if ws[m].sentence_end]
        if inner:
            limit = self.w.sentence_piece_chars
            if not ws[-1].sentence_end and sum(len(x.text) + 1 for x in ws[inner[-1] + 1 :]) - 1 < limit:
                cost += self.w.cost_sentence_piece
            if not starts_sentence and sum(len(x.text) + 1 for x in ws[: inner[0] + 1]) - 1 < limit:
                cost += self.w.cost_sentence_piece
        return cost

    # --- a block -------------------------------------------------------------------------------------------
    def _block(self, ws: list[_W]) -> list[_Draft]:
        n = len(ws)
        best = [math.inf] * (n + 1)
        best[0] = 0.0
        back: list[tuple[int, list[str], int]] = [(-1, [], 0)] * (n + 1)
        for j in range(1, n + 1):
            nxt = ws[j] if j < n else None
            end_cost = self.break_cost(ws[j - 1], nxt)
            chars = -1
            for i in range(j - 1, -1, -1):
                chars += len(ws[i].text) + 1
                span = ws[j - 1].end - ws[i].start
                single = i == j - 1
                if not single and (chars > self.max_chars or span > self.budget_ms):
                    break
                if best[i] == math.inf:
                    continue
                part = ws[i:j]
                fit = self.layout(part) if span <= self.budget_ms else None
                if fit is None and not single:
                    continue  # these words cannot share a legal cue
                if fit is None:  # a lone word that fits no legal cue: it goes alone and is reported
                    lines, line_cost = [part[0].text], self.w.cost_forced
                else:
                    lines, line_cost = fit
                counted = self._counted(part, chars)
                starts = i == 0 or ws[i - 1].sentence_end
                cost = best[i] + line_cost + end_cost + self.cue_cost(part, nxt, counted, starts)
                if cost < best[j]:
                    best[j], back[j] = cost, (i, lines, counted)
        drafts: list[_Draft] = []
        j = n
        while j > 0:
            i, lines, counted = back[j]
            drafts.append(_Draft(first=ws[i], last=ws[j - 1], lines=lines, chars=counted))
            j = i
        drafts.reverse()
        return drafts

    # --- all of it -----------------------------------------------------------------------------------------
    def cues(self, words: Sequence[Word], media_end_ms: int) -> list[Cue]:
        ws = [self._prep(w) for w in words]
        drafts: list[_Draft] = []
        block: list[_W] = []
        for w in ws:
            if block and w.start - block[-1].end >= self.w.block_pause_ms:
                drafts += self._block(block)
                block = []
            block.append(w)
        if block:
            drafts += self._block(block)
        return self._time(drafts, media_end_ms)

    def _time(self, drafts: list[_Draft], media_end_ms: int) -> list[Cue]:
        fps, lim, timing = self.fps, self.lim, self.p.timing

        def floor_f(ms: float) -> int:
            return math.floor(ms * fps.num / (fps.den * 1000))

        def ceil_f(ms: float) -> int:
            return math.ceil(ms * fps.num / (fps.den * 1000))

        min_f, max_f = ceil_f(lim.min_duration_ms), floor_f(lim.max_duration_ms)
        hold_f, gap_f, close_f = floor_f(timing.end_hold_ms), lim.min_gap_frames, lim.close_gaps_up_to_frames
        media_end_f = floor_f(media_end_ms)

        # starts: the frame holding the first word's start, strictly increasing, and leaving at least one frame
        # before the media end. Only after a forced lone word can the bump move a start past its first word.
        starts: list[int] = []
        for d in drafts:
            s = min(max(0, floor_f(d.first.start - timing.lead_in_ms)), media_end_f - 1)
            if starts and s <= starts[-1]:
                s = starts[-1] + 1
            starts.append(s)

        cues: list[Cue] = []
        for k, d in enumerate(drafts):
            s = starts[k]
            nxt = starts[k + 1] if k + 1 < len(drafts) else None
            free = nxt - gap_f if nxt is not None else media_end_f
            cap = s + max_f
            e0 = max(timecode.ms_to_frame(d.last.end, fps), s + 1)
            if d.last.end - d.first.start <= self.budget_ms:
                e0 = min(e0, cap)  # rounding the last word's end must not tip a legal cue over the maximum
            e = max(e0, min(s + min_f, free, cap))  # minimum duration may use the whole free gap
            read_f = ceil_f(d.chars * 1000 / lim.max_cps)
            e = max(e, min(s + read_f, e0 + hold_f, free, cap))  # reading speed: at most end_hold past the word
            e = max(min(e, free), s + 1)  # keep the gap to the next cue (the checker reports it if impossible)
            if nxt is not None and close_f and gap_f < nxt - e <= close_f and nxt - gap_f - s <= max_f:
                e = nxt - gap_f  # chain: close a small gap down to the minimum
            e = max(min(e, media_end_f), s + 1)
            cues.append(Cue(id=k + 1, start_ms=timecode.frame_to_ms(s, fps), end_ms=timecode.frame_to_ms(e, fps),
                            lines=d.lines))  # fmt: skip
        return cues
