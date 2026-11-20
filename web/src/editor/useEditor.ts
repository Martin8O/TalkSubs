// The editor's state: the cue list with its undo history, the rule checker's verdict on exactly the cues
// shown, and the autosave to the project file. The latest history lives in a ref as well, so two events in
// one tick (fast keys, a test driver) build on each other instead of on a stale render.
import { useCallback, useEffect, useRef, useState } from "react";

import { api, ApiError } from "../api/client";
import type { Cue, SubtitleTrack, Violation, Word } from "../api/contract.gen";
import { s } from "../strings";
import { type History, record, redo as redoStep, start, undo as undoStep } from "./history";
import type { Cues, Grid } from "./ops";
import { serial } from "./serial";

export const CHECK_AFTER_MS = 250;
export const SAVE_AFTER_MS = 1000;
const RETRY_SAVE_MS = 5000;

export type SaveState =
  | { kind: "saved" }
  | { kind: "pending" }
  | { kind: "saving" }
  | { kind: "failed"; message: string }
  | { kind: "stale"; message: string }; // changed elsewhere: autosave stops until the editor is opened again

export interface Verdict {
  cues: Cue[]; // the exact list these violations were computed for
  list: Violation[];
}

export interface Loaded {
  track: SubtitleTrack; // as opened (its cues are the first snapshot)
  grid: Grid;
  words: Word[];
  presetChanged: boolean;
}

export type LoadState = { kind: "loading" } | { kind: "failed"; message: string } | ({ kind: "ready" } & Loaded);

const message = (e: unknown) => (e instanceof ApiError ? e.message : s().error.unknown);

function gridOf(track: SubtitleTrack, mediaEndMs: number): Grid {
  const l = track.preset.limits;
  return {
    fps: track.frame_rate,
    mediaEndMs,
    minGapFrames: l.min_gap_frames,
    minDurationMs: l.min_duration_ms,
  };
}

