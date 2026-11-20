// The subtitle editor for one finished job and one preset: player, waveform with cues, the selected cue's
// text and times, the rule checker's warnings and the full list. Every change goes through useEditor
// (undo, live check, autosave); this file only lays it out and maps keys and buttons to operations.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { peaks as loadPeaks } from "../api/client";
import type { Cue, Job, SettingsView, Violation } from "../api/contract.gen";
import {
  cueAt,
  type Cues,
  insert,
  mergeNext,
  nudge,
  readingSpeed,
  remove,
  hasTranslation,
  setLanguage,
  setText,
  setTranslation,
  translationOf,
  setTimes,
  splitAt,
  textOf,
  typedEdge,
} from "../editor/ops";
import { parseStamp, stamp } from "../editor/timecode";
import { useEditor } from "../editor/useEditor";
import { presetText, s } from "../strings";
import { CueList } from "./CueList";
import { Waveform, ZOOMS } from "./Waveform";

interface Props {
  job: Job | undefined;
  known: boolean; // the job list has arrived (until then a missing job is not an error)
  presetId: string;
  settings: SettingsView | null;
}

const JUMP_S = 10; // the back / forward buttons and the ← → keys
const SHOW_TR_KEY = "talksubs.showTranslation";

function rememberedShowTranslation(): boolean {
  try {
    return localStorage.getItem(SHOW_TR_KEY) !== "no";
  } catch {
    return true;
  }
}

function rememberShowTranslation(on: boolean): void {
  try {
    localStorage.setItem(SHOW_TR_KEY, on ? "yes" : "no");
  } catch {
    /* a private window or blocked storage: the choice lasts until the editor closes */
  }
}

/** Seek the editor's player (outside the component: the player element is not React state to change). */
function seek(m: HTMLMediaElement, ms: number) {
  m.currentTime = ms / 1000;
}

/** Inputs that take no text: a click leaves the focus on them, and the editor's keys must still work then. */
const NOT_TEXT = new Set(["button", "checkbox", "radio", "range", "submit", "reset", "color", "file"]);
const typing = (el: EventTarget | null) =>
  el instanceof HTMLTextAreaElement || (el instanceof HTMLInputElement && !NOT_TEXT.has(el.type));
/** A focused control (dropdown, button, link) keeps its own Space, Enter and arrow keys. */
const control = (el: EventTarget | null) => el instanceof Element && !!el.closest("select, button, a, summary");

/** A subtitle's start or end, shown as a time that can also be typed: Enter or leaving the field applies it, Escape
 * or a time the subtitle cannot take puts the old one back (marked for a moment). */
function TimeField({ ms, label, onSet }: { ms: number; label: string; onSet: (ms: number) => boolean }) {
  const [draft, setDraftState] = useState<string | null>(null);
  const [refused, setRefused] = useState(false);
  // read by the blur handler, which runs before React has rendered a change made in the same key press
  const typed = useRef<string | null>(null);
  const setDraft = (v: string | null) => {
    typed.current = v;
    setDraftState(v);
  };
  const apply = () => {
    const text = typed.current;
    setDraft(null); // so the blur that follows Enter or Escape finds nothing more to do
    if (text === null) return;
    const t = parseStamp(text);
    if (t === null || t === ms) {
      setRefused(t === null);
      return;
    }
    setRefused(!onSet(t));
  };
  useEffect(() => {
    if (!refused) return;
    const t = window.setTimeout(() => setRefused(false), 1500);
    return () => window.clearTimeout(t);
  }, [refused]);
  return (
    <input
      className={`ed-time-value ed-time-input${refused ? " ed-time-refused" : ""}`}
      value={draft ?? stamp(ms)}
      aria-label={label}
      aria-invalid={refused}
      data-tip={s().tips.time}
      inputMode="decimal"
      spellCheck={false}
      onFocus={(e) => e.currentTarget.select()}
      onChange={(e) => setDraft(e.currentTarget.value)}
      onBlur={apply}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          apply();
          e.currentTarget.blur();
        } else if (e.key === "Escape") {
          e.preventDefault();
          setDraft(null);
          e.currentTarget.blur();
        }
      }}
    />
  );
}

