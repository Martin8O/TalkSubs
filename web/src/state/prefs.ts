// The two choices made in the header (language and look). The app keeps them in its settings; the browser keeps
// a copy only so the very first paint is already right, before the settings arrive.
import { type Lang, setLanguage } from "../strings";

export type Look = "light" | "dark";

export interface Prefs {
  lang: Lang;
  look: Look;
}

const KEY = "pstt.prefs";

/** What the window showed last time, or the defaults. */
export function remembered(): Prefs {
  let raw: { lang?: unknown; look?: unknown } = {};
  try {
    raw = JSON.parse(localStorage.getItem(KEY) ?? "{}") as typeof raw;
  } catch {
    /* blocked storage or a damaged value: the defaults until the settings arrive */
  }
  return { lang: raw.lang === "cs" ? "cs" : "en", look: raw.look === "dark" ? "dark" : "light" };
}

export function remember(p: Prefs): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(p));
  } catch {
    /* private window or blocked storage: the app keeps the settings anyway */
  }
}

/** Make the window use them: the string table the next render reads, and the colour tokens. */
export function apply(p: Prefs): void {
  setLanguage(p.lang);
  if (typeof document !== "undefined") document.documentElement.dataset.look = p.look;
}
