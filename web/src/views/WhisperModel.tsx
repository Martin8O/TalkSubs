import { useState } from "react";

import { api, ApiError } from "../api/client";
import type { ModelsInfo } from "../api/contract.gen";
import { bytes, percent, roughly } from "../format";
import { s } from "../strings";

/** Why the download failed, in the window's language (the same causes as the speech model's download). */
function failureText(m: ModelsInfo): string | null {
  const E = s().models.error;
  switch (m.error_kind) {
    case "no_space":
      return E.no_space(bytes(m.need_bytes ?? 0), bytes(m.free_bytes ?? 0));
    case "no_connection":
    case "dropped":
    case "secure":
    case "site":
    case "disk_full":
    case "damaged":
      return E[m.error_kind]();
    default:
      return m.error;
  }
}

/** The Whisper engine's model, shown under the engine choice while Whisper is chosen: offered with its size,
 * then its true progress, or why it failed and "Try again". Nothing once it is here. */
export function WhisperModel({ model }: { model: ModelsInfo | null }) {
  const S = s();
  const W = S.whisperModel;
  const [error, setError] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  if (!model || model.state === "ready" || model.state === "checking") return null;
  const going = model.state === "downloading";
  const start = async () => {
    setError(null);
    setAsking(true);
    try {
      await api.fetchWhisperModel();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : S.error.unknown);
    } finally {
      setAsking(false);
    }
  };
  const fraction = model.total_bytes > 0 ? model.done_bytes / model.total_bytes : 0;
  const left =
    model.eta_s == null ? S.progress.etaUnknown : model.eta_s < 60 ? S.progress.etaSoon : S.progress.eta(roughly(model.eta_s));
  return (
    <div className="card-pack" data-state={model.state} aria-label={W.label}>
      {going ? (
        <>
          <p className="card-pack-title">{W.downloading}</p>
          <progress className="bar" max={100} value={percent(fraction, false)} />
          <p className="row-detail" role="status">
            <span className="detail-phase">{S.models.amount(bytes(model.done_bytes), bytes(model.total_bytes))}</span>
            <span className="detail-pct">{`${percent(fraction, false)} %`}</span>
            <span className="detail-eta">{left}</span>
          </p>
          <p className="field-note">{W.goingNote}</p>
        </>
      ) : (
        <>
          {model.state === "failed" ? (
            <>
              <p className="card-pack-title">{W.failedTitle}</p>
              <p className="msg msg-error" role="alert">
                {failureText(model)}
              </p>
            </>
          ) : (
            <p className="field-note">{W.offer(bytes(model.total_bytes))}</p>
          )}
          <div className="form-actions">
            <button type="button" className="btn btn-primary" disabled={asking} onClick={() => void start()}>
              {model.state === "failed" ? S.models.retry : W.download(bytes(model.total_bytes))}
            </button>
          </div>
        </>
      )}
      {error && (
        <p className="msg msg-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
