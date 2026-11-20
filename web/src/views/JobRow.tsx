import { useState } from "react";

import { api, ApiError } from "../api/client";
import type { Job, MachineInfo, SettingsView } from "../api/contract.gen";
import { clock, estimateS, percent, roughly } from "../format";
import { s } from "../strings";
import { PreviewPlayer } from "./PreviewPlayer";
import { ResultPanel } from "./ResultPanel";

interface Props {
  job: Job;
  settings: SettingsView | null;
  machine?: MachineInfo | null;
}

/** "about N min left" before the real progress can judge it: this PC's estimate minus the time already spent. */
function leftText(job: Job, machine: MachineInfo | null): string {
  const S = s();
  const p = job.progress;
  if (p?.eta_s != null) return p.eta_s < 60 ? S.progress.etaSoon : S.progress.eta(roughly(p.eta_s));
  const total = job.state === "running" ? estimateS(machine, job.media_s) : null;
  if (total === null || job.started_at == null) return S.progress.etaUnknown;
  const left = total - (Date.now() / 1000 - job.started_at);
  return left < 60 ? S.progress.etaSoon : S.progress.eta(roughly(left));
}

export function JobRow({ job, settings, machine = null }: Props) {
  const S = s();
  // what was asked, and in which state: it stops counting as soon as the server reports a new state
  const [asked, setAsked] = useState<{
    what: "cancel" | "remove";
    state: Job["state"];
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [player, setPlayer] = useState(false);
  const [confirming, setConfirming] = useState(false); // Remove asks first: it deletes the app's own copies
  const finished = job.state === "done" || job.state === "failed" || job.state === "cancelled";
  const pending = asked && asked.state === job.state ? asked.what : null;

  const act = async (what: "cancel" | "remove") => {
    setAsked({ what, state: job.state });
    setError(null);
    try {
      await (what === "cancel" ? api.cancel(job.id) : api.remove(job.id));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : S.error.unknown);
      setAsked(null);
    }
  };

  const p = job.progress;
  return (
    <li className={`job job-${job.state}`} data-job={job.id}>
      <div className="row-head">
        <span className="row-name" title={job.name}>
          {job.name}
        </span>
        <span className={`badge badge-${job.state}`}>{S.state[job.state]}</span>
        <div className="row-actions">
          {job.preview?.state === "ready" && (
            <button
              type="button"
              className="btn btn-quiet"
              aria-expanded={player}
              onClick={() => setPlayer(!player)}
              data-tip={S.tips.preview}
            >
              {player ? S.preview.hide : S.preview.show}
            </button>
          )}
          {!finished && (
            <button
              type="button"
              className="btn btn-quiet"
              onClick={() => act("cancel")}
              disabled={pending !== null}
              data-tip={S.tips.cancelJob}
            >
              {pending === "cancel" ? S.job.cancelling : S.job.cancel}
            </button>
          )}
          {finished && (
            <button
              type="button"
              className="btn btn-quiet"
              data-tip={S.tips.remove}
              aria-expanded={confirming}
              onClick={() => setConfirming(!confirming)}
              disabled={pending !== null}
            >
              {S.job.remove}
            </button>
          )}
        </div>
      </div>

      {finished && confirming && (
        <div className="confirm" role="alertdialog" aria-label={S.job.removeAsk}>
          <p className="confirm-ask">{S.job.removeAsk}</p>
          <ul className="confirm-list">
            <li>
              <strong>{S.job.removeDeleted}</strong> {S.job.removeWhat[job.source.kind].deleted}
            </li>
            <li>
              <strong>{S.job.removeKept}</strong> {S.job.removeWhat[job.source.kind].kept}
            </li>
          </ul>
          <div className="confirm-actions">
            <button
              type="button"
              className="btn btn-danger"
              data-tip={S.tips.removeConfirm}
              onClick={() => {
                setConfirming(false);
                void act("remove");
              }}
            >
              {S.job.removeConfirm}
            </button>
            <button
              type="button"
              className="btn btn-quiet"
              onClick={() => setConfirming(false)}
              data-tip={S.tips.removeKeep}
            >
              {S.job.removeKeep}
            </button>
          </div>
        </div>
      )}
      {job.source.kind === "link" && <p className="row-detail row-link">{job.source.shown}</p>}
      {(job.state === "running" || job.state === "fetching") && (
        <>
          <progress className="bar" max={100} value={p ? percent(p.fraction, false) : 0} />
          <p className="row-detail">
            <span className="detail-phase">
              {p ? S.phase[p.phase] : job.state === "fetching" ? S.phase.fetch : S.phase.load}
            </span>
            {p && p.media_total_s > 0 && (p.phase === "recognise" || p.phase === "fetch") && (
              <span className="detail-pos">{S.progress.position(clock(p.media_done_s), clock(p.media_total_s))}</span>
            )}
            <span className="detail-pct">{`${p ? percent(p.fraction, false) : 0} %`}</span>
            <span className="detail-eta">{leftText(job, machine)}</span>
          </p>
        </>
      )}
      {job.state === "failed" && job.error && <p className="msg msg-error">{job.error}</p>}
      {job.state === "done" && job.note && <p className="msg msg-note">{job.note}</p>}
      {job.state === "queued" && job.attempts > 0 && <p className="row-detail">{S.job.attempts(job.attempts)}</p>}
      {job.state === "queued" && estimateS(machine, job.media_s) !== null && (
        <p className="row-detail detail-estimate">
          {(estimateS(machine, job.media_s) ?? 0) < 60
            ? S.job.estimateSoon
            : S.job.estimate(roughly(estimateS(machine, job.media_s) ?? 0))}
        </p>
      )}
      {job.preview?.state === "waiting" && <p className="row-detail">{S.preview.waiting}</p>}
      {job.preview?.state === "making" && (
        <>
          <progress className="bar bar-preview" max={100} value={percent(job.preview.fraction, false)} />
          <p className="row-detail">{S.preview.making(percent(job.preview.fraction, false))}</p>
        </>
      )}
      {job.preview?.state === "failed" && job.preview.error && (
        <p className="msg msg-note">{S.preview.failed(job.preview.error)}</p>
      )}
      {error && (
        <p className="msg msg-error" role="alert">
          {error}
        </p>
      )}

      {player && job.preview?.state === "ready" && <PreviewPlayer jobId={job.id} kind={job.preview.kind} />}
      {job.state === "done" && job.project_file && <ResultPanel job={job} settings={settings} />}
    </li>
  );
}
