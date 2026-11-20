import { useCallback, useEffect, useReducer, useRef, useState, type DragEvent } from "react";

import { api, ApiError, uploadFile, type UploadHandle } from "../api/client";
import type { SettingsView } from "../api/contract.gen";
import { bytes } from "../format";
import type { QueueState } from "../state/queue";
import { inFlight, nextToSend, reduceUploads } from "../state/uploads";
import { s } from "../strings";
import { Info } from "./Info";
import { JobRow } from "./JobRow";
import { MachineLine } from "./MachineLine";

/** The refusals people meet most, in the window's language; anything else as the app worded it. */
function uploadError(e: unknown, file: File): string {
  const S = s();
  const name = file.name;
  if (!(e instanceof ApiError)) return S.error.unknown;
  if (e.status === 415) return S.upload.notMedia(name);
  if (e.status === 413) {
    const b = e.body as { free_bytes?: unknown; spare_bytes?: unknown } | null;
    const free = b?.free_bytes;
    const spare = b?.spare_bytes;
    if (typeof free === "number" && typeof spare === "number")
      return S.upload.noRoom(bytes(file.size), bytes(free), bytes(spare));
    return S.upload.noSpace;
  }
  if (e.status === 422) return S.upload.unreadable(name);
  return e.message;
}

interface Props {
  queue: QueueState;
  settings: SettingsView | null;
  settingsError: string | null;
  onUploadingChange: (uploading: boolean) => void;
}

let nextKey = 1;

