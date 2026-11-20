import { describe, expect, it } from "vitest";

import type { Cue, FrameRate, Word } from "../api/contract.gen";
import { record, redo, start, undo } from "./history";
import {
  cueAt,
  type Cues,
  type Grid,
  hasTranslation,
  insert,
  mergeNext,
  nudge,
  typedEdge,
  remove,
  setLanguage,
  setText,
  setTimes,
  setTranslation,
  splitAt,
  wordsOf,
} from "./ops";
import { frameToMs, msToFrame, parseStamp, snap, snapDown, stamp } from "./timecode";

const F25: FrameRate = { num: 25, den: 1 };
const NTSC: FrameRate = { num: 30000, den: 1001 };
const F23: FrameRate = { num: 24000, den: 1001 };
const grid: Grid = { fps: F25, mediaEndMs: 60_000, minGapFrames: 2, minDurationMs: 1000 };

const cue = (id: number, s: number, e: number, ...lines: string[]): Cue => ({
  id,
  start_ms: s,
  end_ms: e,
  lines,
  translation: null,
});
const state = (...cues: Cue[]): Cues => ({ cues, nextId: Math.max(0, ...cues.map((c) => c.id)) + 1 });
const word = (text: string, s: number, e: number): Word => ({ text, start_ms: s, end_ms: e, conf: null });

describe("frame grid matches parakeet_stt/timecode.py", () => {
  // values printed by the Python module for the same inputs
  const inputs = [0, 19, 20, 21, 39, 40, 41, 1001, 3599999, 8100123];
  it.each([
    [F25, [0, 0, 40, 40, 40, 40, 40, 1000, 3600000, 8100120], [1, 202503], [40, 120, 8100120]],
    [NTSC, [0, 33, 33, 33, 33, 33, 33, 1001, 3599996, 8100125], [1, 242761], [33, 100, 6756850]],
    [F23, [0, 0, 0, 42, 42, 42, 42, 1001, 3600013, 8100134], [0, 194209], [42, 125, 8446063]],
  ] as const)("%o", (f, snapped, frames, times) => {
    expect(inputs.map((ms) => snap(ms, f))).toEqual(snapped);
    expect([20, 8100123].map((ms) => msToFrame(ms, f))).toEqual(frames);
    expect([1, 3, 202503].map((n) => frameToMs(n, f))).toEqual(times);
  });
  it("snapDown never passes the time", () => {
    expect(snapDown(59_990, F25)).toBe(59_960);
    expect(snapDown(60_000, F25)).toBe(60_000);
    expect(stamp(3_723_045)).toBe("1:02:03.045");
  });

  it("a time typed by hand reads in the usual forms and refuses the rest", () => {
    expect(parseStamp("1:02:03.045")).toBe(3_723_045);
    expect(parseStamp(stamp(3_723_045))).toBe(3_723_045); // what the editor shows reads back
    expect(parseStamp("0:01:02,5")).toBe(62_500); // comma, one digit of fraction
    expect(parseStamp("1:02,25")).toBe(62_250);
    expect(parseStamp("75")).toBe(75_000);
    expect(parseStamp(" 12.3 ")).toBe(12_300);
    expect(parseStamp("00:01:02,500")).toBe(62_500); // as in an SRT file
    for (const bad of ["", "abc", "1:60", "1:61:00", "1.2.3", "-5", "1:02:03.4567"]) expect(parseStamp(bad)).toBeNull();
  });
});