/** Where the recording is now, read live from the player (smooth while playing, exact after a seek). */
function PlayTime({ media, label }: { media: HTMLMediaElement | null; label: string }) {
  const [ms, setMs] = useState(0);
  useEffect(() => {
    if (!media) return;
    let frame = 0;
    const read = () => setMs(Math.round(media.currentTime * 1000));
    const tick = () => {
      read();
      frame = requestAnimationFrame(tick);
    };
    const start = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(tick);
    };
    const stop = () => {
      cancelAnimationFrame(frame);
      read();
    };
    const events: [string, () => void][] = [
      ["play", start],
      ["pause", stop],
      ["ended", stop],
      ["seeked", read],
      ["timeupdate", read],
      ["loadedmetadata", read],
    ];
    for (const [name, fn] of events) media.addEventListener(name, fn);
    read();
    if (!media.paused) start();
    return () => {
      cancelAnimationFrame(frame);
      for (const [name, fn] of events) media.removeEventListener(name, fn);
    };
  }, [media]);
  return (
    <span className="ed-now" data-tip={label} aria-label={label}>
      {stamp(ms)}
    </span>
  );
}

function setVolume(m: HTMLMediaElement, level: number) {
  m.volume = level;
}

/** The loudness of a sound-only recording, whose own player is not shown. */
function Volume({ media, label }: { media: HTMLMediaElement; label: string }) {
  const [level, setLevel] = useState(media.volume);
  return (
    <label className="ed-volume" data-tip={label}>
      <span aria-hidden="true">🔈</span>
      <input
        type="range"
        min={0}
        max={1}
        step={0.05}
        value={level}
        aria-label={label}
        onChange={(e) => {
          const v = Number(e.currentTarget.value);
          setVolume(media, v);
          setLevel(v);
        }}
      />
    </label>
  );
}