export function QueuePage({ queue, settings, settingsError, onUploadingChange }: Props) {
  const S = s();
  const [uploads, dispatch] = useReducer(reduceUploads, []);
  const files = useRef(new Map<number, File>());
  const sending = useRef<{ key: number; handle: UploadHandle } | null>(null);
  const [dragging, setDragging] = useState(false);
  const [choosing, setChoosing] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);
  const [link, setLink] = useState("");
  const [linking, setLinking] = useState(false);

  const busyUploading = inFlight(uploads);
  useEffect(() => onUploadingChange(busyUploading), [busyUploading, onUploadingChange]);

  // send the next waiting file once nothing else is being sent
  const next = nextToSend(uploads);
  useEffect(() => {
    if (!next || sending.current) return;
    const file = files.current.get(next.key);
    if (!file) return;
    const key = next.key;
    dispatch({ type: "start", key });
    const handle = uploadFile(file, (sent) => dispatch({ type: "progress", key, sent }));
    sending.current = { key, handle };
    const settle = () => {
      files.current.delete(key);
      sending.current = null; // before the dispatch, so the next file can start in that render
    };
    handle.done.then(
      () => {
        settle();
        dispatch({ type: "drop", key });
      },
      (e: unknown) => {
        settle();
        if (e instanceof ApiError && e.status === -1) dispatch({ type: "drop", key });
        else dispatch({ type: "fail", key, error: uploadError(e, file) });
      },
    );
  }, [next]);

  useEffect(() => () => sending.current?.handle.abort(), []); // the window going away stops the copy

  const addDropped = useCallback((list: FileList) => {
    const added = Array.from(list).map((f) => {
      const key = nextKey++;
      files.current.set(key, f);
      return { key, name: f.name, size: f.size };
    });
    if (added.length) dispatch({ type: "add", files: added });
  }, []);

  const cancelUpload = (key: number) => {
    if (sending.current?.key === key) sending.current.handle.abort();
    else {
      files.current.delete(key);
      dispatch({ type: "drop", key });
    }
  };

  const addLink = async () => {
    setAddError(null);
    setLinking(true);
    try {
      await api.addLink(link);
      setLink(""); // the link's secret part does not stay on screen
    } catch (e) {
      setAddError(e instanceof ApiError ? e.message : S.error.unknown);
    } finally {
      setLinking(false);
    }
  };

  const choose = async () => {
    setAddError(null);
    setChoosing(true);
    try {
      const { paths } = await api.dialog("files");
      for (const p of paths) {
        try {
          await api.addFile(p);
        } catch (e) {
          setAddError(e instanceof ApiError ? e.message : S.error.unknown);
        }
      }
    } catch (e) {
      setAddError(e instanceof ApiError ? e.message : S.error.unknown);
    } finally {
      setChoosing(false);
    }
  };

  const onDrag = (e: DragEvent, on: boolean) => {
    if (!e.dataTransfer.types.includes("Files")) return;
    e.preventDefault();
    setDragging(on);
  };

  const jobs = [...queue.jobs].reverse(); // newest first

  return (
    <div
      className="queue-page"
      onDragOver={(e) => onDrag(e, true)}
      onDragEnter={(e) => onDrag(e, true)}
      onDragLeave={(e) => {
        if (e.currentTarget === e.target) setDragging(false);
      }}
      onDrop={(e) => {
        onDrag(e, false);
        if (e.dataTransfer.files.length) addDropped(e.dataTransfer.files);
      }}
    >
      <section className={`drop${dragging ? " drop-active" : ""}`} aria-label={S.drop.title}>
        <svg className="drop-icon" viewBox="0 0 48 48" aria-hidden="true">
          <circle cx="24" cy="24" r="24" className="drop-icon-bg" />
          <path d="M24 31V15M17 22l7-7 7 7M15 34h18" className="drop-icon-line" />
        </svg>
        <div className="drop-text">
          <h1 className="drop-title">
            {dragging ? S.drop.active : S.drop.title}
            <Info text={S.drop.info} />
          </h1>
          <p className="drop-hint">
            {S.drop.hint}{" "}
            <button
              type="button"
              className="btn btn-primary"
              onClick={choose}
              disabled={choosing}
              data-tip={S.tips.choose}
            >
              {choosing ? S.drop.choosing : S.drop.button}
            </button>
          </p>
          <p className="drop-note">{S.drop.copyNote}</p>
        </div>
      </section>
      <form
        className="link-form"
        onSubmit={(e) => {
          e.preventDefault();
          void addLink();
        }}
      >
        <label className="link-label" htmlFor="link-input">
          {S.link.label}
        </label>
        <div className="link-row">
          <input
            id="link-input"
            className="link-input"
            type="text"
            inputMode="url"
            autoComplete="off"
            spellCheck={false}
            placeholder={S.link.placeholder}
            value={link}
            onChange={(e) => setLink(e.target.value)}
          />
          <button type="submit" className="btn" disabled={linking || link.trim() === ""} data-tip={S.tips.addLink}>
            {linking ? S.link.adding : S.link.add}
          </button>
        </div>
        <p className="drop-note">{S.link.note}</p>
      </form>
      {settingsError && (
        <p className="msg msg-error" role="alert">
          {settingsError}
        </p>
      )}
      {addError && (
        <p className="msg msg-error" role="alert">
          {addError}
        </p>
      )}

      {uploads.length > 0 && (
        <ul className="uploads" aria-label={S.upload.list}>
          {uploads.map((u) => (
            <li key={u.key} className={`upload upload-${u.phase}`}>
              <div className="row-head">
                <span className="row-name" title={u.name}>
                  {u.name}
                </span>
                {u.phase !== "failed" && <span className="row-size">{bytes(u.size)}</span>}
                {u.phase !== "checking" && ( // once every byte is in, the job is being added: too late to cancel
                  <div className="row-actions">
                    <button
                      type="button"
                      className="btn btn-quiet"
                      onClick={() => cancelUpload(u.key)}
                      data-tip={S.tips.cancelUpload}
                    >
                      {u.phase === "failed" ? S.upload.dismiss : S.upload.cancel}
                    </button>
                  </div>
                )}
              </div>
              {u.phase === "failed" ? (
                <p className="msg msg-error">{`${S.upload.failed}: ${u.error ?? ""}`}</p>
              ) : (
                <>
                  <progress className="bar" max={u.size || 1} value={u.sent} />
                  <p className="row-detail">
                    {u.phase === "waiting"
                      ? S.upload.waiting
                      : u.phase === "checking"
                        ? S.upload.checking
                        : S.upload.sending(bytes(u.sent), bytes(u.size))}
                  </p>
                </>
              )}
            </li>
          ))}
        </ul>
      )}

      <section className="list" aria-label={S.queue.title}>
        <div className="list-head">
          <h2>{S.queue.title}</h2>
          {jobs.some((j) => j.state === "done" || j.state === "failed" || j.state === "cancelled") && (
            <span className="list-hint">{S.queue.clearHint}</span>
          )}
        </div>
        <MachineLine machine={queue.machine} cardPack={queue.cardPack} />
        {jobs.length === 0 && queue.bootId !== null && <p className="empty">{S.queue.empty}</p>}
        <ul className="jobs">
          {jobs.map((j) => (
            <JobRow key={j.id} job={j} settings={settings} machine={queue.machine} />
          ))}
        </ul>
      </section>
    </div>
  );
}
