import { useCallback, useEffect, useRef, useState } from "react";

import { api, ApiError } from "./api/client";
import type { SettingsView } from "./api/contract.gen";
import { useLive } from "./api/live";
import { apply, type Look, type Prefs, remember, remembered } from "./state/prefs";
import { busy } from "./state/queue";
import { s } from "./strings";
import { EditorPage } from "./views/EditorPage";
import { HelpPage } from "./views/HelpPage";
import { ModelPanel } from "./views/ModelPanel";
import { QueuePage } from "./views/QueuePage";
import { SettingsPage } from "./views/SettingsPage";
import { Tips } from "./views/Tips";

type View = "queue" | "settings" | "help" | "edit";
const viewOf = (hash: string): View =>
  hash === "#settings" ? "settings" : hash === "#help" ? "help" : hash.startsWith("#edit/") ? "edit" : "queue";
/** #edit/<job id>/<preset id> */
const editTarget = (hash: string) => {
  const [, job = "", preset = ""] = hash.split("/");
  return { job, preset };
};

function LookIcon({ look }: { look: Look }) {
  return (
    <svg className="look-icon" viewBox="0 0 24 24" aria-hidden="true">
      {look === "light" ? (
        <>
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2.5v2.5M12 19v2.5M2.5 12H5M19 12h2.5M5.3 5.3l1.8 1.8M16.9 16.9l1.8 1.8M5.3 18.7l1.8-1.8M16.9 7.1l1.8-1.8" />
        </>
      ) : (
        <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" />
      )}
    </svg>
  );
}

declare global {
  interface Window {
    __pstt?: Record<string, unknown>; // read by checks and tests; never by the page itself
  }
}

