import { useState } from "react";

import { api, ApiError } from "../api/client";
import type { MachineInfo, ModelsInfo } from "../api/contract.gen";
import { bytes, percent, roughly } from "../format";
import { s } from "../strings";

/** Why the download failed, in the window's language; anything without a code as the app worded it. */
function failureText(p: ModelsInfo): string | null {
  const E = s().card.error;
  switch (p.error_kind) {
    case "no_space":
      return E.no_space(bytes(p.need_bytes ?? 0), bytes(p.free_bytes ?? 0));
    case "no_connection":
    case "dropped":
    case "secure":
    case "site":
    case "disk_full":
    case "damaged":
      return E[p.error_kind]();
    default:
      return p.error;
  }
}

interface Props {
  machine: MachineInfo;
  pack: ModelsInfo | null;
}

/** The download package's graphics-card support: offered once the app found a card that could use it, then its
 * true progress (and "unpacking" at the end), or why it failed and "Try again". Nothing once the card is in use. */
export function CardPack({ machine, pack }: Props) {
  const S = s();
  const C = S.card;
  const [error, setError] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const going = pack?.state === "downloading";
  if (!pack || (machine.reason !== "card_pack_missing" && !going && pack.state !== "failed")) return null;
  if (pack.state === "ready" || pack.state === "checking") return null;
  const start = async () => {
    setError(null);
    setAsking(true);
    try {
      await api.fetchCardPack();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : S.error.unknown);
    } finally {
      setAsking(false);
    }
  };
  const fraction = pack.total_bytes > 0 ? pack.done_bytes / pack.total_bytes : 0;
  const unpacking = going && pack.total_bytes > 0 && pack.done_bytes >= pack.total_bytes;
  const left = unpacking
    ? C.unpacking
    : pack.eta_s == null
      ? S.progress.etaUnknown
      : pack.eta_s < 60
        ? S.progress.etaSoon
        : S.progress.eta(roughly(pack.eta_s));
  return (
    <div className="card-pack" data-state={pack.state} aria-label={C.label}>
      {going ? (
        <>
          <p className="card-pack-title">{C.downloading}</p>
          <progress className="bar" max={100} value={percent(fraction, false)} />
          <p className="row-detail" role="status">
            <span className="detail-phase">{S.models.amount(bytes(pack.done_bytes), bytes(pack.total_bytes))}</span>
            <span className="detail-pct">{`${percent(fraction, false)} %`}</span>
            <span className="detail-eta">{left}</span>
          </p>
          <p className="field-note">{C.goingNote}</p>
        </>
      ) : (
        <>
          <p className="card-pack-title">
            {pack.state === "failed" ? C.failedTitle : C.offer(machine.card ?? S.machine.unnamedCard)}
          </p>
          {pack.state === "failed" ? (
            <p className="msg msg-error" role="alert">
              {failureText(pack)}
            </p>
          ) : (
            <p className="field-note">{C.note(bytes(pack.total_bytes))}</p>
          )}
          <div className="form-actions">
            <button
              type="button"
              className="btn btn-primary"
              disabled={asking}
              onClick={() => void start()}
              data-tip={C.tip}
            >
              {pack.state === "failed" ? S.models.retry : C.download(bytes(pack.total_bytes))}
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
