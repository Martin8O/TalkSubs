import { useState } from "react";

import type { Preset, PresetInfo, Settings, SettingsView } from "../api/contract.gen";
import { inOrder, moved } from "../state/order";
import { presetText, s, standardText } from "../strings";
import { Info } from "./Info";

interface Props {
  view: SettingsView;
  cur: Settings;
  change: (patch: Partial<Settings>) => void;
}

type Severity = Preset["severity"]["cps"];
type Rule = keyof Preset["severity"];
const RULES: Rule[] = ["chars_per_line", "lines", "cps", "min_duration", "max_duration", "min_gap", "break_quality"];
const GRIDS = ["24000/1001", "24/1", "25/1", "30000/1001", "30/1"];
const MIN_SPAN_MS = 200; // between the shortest and longest subtitle, and after a lead-in (the server's own rule)
const MAX_OWN = 20;
const MAX_RETIRED = 50;

/** A number typed freely; it is judged (kept within its bounds) when the field is left or Enter is pressed, never
 * while typing, so "37" can be typed into a field whose lowest value is 10. */
function NumField(props: {
  label: string;
  tip: string;
  value: number;
  min: number;
  max: number;
  step: number;
  set: (v: number) => void;
}) {
  const { label, tip, value, min, max, step, set } = props;
  const [draft, setDraft] = useState<string | null>(null);
  const commit = () => {
    if (draft === null) return;
    const v = Number(draft.replace(",", "."));
    setDraft(null);
    if (draft.trim() !== "" && Number.isFinite(v)) set(Math.min(max, Math.max(min, v)));
  };
  return (
    <label className="type-field" data-tip={tip}>
      <span>{label}</span>
      <input
        type="text"
        inputMode="decimal"
        value={draft ?? String(value)}
        aria-valuemin={min}
        aria-valuemax={max}
        data-step={step}
        onChange={(e) => setDraft(e.currentTarget.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            commit();
          } else if (e.key === "Escape") setDraft(null);
        }}
      />
    </label>
  );
}

/** A new own type's id: minted once, random, never made from the name (it names the subtitle files). */
function mintId(taken: Set<string>): string {
  for (;;) {
    const bytes = crypto.getRandomValues(new Uint8Array(4));
    const id = `my-${Array.from(bytes, (b) => b.toString(36).padStart(2, "0")).join("")}`;
    if (!taken.has(id)) return id;
  }
}

/** What the lists elsewhere show for an own type (the same shape as the server's description of a type). */
export function infoOf(p: Preset, kind: PresetInfo["kind"] = "custom"): PresetInfo {
  return {
    id: p.id,
    kind,
    name: p.name,
    description: p.description,
    max_chars_per_line: p.limits.max_chars_per_line,
    max_lines: p.limits.max_lines,
    max_cps: p.limits.max_cps,
    max_duration_ms: p.limits.max_duration_ms,
  };
}

/** The user's own subtitle types: start one from a standard or a built-in type, change its values, remove it (its
 * subtitles stay usable: a removed type is kept aside and can be put back). Saved with the other settings. */
