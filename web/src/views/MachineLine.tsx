import { useState } from "react";

import { api, ApiError } from "../api/client";
import type { MachineInfo, ModelsInfo } from "../api/contract.gen";
import { perHour, roughly } from "../format";
import { s } from "../strings";
import { CardPack } from "./CardPack";

interface Props {
  machine: MachineInfo | null;
  detail?: boolean; // Settings: how it was measured, the cores in use, and "Measure again"
  cardPack?: ModelsInfo | null; // the graphics-card support: offered here where a card could use it
}

/** Where the speech model runs on this PC, why, and how long an hour of recording takes. */
export function MachineLine({ machine, detail = false, cardPack = null }: Props) {
  const S = s();
  const [error, setError] = useState<string | null>(null);
  if (!machine) return null;
  const M = S.machine;
  const hour = perHour(machine);
  const measure = async () => {
    setError(null);
    try {
      await api.measureSpeed();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : S.error.unknown);
    }
  };
  return (
    <div className="machine" data-device={machine.device}>
      <p className="machine-mode">
        {machine.device === "cuda" ? M.onCard(machine.card ?? M.unnamedCard) : M.onCpu}
        {machine.device === "cpu" && ` ${M.why[machine.reason]} ${M.cpuSlower}`}
      </p>
      <p className="machine-speed">
        {machine.measuring
          ? M.measuring
          : machine.measure_waiting
            ? M.measureWaiting
            : hour === null
              ? M.notMeasured
              : hour < 60
                ? M.perHourSoon
                : M.perHour(roughly(hour))}
      </p>
      <CardPack machine={machine} pack={cardPack} />
      {detail && (
        <>
          {machine.measured_by && !machine.measuring && (
            <p className="field-note">{machine.measured_by === "jobs" ? M.byJobs : M.byTest}</p>
          )}
          {machine.cpu_threads != null && <p className="field-note">{M.threads(machine.cpu_threads)}</p>}
          <div className="form-actions">
            <button
              type="button"
              className="btn"
              onClick={() => void measure()}
              data-tip={S.tips.measure}
              disabled={machine.measuring || machine.measure_waiting}
            >
              {M.measure}
            </button>
            <span className="field-note">{M.measureNote}</span>
          </div>
          {error && (
            <p className="msg msg-error" role="alert">
              {error}
            </p>
          )}
        </>
      )}
    </div>
  );
}
