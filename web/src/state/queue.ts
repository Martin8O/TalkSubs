// The queue as the window knows it, rebuilt from the server's numbered frames. Pure: no I/O here.
import type { Job, MachineInfo, ModelsInfo, ServerMessage, Status } from "../api/contract.gen";

export type PausedKind = NonNullable<Status["paused_kind"]>;

export interface QueueState {
  bootId: string | null; // null until the first snapshot
  seq: number; // the last frame applied
  jobs: Job[];
  pausedReason: string | null;
  pausedKind: PausedKind | null; // the window's own text for why the queue waits
  restarted: boolean; // the last snapshot came from a new server start
  locked: boolean; // the app's work cannot be opened (its key is missing or another account's)
  machine: MachineInfo | null; // where the model runs on this PC and how fast; null until the app has looked
  models: ModelsInfo | null; // the speech model's one-time download; null while the store is locked
  cardPack: ModelsInfo | null; // the graphics-card support (download package), offered where a card could use it
  whisperModel: ModelsInfo | null; // the Whisper engine's model: downloaded when Whisper is chosen
  appVersion: string | null;
}

export const emptyQueue: QueueState = {
  bootId: null,
  seq: 0,
  jobs: [],
  pausedReason: null,
  pausedKind: null,
  restarted: false,
  locked: false,
  machine: null,
  models: null,
  cardPack: null,
  whisperModel: null,
  appVersion: null,
};

/** Apply one frame. A snapshot replaces everything; any other frame not newer than what we have is dropped. */
export function apply(state: QueueState, msg: ServerMessage): QueueState {
  if (msg.type === "snapshot") {
    const st = msg.status;
    return {
      bootId: st.boot_id,
      seq: msg.seq,
      jobs: st.jobs,
      pausedReason: st.paused_reason,
      pausedKind: st.paused_kind ?? null,
      restarted: state.bootId !== null && state.bootId !== st.boot_id,
      locked: st.storage_locked,
      machine: st.machine ?? null,
      models: st.models ?? null,
      cardPack: st.card_pack ?? null,
      whisperModel: st.whisper_model ?? null,
      appVersion: st.app_version,
    };
  }
  if (state.bootId === null || msg.seq <= state.seq) return state;
  const next = { ...state, seq: msg.seq };
  switch (msg.type) {
    case "job": {
      const i = state.jobs.findIndex((j) => j.id === msg.job.id);
      const jobs = i < 0 ? [...state.jobs, msg.job] : state.jobs.map((j, k) => (k === i ? msg.job : j));
      return { ...next, jobs };
    }
    case "progress":
      return {
        ...next,
        jobs: state.jobs.map((j) =>
          j.id === msg.job_id && (j.state === "running" || j.state === "fetching")
            ? { ...j, progress: msg.progress }
            : j,
        ),
      };
    case "preview":
      return { ...next, jobs: state.jobs.map((j) => (j.id === msg.job_id ? { ...j, preview: msg.preview } : j)) };
    case "paused":
      return { ...next, pausedReason: msg.reason, pausedKind: msg.kind ?? null };
    case "removed":
      return { ...next, jobs: state.jobs.filter((j) => j.id !== msg.job_id) };
    case "machine":
      return { ...next, machine: msg.machine };
    case "models":
      return { ...next, models: msg.models };
    case "card_pack":
      return { ...next, cardPack: msg.card_pack };
    case "whisper_model":
      return { ...next, whisperModel: msg.whisper_model };
  }
}

/** Whether closing the window now would interrupt work. */
export function busy(state: QueueState): boolean {
  return state.jobs.some((j) => j.state === "running" || j.state === "fetching");
}
