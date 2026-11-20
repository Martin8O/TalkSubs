import { describe, expect, it } from "vitest";

import { inFlight, nextToSend, reduceUploads, type Upload } from "./uploads";

describe("upload list", () => {
  it("sends one file at a time, in order", () => {
    let l: Upload[] = reduceUploads([], {
      type: "add",
      files: [
        { key: 1, name: "a.mp4", size: 100 },
        { key: 2, name: "b.mp4", size: 50 },
      ],
    });
    expect(nextToSend(l)?.key).toBe(1);
    l = reduceUploads(l, { type: "start", key: 1 });
    expect(nextToSend(l)).toBeNull();
    l = reduceUploads(l, { type: "progress", key: 1, sent: 100 });
    expect(l[0]?.phase).toBe("checking");
    expect(nextToSend(l)).toBeNull();
    l = reduceUploads(l, { type: "drop", key: 1 });
    expect(nextToSend(l)?.key).toBe(2);
    expect(inFlight(l)).toBe(true);
  });

  it("keeps a failed file visible without blocking the rest", () => {
    let l: Upload[] = reduceUploads([], {
      type: "add",
      files: [
        { key: 1, name: "a.txt", size: 1 },
        { key: 2, name: "b.mp4", size: 1 },
      ],
    });
    l = reduceUploads(l, { type: "start", key: 1 });
    l = reduceUploads(l, { type: "fail", key: 1, error: "not audio" });
    expect(nextToSend(l)?.key).toBe(2);
    l = reduceUploads(l, { type: "drop", key: 2 });
    expect(inFlight(l)).toBe(false);
    expect(l[0]?.error).toBe("not audio");
  });
});