export function TypesEditor({ view, cur, change }: Props) {
  const S = s();
  const T = S.types;
  const [open, setOpen] = useState<string | null>(null);
  // the lecture guidelines first: the closest to what this app is for
  const rank = (id: string) => (id.startsWith("lecture-") ? (id.endsWith("-fast") ? 1 : 0) : 2);
  const standards = [...view.standards].sort((x, y) => rank(x.id) - rank(y.id));
  const [base, setBase] = useState(standards[0]?.id ?? "");
  const own = cur.custom_presets ?? [];
  const retired = cur.retired_presets ?? [];
  const taken = new Set([...own, ...retired, ...view.builtin_presets].map((p) => p.id));

  // an own type's description says where it came from: the values it is changed to are shown beside it anyway
  const add = (from: Preset, name: string, origin: string) => {
    const id = mintId(taken);
    change({ custom_presets: [...own, { ...structuredClone(from), id, name, description: T.basedOn(origin) }] });
    setOpen(id);
  };
  const update = (id: string, patch: (p: Preset) => Preset) =>
    change({ custom_presets: own.map((p) => (p.id === id ? patch(p) : p)) });
  const saved = new Set((view.settings.custom_presets ?? []).map((x) => x.id));
  const retire = (p: Preset) => {
    // a type never saved has made no subtitles yet: it simply goes; a saved one is kept aside
    const keep = saved.has(p.id) || (view.settings.retired_presets ?? []).some((x) => x.id === p.id);
    if (keep && retired.length >= MAX_RETIRED) {
      window.alert(T.retiredFull);
      return;
    }
    if (!window.confirm(keep ? T.removeAsk(p.name) : T.dropAsk(p.name))) return;
    change({
      custom_presets: own.filter((x) => x.id !== p.id),
      retired_presets: keep ? [...retired, p] : retired,
      export_presets: cur.export_presets.filter((x) => x !== p.id),
    });
    setOpen(null);
  };
  const forget = (p: Preset) => {
    if (!window.confirm(T.forgetAsk(p.name))) return;
    change({ retired_presets: retired.filter((x) => x.id !== p.id) });
  };
  // every type the user can use, in their order (removed ones are listed apart)
  const all = inOrder(
    [
      ...view.builtin_presets.map((p) => ({ id: p.id, p, kind: "builtin" as const })),
      ...own.map((p) => ({ id: p.id, p, kind: "custom" as const })),
    ],
    cur.type_order ?? [],
  );
  // only the grip starts a drag: the fields inside an open row stay selectable
  const [grabbed, setGrabbed] = useState<string | null>(null);
  const [dragging, setDragging] = useState<string | null>(null);
  const reorder = (id: string, to: number) => {
    const ids = all.map((x) => x.id);
    const next = moved(ids, id, to);
    if (next !== ids) change({ type_order: next });
  };
  const restore = (p: Preset) =>
    change({ custom_presets: [...own, p], retired_presets: retired.filter((x) => x.id !== p.id) });

  return (
    <fieldset className="card types">
      <legend>
        {T.title}
        <Info text={T.info} />
      </legend>
      <p className="field-note">{T.orderInfo}</p>

      <ul className="type-list type-sortable">
        {all.map(({ id, p, kind }, i) => (
          <li
            key={id}
            className={`type-row${kind === "custom" ? " type-own" : ""}${dragging === id ? " type-dragging" : ""}`}
            draggable={grabbed === id}
            onDragStart={(e) => {
              setDragging(id);
              e.dataTransfer.effectAllowed = "move";
            }}
            onDragOver={(e) => {
              if (dragging === null) return;
              e.preventDefault();
              if (dragging !== id) reorder(dragging, i);
            }}
            onDragEnd={() => {
              setDragging(null);
              setGrabbed(null);
            }}
          >
            <span className="type-move">
              <span
                className="type-grip"
                aria-hidden="true"
                data-tip={T.dragTip}
                onMouseDown={() => setGrabbed(id)}
                onMouseUp={() => setGrabbed(null)}
              >
                ⠿
              </span>
              <button
                type="button"
                className="btn btn-small btn-quiet"
                disabled={i === 0}
                aria-label={T.up}
                data-tip={T.up}
                onClick={() => reorder(id, i - 1)}
              >
                ▲
              </button>
              <button
                type="button"
                className="btn btn-small btn-quiet"
                disabled={i === all.length - 1}
                aria-label={T.down}
                data-tip={T.down}
                onClick={() => reorder(id, i + 1)}
              >
                ▼
              </button>
            </span>
            <span className="type-name">
              {kind === "builtin" ? (
                <>
                  {presetText(infoOf(p, "builtin")).name} <span className="type-kind">{T.builtin}</span>
                </>
              ) : (
                p.name
              )}
            </span>
            <span className="type-sum">{S.preset.limits(infoOf(p))}</span>
            {kind === "builtin" ? (
              <button
                type="button"
                className="btn btn-small"
                disabled={own.length >= MAX_OWN}
                data-tip={T.copyTip}
                onClick={() =>
                  add(p, T.copyName(presetText(infoOf(p, "builtin")).name), presetText(infoOf(p, "builtin")).name)
                }
              >
                {T.copy}
              </button>
            ) : (
              <>
                <span className="type-actions">
                  <button
                    type="button"
                    className="btn btn-small"
                    aria-expanded={open === p.id}
                    data-tip={T.editTip}
                    onClick={() => setOpen(open === p.id ? null : p.id)}
                  >
                    {open === p.id ? T.close : T.edit}
                  </button>
                  <button
                    type="button"
                    className="btn btn-small btn-quiet"
                    data-tip={T.removeTip}
                    onClick={() => retire(p)}
                  >
                    {T.remove}
                  </button>
                </span>
                {open === p.id && <TypeForm p={p} onChange={(next) => update(p.id, () => next)} />}
              </>
            )}
          </li>
        ))}
      </ul>

      <div className="type-new">
        <label className="type-new-label" htmlFor="type-standard">
          {T.fromStandard}
        </label>
        <select id="type-standard" value={base} onChange={(e) => setBase(e.target.value)} data-tip={T.standardTip}>
          {standards.map((p) => (
            <option key={p.id} value={p.id}>
              {standardText(p).name}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="btn btn-small"
          disabled={own.length >= MAX_OWN}
          data-tip={T.createTip}
          onClick={() => {
            const p = standards.find((x) => x.id === base);
            if (p) add(p, standardText(p).name, standardText(p).name);
          }}
        >
          {T.create}
        </button>
      </div>
      {standards.find((x) => x.id === base) && (
        <p className="field-note">{standardText(standards.find((x) => x.id === base)!).description}</p>
      )}
      {own.length >= MAX_OWN && <p className="field-note">{T.full}</p>}

      {retired.length > 0 && (
        <details className="type-retired">
          <summary>{T.retired(retired.length)}</summary>
          <p className="field-note">{T.retiredNote}</p>
          <ul className="type-list">
            {retired.map((p) => (
              <li key={p.id} className="type-row">
                <span className="type-name">{p.name}</span>
                <span className="type-actions">
                  <button
                    type="button"
                    className="btn btn-small"
                    disabled={own.length >= MAX_OWN}
                    data-tip={T.restoreTip}
                    onClick={() => restore(p)}
                  >
                    {T.restore}
                  </button>
                  <button
                    type="button"
                    className="btn btn-small btn-quiet"
                    data-tip={T.forgetTip}
                    onClick={() => forget(p)}
                  >
                    {T.forget}
                  </button>
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
      <p className="field-note">{T.saveNote(S.settings.save)}</p>
    </fieldset>
  );
}

/** The values of one own type; every field stays within what the server accepts (it checks again on saving). */
function TypeForm({ p, onChange }: { p: Preset; onChange: (p: Preset) => void }) {
  const T = s().types;
  const L = p.limits;
  const limits = (patch: Partial<Preset["limits"]>) => onChange({ ...p, limits: { ...L, ...patch } });
  const timing = (patch: Partial<Preset["timing"]>) => onChange({ ...p, timing: { ...p.timing, ...patch } });
  const text = p.text ?? { drop_fillers: true, window_capitals: true };
  const num = (
    label: string,
    tip: string,
    value: number,
    min: number,
    max: number,
    step: number,
    set: (v: number) => void,
  ) => <NumField label={label} tip={tip} value={value} min={min} max={max} step={step} set={set} />;
  return (
    <div className="type-form">
      <label className="type-field type-wide">
        <span>{T.name}</span>
        <input value={p.name} maxLength={60} onChange={(e) => onChange({ ...p, name: e.currentTarget.value })} />
      </label>
      <label className="type-field type-wide">
        <span>{T.description}</span>
        <input
          value={p.description}
          maxLength={300}
          onChange={(e) => onChange({ ...p, description: e.currentTarget.value })}
        />
      </label>

      <h4 className="type-group">{T.groupText}</h4>
      {num(T.chars, T.charsTip, L.max_chars_per_line, 10, 100, 1, (v) => limits({ max_chars_per_line: Math.round(v) }))}
      <label className="type-field" data-tip={T.linesTip}>
        <span>{T.lines}</span>
        <select value={L.max_lines} onChange={(e) => limits({ max_lines: Number(e.currentTarget.value) })}>
          <option value={1}>1</option>
          <option value={2}>2</option>
        </select>
      </label>
      {num(T.cps, T.cpsTip, L.max_cps, 5, 40, 0.5, (v) => limits({ max_cps: v }))}
      <label className="type-field type-check" data-tip={T.spacesTip}>
        <input
          type="checkbox"
          checked={L.cps_counts_spaces}
          onChange={(e) => limits({ cps_counts_spaces: e.currentTarget.checked })}
        />
        <span>{T.spaces}</span>
      </label>

      <h4 className="type-group">{T.groupTime}</h4>
      {num(T.minDur, T.minDurTip, L.min_duration_ms / 1000, 0, 10, 0.1, (v) =>
        limits({ min_duration_ms: Math.min(Math.round(v * 1000), L.max_duration_ms - MIN_SPAN_MS) }),
      )}
      {num(T.maxDur, T.maxDurTip, L.max_duration_ms / 1000, 1, 20, 0.5, (v) => {
        const max = Math.max(Math.round(v * 1000), L.min_duration_ms + MIN_SPAN_MS, p.timing.lead_in_ms + MIN_SPAN_MS);
        onChange({ ...p, limits: { ...L, max_duration_ms: max } });
      })}
      {num(T.gap, T.gapTip, L.min_gap_frames, 0, 25, 1, (v) =>
        limits({
          min_gap_frames: Math.round(v),
          close_gaps_up_to_frames:
            L.close_gaps_up_to_frames && L.close_gaps_up_to_frames <= Math.round(v)
              ? Math.min(50, Math.round(v) + 1)
              : L.close_gaps_up_to_frames,
        }),
      )}
      {num(T.closeGaps, T.closeGapsTip, L.close_gaps_up_to_frames, 0, 50, 1, (v) =>
        limits({ close_gaps_up_to_frames: Math.round(v) && Math.max(Math.round(v), L.min_gap_frames + 1) }),
      )}
      {num(T.leadIn, T.leadInTip, p.timing.lead_in_ms, 0, 2000, 10, (v) =>
        timing({ lead_in_ms: Math.min(Math.round(v), L.max_duration_ms - MIN_SPAN_MS) }),
      )}
      {num(T.hold, T.holdTip, p.timing.end_hold_ms, 0, 5000, 50, (v) => timing({ end_hold_ms: Math.round(v) }))}
      <label className="type-field" data-tip={T.gridTip}>
        <span>{T.grid}</span>
        <select value={L.default_fps} onChange={(e) => limits({ default_fps: e.currentTarget.value })}>
          {(GRIDS.includes(L.default_fps) ? GRIDS : [L.default_fps, ...GRIDS]).map((g) => (
            <option key={g} value={g}>
              {T.fps(g)}
            </option>
          ))}
        </select>
      </label>

      <h4 className="type-group">{T.groupWords}</h4>
      <label className="type-field type-check" data-tip={T.fillersTip}>
        <input
          type="checkbox"
          checked={text.drop_fillers}
          onChange={(e) => onChange({ ...p, text: { ...text, drop_fillers: e.currentTarget.checked } })}
        />
        <span>{T.fillers}</span>
      </label>
      <label className="type-field type-check" data-tip={T.capitalsTip}>
        <input
          type="checkbox"
          checked={text.window_capitals}
          onChange={(e) => onChange({ ...p, text: { ...text, window_capitals: e.currentTarget.checked } })}
        />
        <span>{T.capitals}</span>
      </label>

      <h4 className="type-group">
        {T.groupSeverity}
        <Info text={T.severityInfo} />
      </h4>
      {RULES.map((r) => (
        <label key={r} className="type-field">
          <span>{T.rule[r]}</span>
          <select
            value={p.severity[r]}
            onChange={(e) => onChange({ ...p, severity: { ...p.severity, [r]: e.currentTarget.value as Severity } })}
          >
            <option value="hard">{T.hard}</option>
            <option value="soft">{T.soft}</option>
          </select>
        </label>
      ))}
    </div>
  );
}
