// The list of every subtitle. Rows have one fixed height, so only the rows in view are rendered:
// 2000 subtitles scroll as lightly as 20.
import { memo, useEffect, useRef, useState } from "react";

import type { Cue } from "../api/contract.gen";
import { stamp } from "../editor/timecode";
import { s } from "../strings";
import { place } from "./place";

export const ROW_PX = 44;
const EXTRA_ROWS = 8;

interface Props {
  cues: Cue[];
  selectedId: number | null;
  flagged: ReadonlyMap<number, "hard" | "soft">;
  cps: (c: Cue) => number;
  maxCps: number;
  onSelect: (id: number) => void;
  onTranslate?: (id: number) => void; // a click on a row's translation: choose the cue and go to its translation
  showTranslation?: boolean; // the translation column (off: the list has the English text only)
}

export const CueList = memo(function CueList({
  cues,
  selectedId,
  flagged,
  cps,
  maxCps,
  onSelect,
  onTranslate,
  showTranslation = true,
}: Props) {
  const S = s();
  const box = useRef<HTMLDivElement>(null);
  const [top, setTop] = useState(0);
  const [height, setHeight] = useState(400);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const resize = new ResizeObserver(() => setHeight(el.clientHeight));
    resize.observe(el);
    return () => resize.disconnect();
  }, []);

  // keep the selected row in view when it is chosen elsewhere (waveform, keys)
  const selIndex = cues.findIndex((c) => c.id === selectedId);
  useEffect(() => {
    const el = box.current;
    if (!el || selIndex < 0) return;
    const y = selIndex * ROW_PX;
    if (y < el.scrollTop) el.scrollTop = y;
    else if (y + ROW_PX > el.scrollTop + el.clientHeight) el.scrollTop = y + ROW_PX - el.clientHeight;
  }, [selIndex]);

  const first = Math.max(0, Math.floor(top / ROW_PX) - EXTRA_ROWS);
  const last = Math.min(cues.length, Math.ceil((top + height) / ROW_PX) + EXTRA_ROWS);
  const rows = [];
  for (let i = first; i < last; i++) {
    const c = cues[i]!;
    const flag = flagged.get(c.id);
    const rate = cps(c);
    rows.push(
      <div
        key={c.id}
        role="option"
        aria-selected={c.id === selectedId}
        data-cue={c.id}
        className={`cue-row${flag ? ` cue-row-${flag}` : ""}${showTranslation ? "" : " cue-row-no-tr"}`}
        ref={place({ top: i * ROW_PX })}
        onClick={() => onSelect(c.id)}
      >
        <span className="cue-n">{i + 1}</span>
        <span className="cue-time">
          {stamp(c.start_ms)}
          <br />
          {stamp(c.end_ms)}
        </span>
        <span className={`cue-cps${rate > maxCps ? " cue-cps-over" : ""}`}>{S.editor.cps(rate)}</span>
        <span className="cue-lines">{c.lines.join(" / ") || "—"}</span>
        {showTranslation && (
          <span
            className={`cue-tr${c.translation ? "" : " cue-tr-empty"}`}
            onClick={(e) => {
              if (!onTranslate) return;
              e.stopPropagation();
              onTranslate(c.id);
            }}
          >
            {c.translation ? c.translation.join(" / ") : S.editor.translationColumn}
          </span>
        )}
        <span className="cue-flag" aria-label={flag ? S.editor[flag] : undefined}>
          {flag === "hard" ? "●" : flag === "soft" ? "○" : ""}
        </span>
      </div>,
    );
  }

  return (
    <div
      ref={box}
      className="cue-list"
      role="listbox"
      aria-label={S.editor.list}
      tabIndex={0}
      onScroll={(e) => setTop(e.currentTarget.scrollTop)}
    >
      <div className="cue-list-inner" ref={place({ height: cues.length * ROW_PX })}>
        {rows}
      </div>
    </div>
  );
});
