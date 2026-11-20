import { useState } from "react";

import { api, ApiError, isConflict } from "../api/client";
import type { Job, SettingsView } from "../api/contract.gen";
import { presetText, s } from "../strings";
import { Info } from "./Info";

interface Props {
  job: Job;
  settings: SettingsView | null;
}

const toggle = (list: string[], item: string) =>
  list.includes(item) ? list.filter((x) => x !== item) : [...list, item];

export function ResultPanel({ job, settings }: Props) {
  const S = s();
  const [presets, setPresets] = useState<string[] | null>(null); // null: the defaults from the settings
  const [formats, setFormats] = useState<string[] | null>(null);
  const [texts, setTexts] = useState<("original" | "translation")[]>(["original"]);
  const [skipped, setSkipped] = useState<string[]>([]);
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<{ n: number; folder: string } | null>(null);
  const [conflict, setConflict] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!settings) return null;
  const order = (all: string[], chosen: string[]) => all.filter((x) => chosen.includes(x));
  const chosenPresets = order(
    settings.presets.map((p) => p.id),
    presets ?? settings.settings.export_presets,
  );
  const chosenFormats = order(settings.formats, formats ?? settings.settings.export_formats);
  const ready = chosenPresets.length > 0 && chosenFormats.length > 0 && texts.length > 0;
  const summary = [
    chosenPresets
      .map((id) => {
        const p = settings.presets.find((x) => x.id === id);
        return p ? presetText(p).name : id;
      })
      .join(" + "),
    chosenFormats.map((f) => S.format[f] ?? f).join(", "),
    texts.includes("translation") ? texts.map((t) => S.result[t]).join(" + ") : "",
  ]
    .filter(Boolean)
    .join(" · ");

  const save = async (replace: boolean) => {
    setSaving(true);
    setError(null);
    setConflict(null);
    setSaved(null);
    setSkipped([]);
    try {
      const r = await api.exportJob(job.id, {
        presets: chosenPresets,
        formats: chosenFormats,
        replace,
        texts: [texts[0] ?? "original", ...texts.slice(1)],
      });
      setSaved({ n: r.files.length, folder: r.folder });
      setSkipped(r.skipped ?? []);
    } catch (e) {
      if (isConflict(e)) setConflict(e.body.existing.length);
      else setError(e instanceof ApiError ? e.message : S.error.unknown);
    } finally {
      setSaving(false);
    }
  };

  const reveal = async () => {
    setError(null);
    try {
      await api.reveal(job.id);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : S.error.unknown);
    }
  };

  return (
    <div className="result">
      <div className="result-bar">
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => save(false)}
          disabled={saving || !ready}
          data-tip={S.tips.save}
        >
          {saving ? S.result.saving : S.result.save}
        </button>
        <a
          className="btn"
          data-tip={S.tips.edit}
          href={`#edit/${job.id}/${chosenPresets[0] ?? settings.presets[0]?.id ?? ""}`}
        >
          {S.editor.open}
        </a>
        {job.exported.length > 0 && (
          <button type="button" className="btn" onClick={reveal} data-tip={S.tips.openFolder}>
            {S.result.openFolder}
          </button>
        )}
        <span className="result-summary">{ready ? summary : S.result.pickOne}</span>
        <button
          type="button"
          className="btn btn-small"
          aria-expanded={open}
          onClick={() => setOpen(!open)}
          data-tip={S.tips.change}
        >
          {open ? S.result.closeChoices : S.result.change}
        </button>
      </div>
      {open && (
        <div className="result-choices">
          <fieldset className="choice">
            <legend>
              {S.result.presets}
              <Info text={S.infos.presets} />
            </legend>
            {settings.presets.map((p) => (
              <label key={p.id} className="check" title={presetText(p).description}>
                <input
                  type="checkbox"
                  checked={chosenPresets.includes(p.id)}
                  onChange={() => setPresets(toggle(chosenPresets, p.id))}
                />
                <span>{presetText(p).name}</span>
              </label>
            ))}
          </fieldset>
          <fieldset className="choice" data-tip={S.tips.texts}>
            <legend>{S.result.texts}</legend>
            {(["original", "translation"] as const).map((t) => (
              <label key={t} className="check">
                <input
                  type="checkbox"
                  checked={texts.includes(t)}
                  onChange={() =>
                    setTexts((cur) =>
                      (["original", "translation"] as const).filter((x) =>
                        x === t ? !cur.includes(t) : cur.includes(x),
                      ),
                    )
                  }
                />
                <span>{S.result[t]}</span>
              </label>
            ))}
          </fieldset>
          <fieldset className="choice">
            <legend>
              {S.result.formats}
              <Info text={S.infos.formats} />
            </legend>
            {settings.formats.map((f) => (
              <label key={f} className="check">
                <input
                  type="checkbox"
                  checked={chosenFormats.includes(f)}
                  onChange={() => setFormats(toggle(chosenFormats, f))}
                />
                <span>{S.format[f] ?? f}</span>
              </label>
            ))}
          </fieldset>
        </div>
      )}
      {conflict !== null && (
        <div className="msg msg-warn" role="alert">
          <span>{S.result.exists(conflict)}</span>
          <span className="inline-actions">
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => save(true)}
              disabled={saving}
              data-tip={S.tips.replace}
            >
              {S.result.replace}
            </button>
            <button
              type="button"
              className="btn btn-quiet"
              onClick={() => setConflict(null)}
              data-tip={S.tips.keepFiles}
            >
              {S.result.keep}
            </button>
          </span>
        </div>
      )}
      {saved && (
        <p className="msg msg-ok" role="status">
          {S.result.saved(saved.n, saved.folder)}
        </p>
      )}
      {skipped.length > 0 && (
        <p className="msg msg-note">
          {S.result.skipped(
            skipped
              .map((id) => {
                const p = settings.presets.find((x) => x.id === id);
                return p ? presetText(p).name : id;
              })
              .join(", "),
          )}
        </p>
      )}
      {error && (
        <p className="msg msg-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
