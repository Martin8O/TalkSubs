// The frame grid, exactly as parakeet_stt/timecode.py computes it (integer milliseconds, half rounds up),
// so a time the page snaps is one the server's rule checker sees as on the grid.
import type { FrameRate } from "../api/contract.gen";

export function msToFrame(ms: number, f: FrameRate): number {
  return Math.floor((2 * ms * f.num + f.den * 1000) / (2 * f.den * 1000));
}

export function frameToMs(frame: number, f: FrameRate): number {
  return Math.floor((2 * frame * f.den * 1000 + f.num) / (2 * f.num));
}

export const snap = (ms: number, f: FrameRate): number => frameToMs(msToFrame(ms, f), f);

export const frameMs = (f: FrameRate): number => (1000 * f.den) / f.num;

/** The grid time `frames` frames away from the frame nearest `ms`. */
export const stepFrames = (ms: number, frames: number, f: FrameRate): number => frameToMs(msToFrame(ms, f) + frames, f);

/** The latest grid time at or before `ms` (a cue may end on the media's last frame, never after it). */
export function snapDown(ms: number, f: FrameRate): number {
  const t = snap(ms, f);
  return t > ms ? stepFrames(t, -1, f) : t;
}

/** H:MM:SS.mmm for the editor's time fields. */
export function stamp(ms: number): string {
  const t = Math.max(0, Math.round(ms));
  const h = Math.floor(t / 3_600_000);
  const m = Math.floor(t / 60_000) % 60;
  const s = Math.floor(t / 1000) % 60;
  const pad = (n: number, w = 2) => String(n).padStart(w, "0");
  return `${h}:${pad(m)}:${pad(s)}.${pad(t % 1000, 3)}`;
}

/** A time typed by hand -> milliseconds, or null. Takes "h:mm:ss.mmm", "mm:ss.mmm", "ss.mmm" or whole seconds,
 * with a comma or a full stop before the fraction (SRT and Czech style): "1:02,5" is 62 500 ms. */
export function parseStamp(text: string): number | null {
  const m = /^\s*(?:(\d+):)?(?:(\d+):)?(\d+)(?:[.,](\d{1,3}))?\s*$/.exec(text);
  if (!m) return null;
  const [, first, second, sec, frac] = m;
  const withHours = second !== undefined; // "h:mm:ss"; otherwise "mm:ss" (first) or just seconds
  const hours = withHours ? Number(first) : 0;
  const minutes = withHours ? Number(second) : first !== undefined ? Number(first) : 0;
  const seconds = Number(sec);
  if ((withHours && minutes > 59) || (first !== undefined && seconds > 59)) return null;
  const ms = frac === undefined ? 0 : Number(frac.padEnd(3, "0"));
  return ((hours * 60 + minutes) * 60 + seconds) * 1000 + ms;
}