export function useEditor(jobId: string, presetId: string) {
  const [load, setLoad] = useState<LoadState>({ kind: "loading" });
  const [hist, setHist] = useState<History<Cues> | null>(null);
  const [verdict, setVerdict] = useState<Verdict>({ cues: [], list: [] });
  const [save, setSave] = useState<SaveState>({ kind: "saved" });
  const histRef = useRef<History<Cues> | null>(null);
  const revision = useRef("");
  const saved = useRef<Cues | null>(null); // what the server holds now
  const [writes] = useState(serial); // autosave and start over, one at a time
  const floorId = useRef(1); // no new cue may take an id below this (ids are never reused, even after an undo)
  const stale = useRef(false);
  const checkSeq = useRef(0);

  const show = useCallback((h: History<Cues>) => {
    histRef.current = h;
    setHist(h);
    if (stale.current) return;
    const clean = h.present === saved.current; // an undo back to what is saved needs no save
    setSave((st) => (st.kind === "saving" ? st : clean ? { kind: "saved" } : { kind: "pending" }));
  }, []);

  useEffect(() => {
    let gone = false;
    if (!jobId) return; // the job list has not arrived yet
    Promise.all([api.openTrack(jobId, presetId), api.words(jobId, presetId)]).then(
      ([view, w]) => {
        if (gone) return;
        const cues: Cues = {
          cues: view.track.cues,
          nextId: view.track.next_cue_id,
          // the server's default, never "none": an undo back to the start then sends what the editor shows
          lang: view.track.translation_language ?? "cs",
        };
        revision.current = view.revision;
        saved.current = cues;
        floorId.current = cues.nextId;
        stale.current = false;
        show(start(cues));
        setVerdict({ cues: cues.cues, list: view.violations });
        setLoad({
          kind: "ready",
          track: view.track,
          grid: gridOf(view.track, view.media_end_ms),
          words: w.words,
          presetChanged: view.preset_changed,
        });
      },
      (e: unknown) => !gone && setLoad({ kind: "failed", message: message(e) }),
    );
    return () => {
      gone = true;
    };
  }, [jobId, presetId, show]);

  /** Apply one change; false when it changed nothing. `tag` lets consecutive typing count as one undo step. */
  const apply = useCallback(
    (change: (c: Cues) => Cues, tag: string | null = null): boolean => {
      const h = histRef.current;
      if (!h || stale.current) return false; // changed elsewhere: nothing more is accepted until it is reopened
      const p = h.present;
      const base = p.nextId < floorId.current ? { ...p, nextId: floorId.current } : p;
      const next = change(base);
      if (next === base) return false;
      floorId.current = Math.max(floorId.current, next.nextId);
      show(record(h, next, tag, performance.now()));
      return true;
    },
    [show],
  );
  const undo = useCallback(() => histRef.current && !stale.current && show(undoStep(histRef.current)), [show]);
  const redo = useCallback(() => histRef.current && !stale.current && show(redoStep(histRef.current)), [show]);

  // ---- saving --------------------------------------------------------------------------------------------
  // Each save sends the latest state on the revision the previous write returned (see serial.ts).
  const saveNow = useCallback(
    (): Promise<void> =>
      writes(async () => {
        const body = histRef.current?.present;
        if (!jobId || !body || body === saved.current || stale.current) return;
        setSave({ kind: "saving" });
        try {
          const r = await api.saveTrack(jobId, presetId, revision.current, body.cues, body.nextId, body.lang ?? null);
          revision.current = r.revision;
          saved.current = body;
          floorId.current = Math.max(floorId.current, r.next_cue_id);
          if (histRef.current?.present === body) {
            setVerdict({ cues: body.cues, list: r.violations });
            setSave({ kind: "saved" });
          } else setSave({ kind: "pending" });
        } catch (e) {
          if (e instanceof ApiError && e.status === 409) {
            stale.current = true;
            setSave({ kind: "stale", message: s().editor.staleShort }); // in the window's language
          } else setSave({ kind: "failed", message: message(e) });
        }
      }),
    [jobId, presetId, writes],
  );

  const present = hist?.present;
  useEffect(() => {
    if (!present || present === saved.current || stale.current) return;
    const t = window.setTimeout(() => void saveNow(), SAVE_AFTER_MS);
    return () => window.clearTimeout(t);
  }, [present, saveNow]);

  useEffect(() => {
    if (save.kind !== "failed" || !jobId) return; // a removed job: nothing to retry against
    const t = window.setTimeout(() => void saveNow(), RETRY_SAVE_MS);
    return () => window.clearTimeout(t);
  }, [save, saveNow, jobId]);

  // whatever is not saved yet goes out when the editor closes (switching preset or page)
  useEffect(() => () => void saveNow(), [saveNow]);

  // ---- the rule checker on what is shown -----------------------------------------------------------------
  useEffect(() => {
    if (!jobId || !present || present.cues === verdict.cues) return;
    const seq = ++checkSeq.current;
    const t = window.setTimeout(() => {
      api.check(jobId, presetId, present.cues).then(
        (r) => seq === checkSeq.current && setVerdict({ cues: present.cues, list: r.violations }),
        () => undefined, // the next change asks again; the save also returns a verdict
      );
    }, CHECK_AFTER_MS);
    return () => window.clearTimeout(t);
  }, [present, verdict.cues, jobId, presetId]);

  /**
   * Start the track over from the recording with the preset's current rules (the server saves it at once).
   * Undo brings the old cues back (they are then saved under the new rules). Runs in the same queue as the
   * autosave, after any pending save.
   */
  const resegment = useCallback(async (): Promise<string | null> => {
    await saveNow();
    return writes(async () => {
      if (stale.current) return s().editor.staleShort;
      try {
        const view = await api.resegment(jobId, presetId, revision.current);
        const fresh = await api.words(jobId, presetId).catch(() => null); // tidied by the rules the remake used
        const cues: Cues = {
          cues: view.track.cues,
          nextId: view.track.next_cue_id,
          lang: view.track.translation_language ?? "cs",
        };
        revision.current = view.revision;
        saved.current = cues;
        floorId.current = Math.max(floorId.current, cues.nextId);
        const h = histRef.current;
        if (h) show(record(h, cues, null, performance.now()));
        setVerdict({ cues: cues.cues, list: view.violations });
        // the editor now works with the rules the fresh track was made with
        setLoad((l) =>
          l.kind === "ready"
            ? {
                ...l,
                track: view.track,
                grid: gridOf(view.track, view.media_end_ms),
                words: fresh ? fresh.words : l.words,
                presetChanged: view.preset_changed,
              }
            : l,
        );
        return null;
      } catch (e) {
        return message(e);
      }
    });
  }, [jobId, presetId, saveNow, show, writes]);

  return { load, hist, verdict, save, apply, undo, redo, saveNow, resegment };
}
