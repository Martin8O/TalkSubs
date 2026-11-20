import { describe, expect, it } from "vitest";

import { inOrder, moved } from "./order";

const t = (...ids: string[]) => ids.map((id) => ({ id }));

describe("the order of the subtitle types", () => {
  it("lists named types first, in the user's order, and the others after in their own order", () => {
    expect(inOrder(t("cz", "en", "my-a", "my-b"), ["my-b", "en"]).map((x) => x.id)).toEqual([
      "my-b",
      "en",
      "cz",
      "my-a",
    ]);
    expect(inOrder(t("cz", "en"), []).map((x) => x.id)).toEqual(["cz", "en"]);
    expect(inOrder(t("cz", "en"), ["gone", "en"]).map((x) => x.id)).toEqual(["en", "cz"]); // a removed id is ignored
  });

  it("moves one type to a new place and refuses a place outside the list", () => {
    expect(moved(["a", "b", "c"], "c", 0)).toEqual(["c", "a", "b"]);
    expect(moved(["a", "b", "c"], "a", 2)).toEqual(["b", "c", "a"]);
    const same = ["a", "b"];
    expect(moved(same, "a", 5)).toBe(same);
    expect(moved(same, "x", 0)).toBe(same);
  });
});
