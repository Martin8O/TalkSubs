import { describe, expect, it } from "vitest";

import type { PresetInfo } from "./api/contract.gen";
import { presetText, s, setLanguage } from "./strings";

const preset: PresetInfo = {
  id: "cz-template",
  kind: "builtin",
  name: "Czech translation template",
  description: "from the file",
  max_chars_per_line: 42,
  max_lines: 2,
  max_cps: 17,
  max_duration_ms: 7000,
};

// keys whose values are functions or strings, recursively, so the two tables cannot drift apart
function shape(o: unknown): unknown {
  if (typeof o === "function") return "fn";
  if (typeof o === "string") return "str";
  if (o && typeof o === "object") return Object.fromEntries(Object.entries(o).map(([k, v]) => [k, shape(v)]));
  return typeof o;
}

describe("strings", () => {
  it("Czech has every key English has", () => {
    setLanguage("en");
    const en = shape(s()) as Record<string, unknown>;
    setLanguage("cs");
    const cs = shape(s()) as Record<string, unknown>;
    delete (en.preset as Record<string, unknown>).name; // English uses the preset files' own names
    delete (cs.preset as Record<string, unknown>).name;
    expect(cs).toEqual(en);
  });

  it("describes a preset from its limits in each language", () => {
    setLanguage("en");
    expect(presetText(preset).description).toBe(
      "English cues sized so that the longer Czech translation still fits. " +
        "Up to 42 characters × 2 lines, 7 s on screen, 17 characters per second.",
    );
    setLanguage("cs");
    expect(presetText(preset).name).toBe("Šablona pro český překlad");
    expect(presetText({ ...preset, max_cps: 17.5 }).description).toContain("17,5 znaků za sekundu");
    expect(presetText({ ...preset, id: "other" }).description).toBe("from the file");
  });

  it("Czech plurals", () => {
    setLanguage("cs");
    expect(s().result.saved(1, "D:")).toBe("Uloženo: 1 soubor do D:");
    expect(s().result.saved(4, "D:")).toBe("Uloženo: 4 soubory do D:");
    expect(s().result.saved(10, "D:")).toBe("Uloženo: 10 souborů do D:");
    expect(s().result.exists(10)).toBe("10 souborů s těmito názvy už existuje.");
  });
});
