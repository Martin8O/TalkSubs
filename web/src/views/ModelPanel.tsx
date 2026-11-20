import { useState } from "react";

import { api, ApiError } from "../api/client";
import type { ModelsInfo } from "../api/contract.gen";
import { bytes, percent, roughly } from "../format";
import { s } from "../strings";

/** Why the download failed, in the window's language; anything without a code as the app worded it. */
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

/** The speech model's one-time download on the first start: true progress, or why it failed and "Try again". */
export function ModelPanel({ models }: { models: ModelsInfo | null }) {
  const S = s();
  const [error, setError] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  if (!models || models.state === "ready" || models.state === "checking") return null;
  const M = S.models;
  const fraction = models.total_bytes > 0 ? models.done_bytes / models.total_bytes : 0;
  const retry = async () => {
    setError(null);
    setAsking(true);
    try {
      await api.fetchModels();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : S.error.unknown);
    } finally {
      setAsking(false);
    }
  };
  const left =
    models.eta_s == null
      ? S.progress.etaUnknown
      : models.eta_s < 60
        ? S.progress.etaSoon
        : S.progress.eta(roughly(models.eta_s));
  return (
    <section className="first-run" aria-label={M.label} data-state={models.state}>
      {models.state === "downloading" ? (
        <>
          <h2 className="first-run-title">{M.title}</h2>
          <progress className="bar" max={100} value={percent(fraction, false)} />
          <p className="row-detail" role="status">
            <span className="detail-phase">{M.amount(bytes(models.done_bytes), bytes(models.total_bytes))}</span>
            <span className="detail-pct">{`${percent(fraction, false)} %`}</span>
            <span className="detail-eta">{left}</span>
          </p>
          <p className="field-note">{M.note}</p>
        </>
      ) : (
        <>
          <h2 className="first-run-title">{M.failedTitle}</h2>
          <p className="msg msg-error" role="alert">
            {failureText(models)}
          </p>
          <div className="form-actions">
            <button
              type="button"
              className="btn btn-primary"
              disabled={asking}
              onClick={() => void retry()}
              data-tip={S.tips.retryModel}
            >
              {M.retry}
            </button>
          </div>
          {error && (
            <p className="msg msg-error" role="alert">
              {error}
            </p>
          )}
        </>
      )}
    </section>
  );
}