describe("operations", () => {
  const s = state(cue(1, 1000, 3000, "one two", "three four"), cue(2, 4000, 6000, "five six"));

  it("text edits keep the cue and say when nothing changed", () => {
    const t = setText(s, 1, "one two\nthree four");
    expect(t).toBe(s);
    const u = setText(s, 1, "hello\r\nworld");
    expect(u.cues[0]!.lines).toEqual(["hello", "world"]);
    expect(u.cues[1]).toBe(s.cues[1]); // untouched cues are shared
  });

  it("times snap to the grid, stay inside the media and keep cues sorted", () => {
    const t = setTimes(s, 2, 517, 2011, grid);
    expect(t.cues.map((c) => [c.id, c.start_ms, c.end_ms])).toEqual([
      [2, 520, 2000],
      [1, 1000, 3000],
    ]);
    const end = setTimes(s, 2, 59_000, 70_000, grid);
    expect(end.cues[1]!.end_ms).toBe(60_000);
    const tiny = setTimes(s, 1, 1000, 1000, grid); // at least one frame
    expect(tiny.cues[0]!.end_ms).toBe(1040);
  });

  it("no operation makes a cue the server would refuse (past the media, or of no length)", () => {
    const g = { ...grid, mediaEndMs: 60_020 }; // not on the grid: the nearest frame (60_040) lies past it
    const last = state(cue(1, 59_000, 60_000, "x"));
    expect(nudge(last, 1, "end", 1, g)).toBe(last);
    expect(setTimes(last, 1, 59_000, 99_000, g).cues[0]!.end_ms).toBe(60_000);
    expect(insert(last, 60_010, g)[0].cues[1]).toMatchObject({ start_ms: 59_960, end_ms: 60_000 });
    expect(insert({ cues: [], nextId: 1 }, 0, { ...g, mediaEndMs: 30 })[1]).toBeNull(); // no whole frame fits
    const one = state(cue(1, 1000, 1040, "a b"));
    expect(splitAt(one, 1, 2, [], g)[1]).toBeNull();
  });

  it("nudge moves one edge by whole frames and never inverts a cue", () => {
    expect(nudge(s, 1, "start", 1, grid).cues[0]!.start_ms).toBe(1040);
    expect(nudge(s, 1, "end", -1, grid).cues[0]!.end_ms).toBe(2960);
    const one = state(cue(1, 1000, 1040, "x"));
    expect(nudge(one, 1, "start", 1, grid)).toBe(one);
  });

  it("a typed edge is taken only exactly as typed (on the grid), never clamped or pushing the other edge", () => {
    const c = cue(1, 1000, 3000, "x");
    expect(typedEdge(c, "start", 1210, grid)).toEqual([1200, 3000]); // snapped to the frame
    expect(typedEdge(c, "end", 2480, grid)).toEqual([1000, 2480]);
    expect(typedEdge(c, "start", 3000, grid)).toBeNull(); // at or past the end
    expect(typedEdge(c, "start", 2990, grid)).toBeNull(); // would push the end one frame on
    expect(typedEdge(c, "end", grid.mediaEndMs + 5000, grid)).toBeNull(); // past the recording
  });

  it("insert, delete and merge; ids are never reused", () => {
    const [t, id] = insert(s, 3100, grid);
    expect(id).toBe(3);
    expect(t.cues.map((c) => c.id)).toEqual([1, 3, 2]);
    expect(t.cues[1]).toMatchObject({ start_ms: 3120, end_ms: 4120, lines: [""] });
    const r = remove(t, 3);
    expect(r.nextId).toBe(4);
    expect(insert(r, 0, grid)[1]).toBe(4);
    const m = mergeNext(s, 1);
    expect(m.cues).toEqual([cue(1, 1000, 6000, "one two three four", "five six")]);
    expect(mergeNext(s, 2)).toBe(s);
  });

  it("split takes the time of the recognised word at the caret", () => {
    const words = [
      word("one", 1010, 1300),
      word("two", 1400, 1700),
      word("three", 2000, 2400),
      word("four", 2500, 2900),
    ];
    const caret = "one two\n".length; // before "three"
    const [t, id] = splitAt(s, 1, caret, words, grid);
    expect(id).toBe(3);
    expect(t.cues.slice(0, 2)).toEqual([cue(1, 1000, 1920, "one two"), cue(3, 2000, 3000, "three four")]);
  });

  it("split falls back to the share of characters when the text no longer matches", () => {
    const edited = setText(s, 1, "one two\nthree four five six");
    const [t] = splitAt(edited, 1, "one two\n".length, [], grid);
    // 6 of 22 letters before the caret -> 1000 + 2000 * 6/22 = 1545 -> frame 1560
    expect(t.cues.slice(0, 2).map((c) => [c.start_ms, c.end_ms])).toEqual([
      [1000, 1480],
      [1560, 3000],
    ]);
    expect(splitAt(s, 1, 0, [], grid)[1]).toBeNull(); // nothing on one side
  });

  it("words of a cue are those whose middle lies inside it, even if they start a little earlier", () => {
    const ws = [word("a", 990, 1100), word("b", 2950, 3200), word("c", 4000, 4100)];
    expect(wordsOf(s.cues[0]!, ws).map((w) => w.text)).toEqual(["a"]);
  });

  it("cueAt finds the last cue starting at or before a time", () => {
    expect([0, 1000, 3999, 4000, 99_999].map((ms) => cueAt(s.cues, ms))).toEqual([-1, 0, 0, 1, 1]);
  });
});