export function EditorPage({ job, known, presetId, settings }: Props) {
  const S = s();
  const jobId = job?.id ?? "";
  const ed = useEditor(jobId, presetId);
  const { load, hist, verdict, save, apply } = ed;
  const [selected, setSelected] = useState<number | null>(null);
  const [media, setMedia] = useState<HTMLMediaElement | null>(null);
  const [peaks, setPeaks] = useState<Int8Array | null>(null);
  const [zoom, setZoom] = useState<number>(50);
  const [playing, setPlaying] = useState(false);
  const [showTranslation, setShowTranslation] = useState(rememberedShowTranslation);
  const text = useRef<HTMLTextAreaElement>(null);

  const previewReady = job?.preview?.state === "ready";
  useEffect(() => {
    if (!previewReady || !jobId) return;
    let gone = false;
    void loadPeaks(jobId).then((p) => !gone && setPeaks(p));
    return () => {
      gone = true;
    };
  }, [previewReady, jobId]);

  const cues = hist?.present.cues ?? [];
  // the translation's language as typed; it reaches the track once it is a real code (2-3 letters)
  const storedLang = hist?.present.lang ?? "cs"; // the same default as the server's (subtitles.py)
  const [langDraft, setLangDraft] = useState(storedLang);
  const [langOf, setLangOf] = useState(storedLang);
  if (langOf !== storedLang) {
    setLangOf(storedLang);
    setLangDraft(storedLang);
  }
  const ready = load.kind === "ready" ? load : null;
  const grid = ready?.grid;
  const fresh = verdict.cues === cues; // the warnings were computed for exactly what is shown

  const byCue = useMemo(() => {
    const m = new Map<number, Violation[]>();
    for (const v of verdict.list) m.set(v.cue_id, [...(m.get(v.cue_id) ?? []), v]);
    return m;
  }, [verdict]);
  const flagged = useMemo(() => {
    const m = new Map<number, "hard" | "soft">();
    for (const v of verdict.list) if (m.get(v.cue_id) !== "hard") m.set(v.cue_id, v.severity);
    return m;
  }, [verdict]);
  const counts = useMemo(() => {
    let hard = 0;
    for (const v of verdict.list) if (v.severity === "hard") hard++;
    return { hard, soft: verdict.list.length - hard };
  }, [verdict]);

  const sel = cues.find((c) => c.id === selected) ?? null;
  const selIndex = sel ? cues.indexOf(sel) : -1;

  const now = () => Math.round((media?.currentTime ?? 0) * 1000);
  const select = useCallback(
    (id: number, andSeek = true) => {
      setSelected(id);
      const c = hist?.present.cues.find((x) => x.id === id);
      if (c && media && andSeek && media.paused) seek(media, c.start_ms);
    },
    [hist, media],
  );

  const run = (change: (c: Cues) => [Cues, number | null] | Cues, tag: string | null = null) => {
    let made: number | null = null;
    apply((c) => {
      const r = change(c);
      if (Array.isArray(r)) {
        made = r[1];
        return r[0];
      }
      return r;
    }, tag);
    if (made !== null) setSelected(made);
  };

  const ops = grid && {
    split: (caret: number | undefined) => {
      if (!sel || caret === undefined || !ready) return;
      run((c) => splitAt(c, sel.id, caret, ready.words, grid));
    },
    merge: () => sel && run((c) => mergeNext(c, sel.id)),
    insert: () => run((c) => insert(c, media ? now() : sel ? sel.end_ms : 0, grid)),
    remove: () => {
      if (!sel) return;
      const near = cues[selIndex + 1] ?? cues[selIndex - 1];
      run((c) => remove(c, sel.id));
      setSelected(near ? near.id : null); // the selection moves on to the neighbour
    },
    nudge: (edge: "start" | "end", frames: number) => sel && run((c) => nudge(c, sel.id, edge, frames, grid)),
    times: (id: number, st: number, en: number) => run((c) => setTimes(c, id, st, en, grid)),
    // a time typed for one edge; refused (false) unless it lands where typed, on the frame grid, and the other edge
    // stays put: never silently clamped to the recording or pushed past the other edge
    edge: (edge: "start" | "end", ms: number): boolean => {
      if (!sel) return false;
      const t = typedEdge(sel, edge, ms, grid);
      if (!t) return false;
      run((c) => setTimes(c, sel.id, t[0], t[1], grid));
      return true;
    },
  };

  const step = (by: number) => {
    if (!cues.length) return;
    const i = selIndex < 0 ? cueAt(cues, now()) : selIndex + by;
    const c = cues[Math.min(Math.max(0, i), cues.length - 1)];
    if (c) select(c.id);
  };
  const togglePlay = () => {
    if (!media) return;
    if (media.paused) void media.play().catch(() => undefined);
    else media.pause();
  };
  const stop = () => {
    if (!media) return;
    media.pause();
    seek(media, 0);
  };
  const jump = (seconds: number) => {
    if (!media) return;
    const now = media.currentTime;
    const end = Number.isFinite(media.duration) ? media.duration : now + Math.max(seconds, 0);
    seek(media, Math.min(Math.max(0, now + seconds), end) * 1000);
  };
  const zoomBy = (by: number) => {
    const i = ZOOMS.indexOf(zoom as (typeof ZOOMS)[number]);
    setZoom(ZOOMS[Math.min(Math.max(0, i + by), ZOOMS.length - 1)]!);
  };
  const nextProblem = () => {
    const ids = new Set(flagged.keys());
    const after = cues.slice(selIndex + 1).find((c) => ids.has(c.id)) ?? cues.find((c) => ids.has(c.id));
    if (after) select(after.id);
  };

  // the latest handlers for the one key listener
  const keys = useRef<(e: KeyboardEvent) => void>(() => undefined);
  const onKey = (e: KeyboardEvent) => {
    if (!ops) return;
    const inText = typing(e.target);
    const free = !inText && !control(e.target); // the page itself has the keys
    const ctrl = e.ctrlKey || e.metaKey;
    const k = e.key.toLowerCase();
    let done = true;
    if (ctrl && !e.shiftKey && k === "z") ed.undo();
    else if (ctrl && (k === "y" || (e.shiftKey && k === "z"))) ed.redo();
    else if (ctrl && k === "s") void ed.saveNow();
    // a split is made at the English text's cursor: never from the translation box or the language field
    else if (ctrl && e.key === "Enter" && e.target === text.current) ops.split(text.current?.selectionStart);
    else if (
      ctrl &&
      e.key === "Enter" &&
      !(e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLInputElement)
    )
      ops.split(text.current?.selectionStart);
    else if (ctrl && k === "j") ops.merge();
    else if (ctrl && k === "i") ops.insert();
    else if ((e.altKey || free) && (e.code === "BracketLeft" || e.code === "BracketRight") && !ctrl)
      ops.nudge(e.shiftKey ? "end" : "start", e.code === "BracketLeft" ? -1 : 1);
    else if ((e.altKey || free) && (e.key === "ArrowUp" || e.key === "ArrowDown") && !ctrl)
      step(e.key === "ArrowUp" ? -1 : 1);
    else if (inText && e.key === "Escape") (e.target as HTMLElement).blur();
    else if (inText || control(e.target)) done = false;
    else if (e.key === " ") togglePlay();
    else if (e.key === "ArrowLeft" || e.key === "ArrowRight") jump(e.key === "ArrowLeft" ? -JUMP_S : JUMP_S);
    else if (e.key === "Enter" && sel) text.current?.focus();
    else if (e.key === "Delete") ops.remove();
    else if (e.key === "+" || e.key === "=") zoomBy(1);
    else if (e.key === "-") zoomBy(-1);
    else done = false;
    if (done) e.preventDefault();
  };
  useEffect(() => {
    keys.current = onKey;
  });
  useEffect(() => {
    const on = (e: KeyboardEvent) => keys.current(e);
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, []);

  // unsaved changes: the window asks before it closes (the autosave needs a moment)
  const unsaved = save.kind !== "saved" && save.kind !== "stale";
  useEffect(() => {
    if (!unsaved) return;
    const ask = (e: BeforeUnloadEvent) => {
      void ed.saveNow();
      e.preventDefault();
    };
    window.addEventListener("beforeunload", ask);
    return () => window.removeEventListener("beforeunload", ask);
  }, [unsaved, ed]);

  useEffect(() => {
    window.__pstt = {
      ...window.__pstt,
      editor: {
        jobId,
        presetId,
        cues,
        verdict,
        fresh,
        save,
        selected,
        zoom,
        undo: hist?.past.length ?? 0,
        redo: hist?.future.length ?? 0,
      },
    };
  });

  if (!job)
    return (
      <p className="msg msg-note">
        {known ? S.editor.gone : S.editor.loading} <a href="#queue">{S.editor.back}</a>
      </p>
    );
  const presets = settings?.presets ?? [];
  const countsSpaces = ready?.track.preset.limits.cps_counts_spaces ?? true;
  const maxCps = ready?.track.preset.limits.max_cps ?? Infinity;
  const cpsOf = (c: Cue) => readingSpeed(c, countsSpaces);
  const maxChars = ready?.track.preset.limits.max_chars_per_line ?? Infinity;
  // the translation's own length and reading speed, measured by the type's limits (the rule checker judges the
  // English cues; a translation is only measured here)
  const trStats = (c: Cue) => {
    const lines = c.translation ?? [];
    if (!lines.length) return "";
    const longest = Math.max(...lines.map((l) => l.length));
    const rate = readingSpeed(c, countsSpaces, lines);
    return S.editor.translationStats(longest, maxChars, rate, maxCps);
  };
  // a measure shown in the colour of what the rule checker says about it (red: must fix, yellow: worth a look)
  // (while a check runs the last verdict stays, so the colours do not blink at every key)
  const statClass = (c: Cue, ...rules: Violation["rule"][]) => {
    const found = (byCue.get(c.id) ?? []).filter((v) => rules.includes(v.rule));
    if (!found.length) return "";
    return found.some((v) => v.severity === "hard") ? " ed-stat-hard" : " ed-stat-soft";
  };
  // a sound-only recording with its waveform needs no player of its own: the play bar and the waveform drive it
  // (without a waveform the browser's player stays, the only way to seek)
  const soundOnWave =
    previewReady &&
    job.preview?.kind === "audio" &&
    media !== null &&
    peaks !== null &&
    peaks.length >= 2 &&
    !!job.preview.peaks_per_s;
  const translated = ready !== null && hist !== null && hasTranslation(hist.present);
  // a translation once typed is always shown, so it can never be forgotten unseen
  const showTr = translated || showTranslation;
  const saveText =
    save.kind === "saved" ? S.editor.saved
    : save.kind === "pending" ? S.editor.pending
    : save.kind === "saving" ? S.editor.saving
    : save.kind === "failed" ? S.editor.failed(save.message)
    : save.message; // prettier-ignore

  return (
    <section className="editor" aria-label={S.editor.title(job.name)}>
      <div className="ed-top">
        <div className="ed-head">
          <a className="btn btn-small" href="#queue">
            ← {S.editor.back}
          </a>
          <div className="ed-heading">
            <h1 className="ed-title">{S.editor.title(job.name)}</h1>
            <span className={`ed-save ed-save-${save.kind}`} role="status">
              {saveText}
              {save.kind === "stale" && (
                <button
                  type="button"
                  className="btn btn-small"
                  onClick={() => location.reload()}
                  data-tip={S.tips.reopen}
                >
                  {S.editor.reopen}
                </button>
              )}
            </span>
          </div>
          <label className="ed-preset">
            <span>{S.editor.preset}</span>
            <select
              value={presetId}
              data-tip={S.tips.preset}
              onChange={(e) => (location.hash = `#edit/${job.id}/${e.target.value}`)}
            >
              {presets.map((p) => (
                <option key={p.id} value={p.id}>
                  {presetText(p).name}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="ed-tools" role="toolbar" aria-label={S.editor.tools}>
          <span className="ed-group">
            <button
              type="button"
              className="btn btn-small"
              onClick={ed.undo}
              data-tip={S.tips.undo}
              disabled={!hist?.past.length}
            >
              {S.editor.undo}
            </button>
            <button
              type="button"
              className="btn btn-small"
              onClick={ed.redo}
              data-tip={S.tips.redo}
              disabled={!hist?.future.length}
            >
              {S.editor.redo}
            </button>
          </span>
          <span className="ed-sep" aria-hidden="true" />
          <label
            className="ed-tr-switch switch"
            data-tip={translated ? S.tips.showTranslationLocked : S.tips.showTranslation}
          >
            <input
              type="checkbox"
              checked={showTr}
              disabled={translated}
              onChange={(e) => {
                setShowTranslation(e.currentTarget.checked);
                rememberShowTranslation(e.currentTarget.checked);
                if (e.nativeEvent instanceof PointerEvent || e.nativeEvent instanceof MouseEvent)
                  e.currentTarget.blur(); // after a click the editor's keys (Space plays) are the page's again
              }}
            />
            <span>{S.editor.showTranslation}</span>
          </label>
          <span className="ed-spacer" />
          <details className="ed-keys">
            <summary className="btn btn-small" data-tip={S.tips.controls}>
              {S.editor.keys}
            </summary>
            <dl className="keys">
              {S.editor.keyList.map(([k, what]) => (
                <div key={k}>
                  <dt>{k}</dt>
                  <dd>{what}</dd>
                </div>
              ))}
            </dl>
          </details>
          <button
            type="button"
            className="btn btn-small btn-quiet ed-remake"
            disabled={!ready || translated}
            data-tip={translated ? S.tips.startOverTranslated : S.tips.startOver}
            onClick={() => {
              if (!window.confirm(S.editor.startOverAsk)) return;
              void ed.resegment().then((err) => err && window.alert(err));
            }}
          >
            {S.editor.startOver}
          </button>
        </div>
      </div>

      {load.kind === "loading" && <p className="msg msg-note">{S.editor.loading}</p>}
      {load.kind === "failed" && <p className="msg msg-error">{load.message}</p>}
      {ready?.track.edited && ready.presetChanged && <p className="msg msg-note">{S.editor.presetChanged}</p>}

      {ready && grid && ops && (
        <>
          <div className={`ed-main${soundOnWave ? " ed-main-audio" : ""}`}>
            <div className="ed-player">
              {previewReady ? (
                job.preview?.kind === "audio" ? (
                  <audio
                    ref={setMedia}
                    className="player player-audio"
                    controls={!soundOnWave}
                    preload="auto"
                    src={`/api/jobs/${job.id}/media`}
                    aria-label={S.preview.audioLabel}
                    onPlay={() => setPlaying(true)}
                    onPause={() => setPlaying(false)}
                  />
                ) : (
                  <video
                    ref={setMedia}
                    className="player"
                    controls
                    preload="auto"
                    src={`/api/jobs/${job.id}/media`}
                    aria-label={S.preview.videoLabel}
                    onPlay={() => setPlaying(true)}
                    onPause={() => setPlaying(false)}
                  />
                )
              ) : (
                <p className="msg msg-note">
                  {job.preview?.state === "failed" ? S.editor.previewFailed : S.editor.noPreview}
                </p>
              )}
            </div>

            <div className="ed-edit">
              {sel ? (
                <>
                  <div className="ed-edit-head">
                    <span className="ed-edit-title">{S.editor.cueOf(selIndex + 1, cues.length)}</span>
                  </div>
                  <div className="ed-times">
                    {(["start", "end"] as const).map((edge) => (
                      <div key={edge} className="ed-time">
                        <span className="ed-time-label">{S.editor[edge]}</span>
                        <button
                          type="button"
                          className="btn btn-small"
                          data-tip={S.editor.earlier}
                          aria-label={`${S.editor[edge]}: ${S.editor.earlier}`}
                          onClick={() => ops.nudge(edge, -1)}
                        >
                          −
                        </button>
                        <TimeField
                          key={`${sel.id}-${edge}`}
                          ms={edge === "start" ? sel.start_ms : sel.end_ms}
                          label={S.editor[edge]}
                          onSet={(t) => ops.edge(edge, t)}
                        />
                        <button
                          type="button"
                          className="btn btn-small"
                          data-tip={S.editor.later}
                          aria-label={`${S.editor[edge]}: ${S.editor.later}`}
                          onClick={() => ops.nudge(edge, 1)}
                        >
                          +
                        </button>
                      </div>
                    ))}
                    <span className="ed-stats">
                      <span className={`ed-stat${statClass(sel, "min_duration", "max_duration")}`}>
                        <span className="ed-time-label">{S.editor.length}</span>{" "}
                        {S.editor.seconds(sel.end_ms - sel.start_ms)}
                      </span>
                      <span className={`ed-stat${statClass(sel, "cps")}`}>
                        <span className="ed-time-label">{S.editor.speed}</span> {S.editor.cps(cpsOf(sel))}
                      </span>
                    </span>
                  </div>
                  <div className="ed-field-head">
                    <label className="ed-tr-label" htmlFor="ed-text">
                      {S.editor.textLabel}
                    </label>
                    <span className="ed-hint">{S.editor.textHint}</span>
                  </div>
                  <textarea
                    ref={text}
                    id="ed-text"
                    className="ed-text"
                    rows={3}
                    value={textOf(sel)}
                    readOnly={save.kind === "stale"}
                    spellCheck={false}
                    onChange={(e) => apply((c) => setText(c, sel.id, e.target.value), `text:${sel.id}`)}
                  />
                  {showTr && (
                    <>
                      <div className="ed-tr-head ed-field-head">
                        <label className="ed-tr-label" htmlFor="ed-translation">
                          {S.editor.translation}
                        </label>
                        <label className="ed-tr-lang" data-tip={S.tips.translationLanguage}>
                          <span>{S.editor.translationLanguage}</span>
                          <input
                            value={langDraft}
                            onBlur={() => setLangDraft(storedLang)}
                            maxLength={3}
                            size={3}
                            spellCheck={false}
                            aria-label={S.editor.translationLanguage}
                            onChange={(e) => {
                              const v = e.currentTarget.value.toLowerCase().replace(/[^a-z]/g, "");
                              setLangDraft(v);
                              if (/^[a-z]{2,3}$/.test(v)) apply((c) => setLanguage(c, v), "lang");
                            }}
                          />
                        </label>
                        <span className="ed-tr-stats">{trStats(sel)}</span>
                      </div>
                      <textarea
                        id="ed-translation"
                        className="ed-text ed-translation"
                        rows={3}
                        value={translationOf(sel)}
                        readOnly={save.kind === "stale"}
                        spellCheck={false}
                        placeholder={S.editor.translationPlaceholder}
                        data-tip={S.tips.translation}
                        onChange={(e) => apply((c) => setTranslation(c, sel.id, e.target.value), `tr:${sel.id}`)}
                      />
                    </>
                  )}
                  <div className="ed-ops">
                    <button
                      type="button"
                      className="btn btn-small"
                      onClick={() => ops.split(text.current?.selectionStart)}
                      data-tip={S.tips.split}
                    >
                      {S.editor.split}
                    </button>
                    <button
                      type="button"
                      className="btn btn-small"
                      onClick={ops.merge}
                      data-tip={S.tips.merge}
                      disabled={selIndex >= cues.length - 1}
                    >
                      {S.editor.merge}
                    </button>
                    <button type="button" className="btn btn-small" onClick={ops.insert} data-tip={S.tips.insert}>
                      {S.editor.insert}
                    </button>
                    <button
                      type="button"
                      className="btn btn-small btn-quiet btn-quiet-danger ed-ops-end"
                      onClick={ops.remove}
                      data-tip={S.tips.remove1}
                    >
                      {S.editor.remove}
                    </button>
                  </div>
                  <ul className={`ed-warnings${fresh ? "" : " ed-stale"}`} aria-live="polite">
                    {(byCue.get(sel.id) ?? []).map((v, i) => (
                      <li key={i} className={`warn warn-${v.severity}`} data-rule={v.rule}>
                        <span className="warn-kind">{S.editor[v.severity]}</span>{" "}
                        {S.editor.rule[v.rule](v.value, v.limit)}
                      </li>
                    ))}
                    {fresh && !byCue.get(sel.id)?.length && <li className="warn warn-none">{S.editor.noProblem}</li>}
                  </ul>
                </>
              ) : (
                <div className="ed-none">
                  <p className="msg msg-note">{S.editor.none}</p>
                  <button type="button" className="btn btn-small" onClick={ops.insert} data-tip={S.tips.insert}>
                    {S.editor.insert}
                  </button>
                </div>
              )}
            </div>
          </div>

          <div className="ed-wave">
            <div className="ed-wave-bar">
              <button
                type="button"
                className="btn btn-small btn-primary ed-play"
                onClick={togglePlay}
                data-tip={S.tips.play}
                disabled={!media}
              >
                {playing ? S.editor.pause : S.editor.play}
              </button>
              <button
                type="button"
                className="btn btn-small"
                onClick={stop}
                disabled={!media}
                data-tip={S.editor.stopTitle}
              >
                {S.editor.stop}
              </button>
              <button
                type="button"
                className="btn btn-small"
                onClick={() => jump(-JUMP_S)}
                disabled={!media}
                data-tip={S.editor.jumpBackTitle}
              >
                {S.editor.jumpBack}
              </button>
              <button
                type="button"
                className="btn btn-small"
                onClick={() => jump(JUMP_S)}
                disabled={!media}
                data-tip={S.editor.jumpForwardTitle}
              >
                {S.editor.jumpForward}
              </button>
              <PlayTime media={media} label={S.editor.now} />
              <span className="ed-sep" aria-hidden="true" />
              <span className="ed-zoom-label">{S.editor.zoom}</span>
              <button
                type="button"
                className="btn btn-small"
                onClick={() => zoomBy(-1)}
                aria-label={S.editor.zoomOut}
                data-tip={S.editor.zoomOut}
              >
                −
              </button>
              <button
                type="button"
                className="btn btn-small"
                onClick={() => zoomBy(1)}
                aria-label={S.editor.zoomIn}
                data-tip={S.editor.zoomIn}
              >
                +
              </button>
              {soundOnWave && <Volume media={media} label={S.editor.volume} />}
              <span className="ed-spacer" />
              <span
                className={`ed-problems${fresh ? "" : " ed-stale"}${fresh && counts.hard ? " ed-problems-hard" : ""}`}
              >
                {fresh ? S.editor.problems(counts.hard, counts.soft) : S.editor.checking}
              </span>
              <button
                type="button"
                className="btn btn-small"
                onClick={nextProblem}
                data-tip={S.tips.nextProblem}
                disabled={!flagged.size}
              >
                {S.editor.nextProblem}
              </button>
              <span className="ed-sep" aria-hidden="true" />
              <span className="ed-count">{S.editor.count(cues.length)}</span>
            </div>
            {media && peaks && peaks.length >= 2 && job.preview?.peaks_per_s ? (
              <Waveform
                media={media}
                peaks={peaks}
                peaksPerS={job.preview.peaks_per_s}
                cues={cues}
                selectedId={selected}
                flagged={flagged}
                grid={grid}
                zoom={zoom}
                onSelect={(id) => select(id, false)}
                onTimes={ops.times}
              />
            ) : null}
          </div>

          <CueList
            cues={cues}
            selectedId={selected}
            flagged={flagged}
            cps={cpsOf}
            maxCps={maxCps}
            onSelect={select}
            showTranslation={showTr}
            onTranslate={(id) => {
              select(id);
              window.setTimeout(() => document.getElementById("ed-translation")?.focus(), 0);
            }}
          />
        </>
      )}
    </section>
  );
}
