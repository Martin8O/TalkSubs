// Dropped files waiting to be copied into the app, one at a time. Pure: the component does the sending.

export type UploadPhase = "waiting" | "sending" | "checking" | "failed";

export interface Upload {
  key: number;
  name: string;
  size: number;
  sent: number;
  phase: UploadPhase;
  error: string | null;
}

export type UploadAction =
  | { type: "add"; files: { key: number; name: string; size: number }[] }
  | { type: "start"; key: number }
  | { type: "progress"; key: number; sent: number }
  | { type: "fail"; key: number; error: string }
  | { type: "drop"; key: number }; // finished (the job row takes over), cancelled or dismissed

export function reduceUploads(list: Upload[], a: UploadAction): Upload[] {
  switch (a.type) {
    case "add":
      return [...list, ...a.files.map((f) => ({ ...f, sent: 0, phase: "waiting" as const, error: null }))];
    case "start":
      return list.map((u) => (u.key === a.key ? { ...u, phase: "sending" } : u));
    case "progress":
      return list.map((u) =>
        u.key === a.key ? { ...u, sent: a.sent, phase: a.sent >= u.size ? "checking" : "sending" } : u,
      );
    case "fail":
      return list.map((u) => (u.key === a.key ? { ...u, phase: "failed", error: a.error } : u));
    case "drop":
      return list.filter((u) => u.key !== a.key);
  }
}

/** The next file to send, or null while one is being sent. */
export function nextToSend(list: Upload[]): Upload | null {
  if (list.some((u) => u.phase === "sending" || u.phase === "checking")) return null;
  return list.find((u) => u.phase === "waiting") ?? null;
}

export function inFlight(list: Upload[]): boolean {
  return list.some((u) => u.phase !== "failed");
}
