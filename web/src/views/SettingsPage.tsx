import { useEffect, useState } from "react";

import { api, ApiError } from "../api/client";
import type { MachineInfo, ModelsInfo, Settings, SettingsView } from "../api/contract.gen";
import { inOrder } from "../state/order";
import { presetText, s } from "../strings";
import { Info } from "./Info";
import { MachineLine } from "./MachineLine";
import { infoOf, TypesEditor } from "./TypesEditor";

interface Props {
  view: SettingsView | null;
  loadError: string | null;
  onSaved: (v: SettingsView) => void;
  machine?: MachineInfo | null;
  onDirty?: (dirty: boolean) => void; // tells the window whether leaving would lose changes
  cardPack?: ModelsInfo | null;
}

const toggle = (list: string[], item: string) =>
  list.includes(item) ? list.filter((x) => x !== item) : [...list, item];

export function SettingsPage({ view, loadError, onSaved, machine = null, onDirty, cardPack = null }: Props) {
  const S = s();
  const [draft, setDraft] = useState<Settings | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  // changes count only once saved: the window asks before they are left behind
  const unsaved =
    view !== null &&
    draft !== null &&
    JSON.stringify({ ...draft, language: view.settings.language, look: view.settings.look }) !==
      JSON.stringify(view.settings);
  useEffect(() => onDirty?.(unsaved), [unsaved, onDirty]);
  useEffect(() => () => onDirty?.(false), [onDirty]);

  if (!view) {
    return loadError ? (
      <p className="msg msg-error" role="alert">
        {loadError}
      </p>
    ) : null;
  }
  // language and look are switched in the header and saved at once, so a draft always takes the current ones
  const cur = draft ? { ...draft, language: view.settings.language, look: view.settings.look } : view.settings;
  const onlyEngine = view.engines.length === 1 ? view.engines[0] : undefined; // one engine: shown, not offered
  const dirty = draft !== null && JSON.stringify(cur) !== JSON.stringify(view.settings);
  const change = (patch: Partial<Settings>) => {
    setMessage(null);
    setDraft({ ...cur, ...patch });
  };
  const folderShown = cur.output_dir ?? view.output_dir_default;
  // the types a save may tick: built-in ones and the user's own as they are in this draft (not the removed ones)
  const offered = inOrder(
    [...view.presets.filter((p) => p.kind === "builtin"), ...(cur.custom_presets ?? []).map((p) => infoOf(p))],
    cur.type_order ?? [],
  );
  const builtinCount = view.glossary_builtin.split("\n").filter((l) => l.trim() !== "").length;

  const pickFolder = async () => {
    try {
      const { paths } = await api.dialog("folder");
      if (paths[0]) change({ output_dir: paths[0] });
    } catch (e) {
      setMessage({
        ok: false,
        text: e instanceof ApiError ? e.message : S.error.unknown,
      });
    }
  };

  const save = async () => {
    setSaving(true);
    try {
      const v = await api.saveSettings(cur);
      onSaved(v);
      setDraft(null);
      setMessage({ ok: true, text: S.settings.saved });
    } catch (e) {
      setMessage({
        ok: false,
        text: e instanceof ApiError ? e.message : S.error.unknown,
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <form
      className="settings"
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      <h1 className="page-title">{S.settings.title}</h1>

      <fieldset className="card">
        <legend>
          {S.settings.engine}
          <Info text={S.infos.engine} />
        </legend>
        {onlyEngine !== undefined ? (
          <p>{S.settings.engineNames[onlyEngine] ?? onlyEngine}</p>
        ) : (
          <>
            {view.engines.map((name) => (
              <label key={name} className="radio">
                <input
                  type="radio"
                  name="engine"
                  checked={cur.engine === name}
                  onChange={() => change({ engine: name })}
                />
                <span>{S.settings.engineNames[name] ?? name}</span>
              </label>
            ))}
            <p className="field-note">{S.settings.engineNote}</p>
          </>
        )}
      </fieldset>

      <TypesEditor view={view} cur={cur} change={change} />

      <fieldset className="card">
        <legend>{S.settings.defaults}</legend>
        <div className="split">
          <div>
            <h3 className="sub">
              {S.result.presets}
              <Info text={S.infos.presets} />
            </h3>
            {offered.map((p) => (
              <label key={p.id} className="check check-block">
                <input
                  type="checkbox"
                  checked={cur.export_presets.includes(p.id)}
                  onChange={() => change({ export_presets: toggle(cur.export_presets, p.id) })}
                />
                <span>
                  <span className="check-title">{presetText(p).name}</span>
                  <span className="check-desc">{presetText(p).description}</span>
                </span>
              </label>
            ))}
          </div>
          <div>
            <h3 className="sub">
              {S.result.formats}
              <Info text={S.infos.formats} />
            </h3>
            {view.formats.map((f) => (
              <label key={f} className="check check-block">
                <input
                  type="checkbox"
                  checked={cur.export_formats.includes(f)}
                  onChange={() => change({ export_formats: toggle(cur.export_formats, f) })}
                />
                <span className="check-title">{S.format[f] ?? f}</span>
              </label>
            ))}
          </div>
        </div>
      </fieldset>

      <fieldset className="card">
        <legend>
          {S.settings.output}
          <Info text={S.infos.output} />
        </legend>
        <label className="radio">
          <input
            type="radio"
            name="output"
            checked={cur.output === "beside_source"}
            onChange={() => change({ output: "beside_source" })}
          />
          <span>{S.settings.besideSource}</span>
        </label>
        <label className="radio">
          <input
            type="radio"
            name="output"
            checked={cur.output === "folder"}
            onChange={() => change({ output: "folder" })}
          />
          <span>{S.settings.inFolder}</span>
        </label>
        <div className="folder">
          <span className="folder-label">{S.settings.folder}</span>
          <code className="folder-path" title={folderShown}>
            {folderShown}
          </code>
          <span className="inline-actions">
            <button type="button" className="btn" onClick={pickFolder} data-tip={S.tips.chooseFolder}>
              {S.settings.choose}
            </button>
            {cur.output_dir !== null && (
              <button
                type="button"
                className="btn btn-quiet"
                onClick={() => change({ output_dir: null })}
                data-tip={S.tips.resetFolder}
              >
                {S.settings.reset}
              </button>
            )}
          </span>
        </div>
        <p className="field-note">{S.settings.folderNote}</p>
      </fieldset>

      <fieldset className="card">
        <legend>
          {S.glossary.title}
          <Info text={S.glossary.info} />
        </legend>
        {builtinCount > 0 && (
          <details className="glossary-builtin">
            <summary>{S.glossary.builtin(builtinCount)}</summary>
            <pre className="glossary-list">{view.glossary_builtin}</pre>
          </details>
        )}
        <label className="sub" htmlFor="glossary-own">
          {S.glossary.own}
        </label>
        <textarea
          id="glossary-own"
          className="glossary-input"
          rows={8}
          spellCheck={false}
          placeholder={S.glossary.placeholder}
          value={cur.glossary_text}
          onChange={(e) => change({ glossary_text: e.target.value })}
        />
        <p className="field-note">{S.glossary.hint}</p>
      </fieldset>

      {machine && (
        <fieldset className="card">
          <legend>{S.machine.title}</legend>
          <MachineLine machine={machine} detail cardPack={cardPack} />
        </fieldset>
      )}

      <fieldset className="card">
        <legend>{S.settings.privacy}</legend>
        <p className="field-note">{S.settings.encrypted(view.account)}</p>
      </fieldset>

      <div className={`form-actions${dirty ? " form-actions-unsaved" : ""}`}>
        <button type="submit" className="btn btn-primary" disabled={saving || !dirty} data-tip={S.tips.saveSettings}>
          {saving ? S.settings.saving : S.settings.save}
        </button>
        {dirty && <span className="field-note unsaved-note">{S.settings.unsaved}</span>}
        {message && (
          <span className={`msg ${message.ok ? "msg-ok" : "msg-error"}`} role={message.ok ? "status" : "alert"}>
            {message.text}
          </span>
        )}
      </div>
    </form>
  );
}
