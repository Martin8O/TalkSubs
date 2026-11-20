// Every change the editor makes to a track, as pure functions on (cues, next id). Each returns the same
// object when it does nothing, so the caller can tell "no change" from a change.
// Times always land on the frame grid and inside the media; cues stay sorted by (start, id) as the contract wants.
// Rule breaks (overlap, length, reading speed ...) are allowed here: the server's rule checker reports them.
import type { Cue, FrameRate, Word } from "../api/contract.gen";
import { snap, snapDown, stepFrames } from "./timecode";

export interface Cues {
  cues: Cue[];
  nextId: number;
  lang?: string | null; // the language the translations are in (kept with the track)
}

export interface Grid {
  fps: FrameRate;
  mediaEndMs: number;
  minGapFrames: number;
  minDurationMs: number;
}

const byStart = (a: Cue, b: Cue) => a.start_ms - b.start_ms || a.id - b.id;

function put(state: Cues, changed: Cue[], removed: number[] = []): Cues {
  const drop = new Set([...removed, ...changed.map((c) => c.id)]);
  const cues = state.cues.filter((c) => !drop.has(c.id)).concat(changed);
  cues.sort(byStart);
  const top = changed.reduce((m, c) => Math.max(m, c.id + 1), state.nextId);
  return { ...state, cues, nextId: top };
}

export const indexOf = (state: Cues, id: number): number => state.cues.findIndex((c) => c.id === id);

/** Index of the last cue starting at or before `ms` (-1 before the first): a binary search, the list is sorted. */
export function cueAt(cues: Cue[], ms: number): number {
  let lo = 0;
  let hi = cues.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (cues[mid]!.start_ms <= ms) {
      found = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  return found;
}

export const textOf = (c: Cue): string => c.lines.join("\n");
export const linesOf = (text: string): string[] => text.replace(/\r/g, "").split("\n");

export const translationOf = (c: Cue): string => (c.translation ?? []).join("\n");

/** A cue's translation as typed; a blank one is none (it must not count as translated). */
export function setTranslation(state: Cues, id: number, text: string): Cues {
  const c = state.cues[indexOf(state, id)];
  if (!c || translationOf(c) === text) return state;
  const lines = linesOf(text);
  return put(state, [{ ...c, translation: lines.some((l) => l.trim()) ? lines : null }]);
}

/** The language the translations are in. */
export function setLanguage(state: Cues, lang: string): Cues {
  return state.lang === lang ? state : { ...state, lang };
}

export const hasTranslation = (state: Cues): boolean => state.cues.some((c) => c.translation);

export function setText(state: Cues, id: number, text: string): Cues {
  const c = state.cues[indexOf(state, id)];
  if (!c || textOf(c) === text) return state;
  return put(state, [{ ...c, lines: linesOf(text) }]);
}

/** Clamp to the media and the grid, keeping at least one frame; null when no such cue fits. */
export function fit(start: number, end: number, g: Grid): [number, number] | null {
  const last = snapDown(g.mediaEndMs, g.fps);
  const s = Math.max(0, Math.min(snap(start, g.fps), stepFrames(last, -1, g.fps)));
  const e = Math.min(Math.max(snap(end, g.fps), stepFrames(s, 1, g.fps)), last);
  return e > s ? [s, e] : null;
}

export function setTimes(state: Cues, id: number, start: number, end: number, g: Grid): Cues {
  const c = state.cues[indexOf(state, id)];
  const t = c && fit(start, end, g);
  if (!c || !t || (t[0] === c.start_ms && t[1] === c.end_ms)) return state;
  return put(state, [{ ...c, start_ms: t[0], end_ms: t[1] }]);
}

/** The cue's times with one edge typed by hand, or null when the cue cannot take it exactly as typed (on the frame
 * grid): a typed time is never silently clamped to the recording, nor does it push the other edge. */
export function typedEdge(c: Cue, edge: "start" | "end", ms: number, g: Grid): [number, number] | null {
  const [st, en] = edge === "start" ? [ms, c.end_ms] : [c.start_ms, ms];
  const t = fit(st, en, g);
  return t && t[0] === snap(st, g.fps) && t[1] === snap(en, g.fps) ? t : null;
}

export function nudge(state: Cues, id: number, edge: "start" | "end", frames: number, g: Grid): Cues {
  const c = state.cues[indexOf(state, id)];
  if (!c) return state;
  const start = edge === "start" ? stepFrames(c.start_ms, frames, g.fps) : c.start_ms;
  const end = edge === "end" ? stepFrames(c.end_ms, frames, g.fps) : c.end_ms;
  if (end <= start) return state; // never squeeze a cue to nothing
  return setTimes(state, id, start, end, g);
}

export function remove(state: Cues, id: number): Cues {
  return indexOf(state, id) < 0 ? state : { ...state, cues: state.cues.filter((c) => c.id !== id) };
}

/** A new empty cue starting at `atMs` with the preset's minimum duration; returns its id too. */
export function insert(state: Cues, atMs: number, g: Grid): [Cues, number | null] {
  const t = fit(atMs, atMs + g.minDurationMs, g);
  if (!t) return [state, null];
  const id = state.nextId;
  return [put(state, [{ id, start_ms: t[0], end_ms: t[1], lines: [""], translation: null }]), id];
}

/** Join a cue with the one after it: each former cue becomes one line. */
export function mergeNext(state: Cues, id: number): Cues {
  const i = indexOf(state, id);
  const a = state.cues[i];
  const b = state.cues[i + 1];
  if (!a || !b) return state;
  const lines = [a.lines.join(" ").trim(), b.lines.join(" ").trim()].filter(Boolean);
  // translations join the same way; none on either side stays none
  const tr = [a.translation, b.translation].map((t) => (t ?? []).join(" ").trim()).filter(Boolean);
  const merged = {
    ...a,
    end_ms: Math.max(a.end_ms, b.end_ms),
    lines: lines.length ? lines : [""],
    translation: tr.length ? tr : null,
  };
  return put(state, [merged], [b.id]);
}

const tokens = (s: string): string[] => s.split(/\s+/).filter(Boolean);

/**
 * The recognised words heard during a cue: those whose middle lies inside it. (Cue starts are rounded to the
 * frame grid, so a first word can start a few milliseconds before its cue.)
 */
export function wordsOf(c: Cue, words: Word[]): Word[] {
  const from = c.start_ms - 2000; // no word is longer than this
  let lo = 0;
  let hi = words.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (words[mid]!.start_ms < from) lo = mid + 1;
    else hi = mid;
  }
  const out: Word[] = [];
  for (let k = lo; k < words.length && words[k]!.start_ms < c.end_ms; k++) {
    const w = words[k]!;
    const middle = (w.start_ms + w.end_ms) / 2;
    if (middle >= c.start_ms && middle < c.end_ms) out.push(w);
  }
  return out;
}

