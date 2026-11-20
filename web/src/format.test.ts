import { describe, expect, it } from "vitest";

import { bytes, clock, estimateS, percent, perHour, roughly } from "./format";

describe("format", () => {
  it("clock", () => {
    expect(clock(0)).toBe("0:00");
    expect(clock(42.4)).toBe("0:42");
    expect(clock(725)).toBe("12:05");
    expect(clock(3729)).toBe("1:02:09");
  });
  it("roughly", () => {
    expect(roughly(20)).toBe("1 min");
    expect(roughly(185)).toBe("3 min");
    expect(roughly(7200)).toBe("2 h");
    expect(roughly(8100)).toBe("2 h 15 min");
  });
  it("bytes", () => {
    expect(bytes(950)).toBe("950 B");
    expect(bytes(12_300)).toBe("12.3 KB");
    expect(bytes(3_400_000)).toBe("3.40 MB");
    expect(bytes(1_250_000_000)).toBe("1.25 GB");
  });
  it("percent never shows 100 before done", () => {
    expect(percent(0.999, false)).toBe(99);
    expect(percent(1, true)).toBe(100);
    expect(percent(-1, false)).toBe(0);
  });
});

describe("estimates", () => {
  const pc = {
    device: "cpu" as const,
    reason: "no_card" as const,
    card: null,
    cpu_threads: 8,
    fixed_s: 5,
    per_s: 0.03,
    measured_by: "jobs" as const,
    measuring: false,
    measure_waiting: false,
  };
  it("adds the start-up to the cost per second of recording", () => {
    expect(estimateS(pc, 3600)).toBeCloseTo(5 + 108);
    expect(perHour(pc)).toBeCloseTo(108);
  });
  it("knows nothing without a measurement or a length", () => {
    expect(estimateS(null, 3600)).toBeNull();
    expect(estimateS({ ...pc, per_s: null }, 3600)).toBeNull();
    expect(estimateS(pc, null)).toBeNull();
    expect(perHour({ ...pc, per_s: null })).toBeNull();
  });
});