describe("history", () => {
  it("undo and redo walk the snapshots; typing in one cue is one step", () => {
    let h = start("a");
    h = record(h, "ab", "text:1", 0);
    h = record(h, "abc", "text:1", 500);
    h = record(h, "abcd", "text:1", 1400); // still within a second of the previous keystroke
    h = record(h, "X", "merge", 1500);
    expect(h.past).toEqual(["a", "abcd"]);
    h = undo(h);
    expect(h.present).toBe("abcd");
    h = undo(h);
    expect(h.present).toBe("a");
    expect(undo(h)).toBe(h);
    h = redo(redo(h));
    expect(h.present).toBe("X");
    h = record(undo(h), "Y", "text:1", 9000);
    expect(h.future).toEqual([]);
    expect(h.past).toEqual(["a", "abcd"]);
  });
  it("a pause in typing starts a new step", () => {
    let h = start("a");
    h = record(h, "ab", "text:1", 0);
    h = record(h, "abc", "text:1", 2000);
    expect(h.past).toEqual(["a", "ab"]);
  });
});

describe("one write at a time", () => {
  it("runs tasks in order, each after the previous one finished, even after a failure", async () => {
    const { serial } = await import("./serial");
    const queue = serial();
    const log: string[] = [];
    let open = 0;
    const task =
      (name: string, fail = false) =>
      async () => {
        open++;
        expect(open).toBe(1); // never two at once
        log.push(`start ${name}`);
        await new Promise((r) => setTimeout(r, 5));
        log.push(`end ${name}`);
        open--;
        if (fail) throw new Error(name);
        return name;
      };
    const a = queue(task("a"));
    const b = queue(task("b", true));
    const c = queue(task("c"));
    await expect(a).resolves.toBe("a");
    await expect(b).rejects.toThrow("b");
    await expect(c).resolves.toBe("c");
    expect(log).toEqual(["start a", "end a", "start b", "end b", "start c", "end c"]);
  });
});

describe("translation", () => {
  const two = (): Cues => ({
    cues: [
      { ...cue(1, 1000, 2000, "one"), translation: ["jedna"] },
      { ...cue(2, 2040, 3000, "two"), translation: ["dva"] },
    ],
    nextId: 3,
  });

  it("a typed translation is kept per cue and a blank one is none", () => {
    const t = setTranslation(two(), 1, "první\nřádek");
    expect(t.cues[0]!.translation).toEqual(["první", "řádek"]);
    expect(setTranslation(t, 1, "  \n").cues[0]!.translation).toBeNull();
    expect(hasTranslation(setTranslation(setTranslation(two(), 1, ""), 2, ""))).toBe(false);
  });

  it("merge joins the translations, insert and the second half of a split have none", () => {
    expect(mergeNext(two(), 1).cues[0]!.translation).toEqual(["jedna", "dva"]);
    const none = { ...two(), cues: two().cues.map((c) => ({ ...c, translation: null })) };
    expect(mergeNext(none, 1).cues[0]!.translation).toBeNull();
    const [added, id] = insert(two(), 10_000, grid);
    expect(added.cues.find((c) => c.id === id)!.translation).toBeNull();
  });

  it("the language travels with the cues", () => {
    expect(setLanguage(two(), "sk").lang).toBe("sk");
    expect(mergeNext(setLanguage(two(), "sk"), 1).lang).toBe("sk");
  });
});
