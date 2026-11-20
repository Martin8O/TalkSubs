// Undo / redo over whole snapshots of the cue list (cheap: unchanged cues are shared between snapshots).
// Typing in one cue within a second counts as one step, like a text editor.

export interface History<T> {
  past: T[];
  present: T;
  future: T[];
  tag: string | null; // what the last step was, for coalescing ("text:12")
  at: number; // when it was made (ms)
}

export const LIMIT = 300;
export const COALESCE_MS = 1000;

export const start = <T>(present: T): History<T> => ({ past: [], present, future: [], tag: null, at: 0 });

/** Record a new present. A step with the same tag as the previous one, soon after it, replaces it instead. */
export function record<T>(h: History<T>, next: T, tag: string | null, now: number): History<T> {
  if (next === h.present) return h;
  if (tag !== null && tag === h.tag && now - h.at < COALESCE_MS && h.future.length === 0) {
    return { ...h, present: next, at: now };
  }
  return { past: [...h.past, h.present].slice(-LIMIT), present: next, future: [], tag, at: now };
}

export function undo<T>(h: History<T>): History<T> {
  const prev = h.past[h.past.length - 1];
  if (prev === undefined) return h;
  return { past: h.past.slice(0, -1), present: prev, future: [h.present, ...h.future], tag: null, at: 0 };
}

export function redo<T>(h: History<T>): History<T> {
  const [next, ...rest] = h.future;
  if (next === undefined) return h;
  return { past: [...h.past, h.present], present: next, future: rest, tag: null, at: 0 };
}