/**
 * Split a cue at a caret position in its text (lines joined by "\n"). The second part starts where the
 * recognised word at the caret starts, when the cue's words still match what was heard; otherwise at the
 * share of characters before the caret. The first part ends the preset's minimum gap earlier when there is room.
 */
export function splitAt(state: Cues, id: number, caret: number, words: Word[], g: Grid): [Cues, number | null] {
  const i = indexOf(state, id);
  const c = state.cues[i];
  if (!c) return [state, null];
  const text = textOf(c);
  const left = text.slice(0, caret);
  const right = text.slice(caret);
  const leftLines = linesOf(left)
    .map((l) => l.trim())
    .filter(Boolean);
  const rightLines = linesOf(right)
    .map((l) => l.trim())
    .filter(Boolean);
  if (!leftLines.length || !rightLines.length) return [state, null];

  const heard = wordsOf(c, words);
  const k = tokens(left).length;
  const n = tokens(text).length;
  // a caret inside a word splits that word; the time then comes from the character share
  const atWordEdge = /\s$/.test(left) || /^\s/.test(right);
  let at: number;
  if (heard.length === n && atWordEdge && k > 0 && k < n) at = heard[k]!.start_ms;
  else {
    const chars = (s: string) => s.replace(/\s/g, "").length;
    at = c.start_ms + ((c.end_ms - c.start_ms) * chars(left)) / Math.max(1, chars(text));
  }
  const lo = stepFrames(c.start_ms, 1, g.fps);
  const hi = stepFrames(c.end_ms, -1, g.fps);
  if (hi < lo) return [state, null]; // a cue of one frame cannot be split
  const t = Math.min(Math.max(snap(at, g.fps), lo), hi);
  const gapped = stepFrames(t, -g.minGapFrames, g.fps);
  const firstEnd = gapped > c.start_ms ? gapped : t;
  const second = state.nextId;
  const next = put(state, [
    { ...c, end_ms: firstEnd, lines: leftLines },
    { id: second, start_ms: t, end_ms: c.end_ms, lines: rightLines, translation: null }, // re-split by the translator
  ]);
  return [next, second];
}

/** Characters per second as the rule checker counts them (shown beside each cue; the checker judges). */
export function readingSpeed(c: Cue, countsSpaces: boolean, lines: string[] = c.lines): number {
  const text = lines.join(" ");
  const chars = countsSpaces ? text.length : text.replace(/ /g, "").length;
  return chars / Math.max((c.end_ms - c.start_ms) / 1000, 0.001);
}
