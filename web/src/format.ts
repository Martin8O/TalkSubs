// Numbers as people read them.
import type { MachineInfo } from "./api/contract.gen";

/** 0:42 · 12:05 · 1:02:09 */
export function clock(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const mm = h > 0 ? String(m).padStart(2, "0") : String(m);
  return `${h > 0 ? `${h}:` : ""}${mm}:${String(sec).padStart(2, "0")}`;
}

/** A rough duration: N min, or H h M min (callers say "less than a minute" themselves). */
export function roughly(seconds: number): string {
  const s = Math.max(60, Math.round(seconds));
  const min = Math.round(s / 60);
  if (min < 60) return `${min} min`;
  const h = Math.floor(min / 60);
  const rest = min % 60;
  return rest ? `${h} h ${rest} min` : `${h} h`;
}

/** 950 B · 12 KB · 3.4 MB · 1.25 GB */
export function bytes(n: number): string {
  if (n < 1000) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n;
  let i = -1;
  do {
    v /= 1000;
    i++;
  } while (v >= 1000 && i < units.length - 1);
  const digits = v >= 100 ? 0 : v >= 10 ? 1 : 2;
  return `${v.toFixed(digits)} ${units[i]}`;
}

/** Whole percent, never 100 before the job is really done. */
export function percent(fraction: number, done: boolean): number {
  const p = Math.floor(Math.min(Math.max(fraction, 0), 1) * 100);
  return done ? 100 : Math.min(p, 99);
}

/** Seconds a transcription of `mediaS` seconds of recording should take on this PC, or null if unknown. */
export function estimateS(m: MachineInfo | null, mediaS: number | null | undefined): number | null {
  if (!m || m.fixed_s == null || m.per_s == null || mediaS == null) return null;
  return m.fixed_s + mediaS * m.per_s;
}

/** Seconds of work per hour of recording (start-up not counted), or null if not measured yet. */
export function perHour(m: MachineInfo | null): number | null {
  return m && m.per_s != null ? m.per_s * 3600 : null;
}
