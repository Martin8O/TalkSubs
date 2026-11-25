// Calls to the app's own server. Every write is JSON with the page's own origin (the server refuses anything else).
import { s } from "../strings";
import type {
  CheckResult,
  Cue,
  DialogResult,
  ExportConflict,
  ExportRequest,
  ExportResult,
  Job,
  Settings,
  SettingsView,
  TrackSaved,
  TrackView,
  WordsView,
} from "./contract.gen";

export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;
  constructor(message: string, status: number, body: unknown) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

const TIMEOUT_MS = 20_000;

async function call<T>(method: string, path: string, body?: unknown, timeout: number | null = TIMEOUT_MS): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers: body === undefined ? {} : { "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: timeout === null ? undefined : AbortSignal.timeout(timeout),
    });
  } catch {
    throw new ApiError(s().error.network, 0, null);
  }
  if (res.status === 204) return undefined as T;
  const data: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    const msg = data && typeof data === "object" && "error" in data ? String(data.error) : s().error.unknown;
    throw new ApiError(msg, res.status, data);
  }
  return data as T;
}

export const api = {
  settings: () => call<SettingsView>("GET", "/api/settings"),
  saveSettings: (settings: Settings) => call<SettingsView>("PUT", "/api/settings", settings),
  dialog: (kind: "files" | "folder") => call<DialogResult>("POST", "/api/dialog", { kind }, null), // the user takes their time
  addFile: (path: string) => call<Job>("POST", "/api/jobs", { path }),
  addLink: (url: string) => call<Job>("POST", "/api/links", { url }),
  cancel: (id: string) => call<Job>("POST", `/api/jobs/${id}/cancel`, {}),
  measureSpeed: () => call<void>("POST", "/api/speed/measure", {}),
  fetchModels: () => call<void>("POST", "/api/models/fetch", {}),
  fetchCardPack: () => call<void>("POST", "/api/card/fetch", {}),
  fetchWhisperModel: () => call<void>("POST", "/api/whisper/fetch", {}),
  exportJob: (id: string, req: ExportRequest) => call<ExportResult>("POST", `/api/jobs/${id}/export`, req, 120_000),
  reveal: (id: string) => call<void>("POST", `/api/jobs/${id}/reveal`, {}),
  remove: (id: string) => call<void>("POST", `/api/jobs/${id}/remove`, {}),
  // the subtitle editor
  openTrack: (id: string, preset: string) =>
    call<TrackView>("GET", `/api/jobs/${id}/tracks/${preset}`, undefined, 60_000),
  // the words as this type's cues have them (tidied by its rules), so a split finds each word's own time
  words: (id: string, preset: string) =>
    call<WordsView>("GET", `/api/jobs/${id}/tracks/${preset}/words`, undefined, 60_000),
  check: (id: string, preset: string, cues: Cue[]) =>
    call<CheckResult>("POST", `/api/jobs/${id}/tracks/${preset}/check`, { cues }),
  saveTrack: (
    id: string,
    preset: string,
    base_revision: string,
    cues: Cue[],
    next_cue_id: number,
    translation_language: string | null = null,
  ) =>
    call<TrackSaved>(
      "PUT",
      `/api/jobs/${id}/tracks/${preset}`,
      { base_revision, cues, next_cue_id, translation_language },
      60_000,
    ),
  resegment: (id: string, preset: string, base_revision: string) =>
    call<TrackView>("POST", `/api/jobs/${id}/tracks/${preset}/resegment`, { base_revision }, 60_000),
  // only while the app's work cannot be opened: move it aside (never deleted) and start empty
  startFresh: () => call<void>("POST", "/api/storage/start-fresh", {}, 120_000),
};

/** The waveform of a job's preview: (min, max) pairs as signed bytes; null while the preview is not ready. */
export async function peaks(id: string): Promise<Int8Array | null> {
  const res = await fetch(`/api/jobs/${id}/peaks`).catch(() => null);
  if (!res?.ok) return null;
  return new Int8Array(await res.arrayBuffer());
}

export function isConflict(e: unknown): e is ApiError & { body: ExportConflict } {
  return e instanceof ApiError && e.status === 409 && !!e.body && typeof e.body === "object" && "existing" in e.body;
}

export interface UploadHandle {
  done: Promise<Job>;
  abort: () => void;
}

/** Send one dropped file as raw bytes; progress reports bytes sent. (fetch cannot report upload progress.) */
export function uploadFile(file: File, onProgress: (sent: number) => void): UploadHandle {
  const xhr = new XMLHttpRequest();
  const done = new Promise<Job>((resolve, reject) => {
    xhr.upload.onprogress = (e) => onProgress(e.loaded);
    xhr.onload = () => {
      let data: unknown = null;
      try {
        data = JSON.parse(xhr.responseText);
      } catch {
        /* not JSON: reported below */
      }
      if (xhr.status === 201) resolve(data as Job);
      else {
        const msg = data && typeof data === "object" && "error" in data ? String(data.error) : s().error.unknown;
        reject(new ApiError(msg, xhr.status, data));
      }
    };
    xhr.onerror = () => reject(new ApiError(s().error.network, 0, null));
    xhr.onabort = () => reject(new ApiError("aborted", -1, null));
  });
  xhr.open("PUT", "/api/uploads");
  xhr.setRequestHeader("content-type", "application/octet-stream");
  xhr.setRequestHeader("x-file-name", encodeURIComponent(file.name));
  xhr.send(file);
  return { done, abort: () => xhr.abort() };
}
