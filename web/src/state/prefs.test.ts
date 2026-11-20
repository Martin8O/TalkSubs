import { afterEach, describe, expect, it, vi } from "vitest";

import { remember, remembered } from "./prefs";

function fakeStorage(start: Record<string, string> = {}) {
  const items = new Map(Object.entries(start));
  vi.stubGlobal("localStorage", {
    getItem: (k: string) => items.get(k) ?? null,
    setItem: (k: string, v: string) => void items.set(k, v),
  });
}

describe("header choices kept for the first paint", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("starts English and light", () => {
    fakeStorage();
    expect(remembered()).toEqual({ lang: "en", look: "light" });
  });

  it("gives back what was kept", () => {
    fakeStorage();
    remember({ lang: "cs", look: "dark" });
    expect(remembered()).toEqual({ lang: "cs", look: "dark" });
  });

  it("falls back to the defaults on a damaged or strange value", () => {
    fakeStorage({ "pstt.prefs": "{broken" });
    expect(remembered()).toEqual({ lang: "en", look: "light" });
    fakeStorage({ "pstt.prefs": JSON.stringify({ lang: "de", look: "pink" }) });
    expect(remembered()).toEqual({ lang: "en", look: "light" });
  });

  it("works without any storage", () => {
    vi.stubGlobal("localStorage", undefined);
    expect(remembered()).toEqual({ lang: "en", look: "light" });
    expect(() => remember({ lang: "cs", look: "dark" })).not.toThrow();
  });
});