export function App() {
  const live = useLive();
  const [view, setView] = useState<View>(viewOf(location.hash));
  const [hash, setHash] = useState(location.hash);
  // unsaved changes in Settings: leaving asks first (a "stay" keeps the page, and its changes, as it was)
  const [settingsDirty, setSettingsDirty] = useState(false);
  const [settingsKey, setSettingsKey] = useState(0); // a new key = the Settings page afresh, its draft dropped
  const dirtyRef = useRef(false);
  useEffect(() => {
    dirtyRef.current = settingsDirty;
  }, [settingsDirty]);
  const shownHash = useRef(location.hash);
  const [settings, setSettings] = useState<SettingsView | null>(null);
  const [settingsError, setSettingsError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [prefs, setPrefs] = useState<Prefs>(remembered);
  const latest = useRef(prefs); // two clicks before the next render must not undo each other
  const S = s();
  const show = useCallback((next: Prefs) => {
    latest.current = next;
    apply(next);
    setPrefs(next);
    remember(next);
  }, []);

  const loadSettings = useCallback(() => {
    api.settings().then(
      (v) => {
        setSettings(v);
        setSettingsError(null);
        show({ lang: v.settings.language, look: v.settings.look });
      },
      (e: unknown) => setSettingsError(e instanceof ApiError ? e.message : s().error.unknown),
    );
  }, [show]);

  // again after every server start: the settings file may have been changed or repaired meanwhile
  const bootId = live.queue.bootId;
  useEffect(() => {
    if (bootId !== null) loadSettings();
  }, [bootId, loadSettings]);

  const changePrefs = (patch: Partial<Prefs>) => {
    const next = { ...latest.current, ...patch };
    show(next);
    if (settings) {
      // if saving fails the window still shows the choice; it is saved with the next settings change
      api
        .saveSettings({
          ...settings.settings,
          language: next.lang,
          look: next.look,
        })
        .then(setSettings, () => undefined);
    }
  };
  // the header's choices are always the current ones, even while a save of them is on its way
  const shown = settings && {
    ...settings,
    settings: { ...settings.settings, language: prefs.lang, look: prefs.look },
  };

  useEffect(() => {
    const onHash = () => {
      const leaving = viewOf(shownHash.current) === "settings" && viewOf(location.hash) !== "settings";
      if (leaving && dirtyRef.current) {
        if (!window.confirm(s().settings.leaveAsk)) {
          history.replaceState(null, "", shownHash.current); // back to Settings, without a page change
          return;
        }
        // "leave without saving": the unsaved changes go, as the question says (a fresh page, from what is saved)
        setSettingsKey((k) => k + 1);
        setSettingsDirty(false);
      }
      shownHash.current = location.hash;
      setView(viewOf(location.hash));
      setHash(location.hash);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const working = busy(live.queue) || uploading || settingsDirty;
  useEffect(() => {
    if (!working) return;
    const ask = (e: BeforeUnloadEvent) => {
      e.preventDefault(); // the browser shows its own "Leave?" question
    };
    window.addEventListener("beforeunload", ask);
    return () => window.removeEventListener("beforeunload", ask);
  }, [working]);

  const startFresh = async () => {
    if (!window.confirm(S.locked.confirm)) return;
    try {
      await api.startFresh();
      location.reload(); // everything is read again from the new, empty store
    } catch (e) {
      window.alert(e instanceof ApiError ? e.message : S.error.unknown);
    }
  };

  useEffect(() => {
    window.__pstt = {
      ...window.__pstt,
      queue: live.queue,
      online: live.online,
      settings,
      view,
      uploading,
      ...prefs,
    };
  });

  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <span className="mark" aria-hidden="true" />
          <span className="brand-name">{S.appName}</span>
        </div>
        <nav className="tabs" aria-label={S.nav.pages}>
          <a href="#queue" className="tab" aria-current={view === "queue" ? "page" : undefined}>
            {S.nav.queue}
          </a>
          <a href="#settings" className="tab" aria-current={view === "settings" ? "page" : undefined}>
            {S.nav.settings}
          </a>
          <a href="#help" className="tab" aria-current={view === "help" ? "page" : undefined}>
            {S.nav.help}
          </a>
        </nav>
        <div className="switches">
          <div className="switch" role="group" aria-label={S.look.label}>
            {(["light", "dark"] as const).map((l) => (
              <button
                key={l}
                type="button"
                className="switch-btn switch-icon"
                aria-label={S.look[l]}
                data-tip={S.tips.look}
                aria-pressed={prefs.look === l}
                onClick={() => changePrefs({ look: l })}
              >
                <LookIcon look={l} />
              </button>
            ))}
          </div>
          <div className="switch" role="group" aria-label={S.lang.label}>
            {(["cs", "en"] as const).map((l) => (
              <button
                key={l}
                type="button"
                className="switch-btn"
                lang={l}
                aria-pressed={prefs.lang === l}
                onClick={() => changePrefs({ lang: l })}
                data-tip={S.tips.lang}
              >
                {l === "cs" ? "CZ" : "EN"}
              </button>
            ))}
          </div>
        </div>
      </header>
      {!live.online && (
        <div className="banner banner-warn" role="status">
          {S.conn.lost}
        </div>
      )}
      {live.online && live.queue.locked && (
        <div className="banner banner-warn banner-action" role="alert">
          <span>{S.locked.text}</span>
          <button type="button" className="btn" onClick={() => void startFresh()} data-tip={S.tips.startFresh}>
            {S.locked.startFresh}
          </button>
        </div>
      )}
      {live.online && live.queue.pausedReason && (
        <div className="banner banner-info" role="status">
          {S.paused(live.queue.pausedReason, live.queue.pausedKind)}
        </div>
      )}
      <main className="main">
        {live.online && (view === "queue" || view === "settings") && <ModelPanel models={live.queue.models} />}
        {/* both stay mounted: leaving the queue page must not stop a copy in progress */}
        <div hidden={view !== "queue"}>
          <QueuePage
            queue={live.queue}
            settings={shown}
            settingsError={settingsError}
            onUploadingChange={setUploading}
          />
        </div>
        {view === "edit" && (
          <EditorPage
            key={hash}
            job={live.queue.jobs.find((j) => j.id === editTarget(hash).job)}
            known={live.queue.bootId !== null}
            presetId={editTarget(hash).preset}
            settings={shown}
          />
        )}
        <div hidden={view !== "settings"}>
          <SettingsPage
            key={settingsKey}
            view={shown}
            loadError={settingsError}
            onSaved={setSettings}
            machine={live.queue.machine}
            cardPack={live.queue.cardPack}
            whisperModel={live.queue.whisperModel}
            onDirty={setSettingsDirty}
          />
        </div>
        {view === "help" && <HelpPage version={live.queue.appVersion} />}
      </main>
      <Tips />
    </div>
  );
}
