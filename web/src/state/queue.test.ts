import { describe, expect, it } from "vitest";

import type { Job, ServerMessage, Status } from "../api/contract.gen";
import { apply, busy, emptyQueue } from "./queue";

const job = (id: string, over: Partial<Job> = {}): Job => ({
  id,
  name: `${id}.mp4`,
  source: { kind: "file", path: `D:\\${id}.mp4` },
  state: "queued",
  created_at: 1,
  started_at: null,
  finished_at: null,
  attempts: 0,
  error: null,
  note: null,
  project_file: null,
  progress: null,
  engine: "parakeet",
  exported: [],
  preview: null,
  media_s: null,
  ...over,
});

const status = (boot: string, seq: number, jobs: Job[]): Status => ({
  app: "parakeet-stt",
  app_version: "0.1.0",
  boot_id: boot,
  seq,
  machine: null,
  models: {
    state: "ready",
    done_bytes: 10,
    total_bytes: 10,
    eta_s: null,
    error: null,
    error_kind: null,
    need_bytes: null,
    free_bytes: null,
  },
  card_pack: null,
  paused_reason: null,
  paused_kind: null,
  jobs,
  storage_locked: false,
});
const snap = (boot: string, seq: number, jobs: Job[]): ServerMessage => ({
  type: "snapshot",
  seq,
  status: status(boot, seq, jobs),
});
const prog = { phase: "recognise", fraction: 0.5, media_done_s: 30, media_total_s: 60, eta_s: 12 } as const;

describe("queue state", () => {
  it("ignores frames before the first snapshot", () => {
    expect(apply(emptyQueue, { type: "job", seq: 1, job: job("a") })).toBe(emptyQueue);
  });

  it("replaces everything on a snapshot and drops frames it already contains", () => {
    let s = apply(emptyQueue, snap("b1", 5, [job("a")]));
    expect(s.jobs.map((j) => j.id)).toEqual(["a"]);
    s = apply(s, { type: "job", seq: 5, job: job("zz") }); // seq 5 is inside the snapshot
    expect(s.jobs).toHaveLength(1);
    s = apply(s, { type: "job", seq: 6, job: job("b") });
    expect(s.jobs.map((j) => j.id)).toEqual(["a", "b"]);
    expect(s.seq).toBe(6);
  });

  it("applies a preview update to its job only, and clears it when the server says so", () => {
    let s = apply(emptyQueue, snap("b1", 1, [job("a"), job("b")]));
    const making = {
      state: "making",
      kind: null,
      fraction: 0.4,
      media_done_s: 4,
      media_total_s: 10,
      error: null,
      peaks_per_s: null,
    } as const;
    s = apply(s, { type: "preview", seq: 2, job_id: "b", preview: making });
    expect(s.jobs.map((j) => j.preview?.state ?? null)).toEqual([null, "making"]);
    s = apply(s, { type: "preview", seq: 3, job_id: "b", preview: null });
    expect(s.jobs[1]?.preview).toBeNull();
    expect(apply(s, { type: "preview", seq: 3, job_id: "a", preview: making })).toBe(s); // not newer: dropped
  });

  it("updates a job in place, keeping the order", () => {
    let s = apply(emptyQueue, snap("b1", 1, [job("a"), job("b")]));
    s = apply(s, { type: "job", seq: 2, job: job("a", { state: "running" }) });
    expect(s.jobs.map((j) => [j.id, j.state])).toEqual([
      ["a", "running"],
      ["b", "queued"],
    ]);
    expect(busy(s)).toBe(true);
  });

  it("applies progress only to a running job", () => {
    let s = apply(emptyQueue, snap("b1", 1, [job("a", { state: "running" }), job("b", { state: "done" })]));
    s = apply(s, { type: "progress", seq: 2, job_id: "a", progress: prog });
    s = apply(s, { type: "progress", seq: 3, job_id: "b", progress: prog });
    expect(s.jobs[0]?.progress?.fraction).toBe(0.5);
    expect(s.jobs[1]?.progress).toBeNull();
  });

  it("takes the pause from a snapshot", () => {
    const st = { ...status("b1", 1, []), paused_reason: "full", paused_kind: "gpu_memory" as const };
    const s = apply(emptyQueue, { type: "snapshot", seq: 1, status: st });
    expect(s.pausedReason).toBe("full");
    expect(s.pausedKind).toBe("gpu_memory");
  });

  it("tracks the pause and removals", () => {
    let s = apply(emptyQueue, snap("b1", 1, [job("a"), job("b")]));
    s = apply(s, { type: "paused", seq: 2, reason: "GPU in use", kind: "gpu_busy" });
    expect(s.pausedReason).toBe("GPU in use");
    expect(s.pausedKind).toBe("gpu_busy");
    s = apply(s, { type: "paused", seq: 3, reason: null, kind: null });
    expect(s.pausedReason).toBeNull();
    expect(s.pausedKind).toBeNull();
    s = apply(s, { type: "removed", seq: 4, job_id: "a" });
    expect(s.jobs.map((j) => j.id)).toEqual(["b"]);
    expect(busy(s)).toBe(false);
  });

  it("notices a server restart by its new boot id", () => {
    let s = apply(emptyQueue, snap("b1", 9, [job("a")]));
    expect(s.restarted).toBe(false);
    s = apply(s, snap("b2", 0, [job("a")]));
    expect(s.restarted).toBe(true);
    expect(s.seq).toBe(0);
    s = apply(s, { type: "job", seq: 1, job: job("c") }); // numbering starts again after a restart
    expect(s.jobs).toHaveLength(2);
  });
});

describe("this PC", () => {
  const pc = {
    device: "cpu" as const,
    reason: "no_card" as const,
    card: null,
    cpu_threads: 8,
    fixed_s: 3,
    per_s: 0.03,
    measured_by: "test" as const,
    measuring: false,
    measure_waiting: false,
  };
  it("comes with the snapshot and is replaced by its own frame", () => {
    let st = apply(emptyQueue, { type: "snapshot", seq: 1, status: status("b1", 1, []) });
    expect(st.machine).toBeNull();
    st = apply(st, { type: "machine", seq: 2, machine: pc });
    expect(st.machine?.per_s).toBe(0.03);
    st = apply(st, { type: "machine", seq: 2, machine: { ...pc, per_s: 1 } }); // not newer: dropped
    expect(st.machine?.per_s).toBe(0.03);
  });
});

describe("the speech model's download", () => {
  it("comes with the snapshot and every newer frame replaces it", () => {
    let st = apply(emptyQueue, { type: "snapshot", seq: 1, status: status("b1", 1, []) });
    expect(st.models?.state).toBe("ready");
    expect(st.appVersion).toBe("0.1.0");
    const going = {
      state: "downloading",
      done_bytes: 4,
      total_bytes: 10,
      eta_s: 30,
      error: null,
      error_kind: null,
      need_bytes: null,
      free_bytes: null,
    } as const;
    st = apply(st, { type: "models", seq: 2, models: going });
    expect(st.models?.done_bytes).toBe(4);
    st = apply(st, { type: "models", seq: 2, models: { ...going, done_bytes: 9 } }); // not newer: dropped
    expect(st.models?.done_bytes).toBe(4);
  });

  it("keeps the graphics-card support's download apart from the model's", () => {
    let st = apply(emptyQueue, { type: "snapshot", seq: 1, status: status("boot-1", 1, []) });
    expect(st.cardPack).toBeNull();
    const pack = {
      state: "downloading",
      done_bytes: 5,
      total_bytes: 20,
      eta_s: null,
      error: null,
      error_kind: null,
      need_bytes: null,
      free_bytes: null,
    } as const;
    st = apply(st, { type: "card_pack", seq: 2, card_pack: pack });
    expect(st.cardPack?.done_bytes).toBe(5);
    expect(st.models?.state).toBe("ready");
  });
});
