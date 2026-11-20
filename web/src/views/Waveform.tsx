// The waveform under the player, with the cues drawn over it. wavesurfer.js draws the waveform, the play
// cursor, scrolling and zoom from the precomputed peaks (it never decodes the media); the cues are ours:
// a layer inside its scrolling wrapper that renders only the cues in view, so 2000 cues cost nothing.
// Dragging measures from where the pointer went down and snaps to the frame grid when it is released.
import { memo, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import WaveSurfer from "wavesurfer.js";

import type { Cue } from "../api/contract.gen";
import { cueAt, type Grid } from "../editor/ops";
import { snap, snapDown, stepFrames } from "../editor/timecode";
import { s } from "../strings";
import { place } from "./place";

export const ZOOMS = [10, 25, 50, 100, 200] as const; // pixels per second

interface Props {
  media: HTMLMediaElement;
  peaks: Int8Array;
  peaksPerS: number;
  cues: Cue[];
  selectedId: number | null;
  flagged: ReadonlyMap<number, "hard" | "soft">;
  grid: Grid;
  zoom: number;
  onSelect: (id: number) => void;
  onTimes: (id: number, startMs: number, endMs: number) => void;
}

type Edge = "start" | "end" | "body";
interface Drag {
  id: number;
  edge: Edge;
  x0: number;
  start: number;
  end: number;
  dx: number;
}

/** Signed byte pairs (min, max) -> the two channels wavesurfer draws: top from max, bottom from min. */
function channels(raw: Int8Array): [Float32Array, Float32Array] {
  const n = raw.length >> 1;
  const top = new Float32Array(n);
  const bottom = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    bottom[i] = raw[2 * i]! / 128;
    top[i] = raw[2 * i + 1]! / 128;
  }
  return [top, bottom];
}

function playFrom(m: HTMLMediaElement, ms: number) {
  m.currentTime = ms / 1000;
  void m.play().catch(() => undefined);
}

function color(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
}

export const Waveform = memo(function Waveform(p: Props) {
  const S = s();
  const host = useRef<HTMLDivElement>(null);
  const [ws, setWs] = useState<WaveSurfer | null>(null);
  const [layer, setLayer] = useState<HTMLDivElement | null>(null);
  const [view, setView] = useState({ from: 0, to: 0, pxPerS: p.zoom });
  const [drag, setDrag] = useState<Drag | null>(null);
  const data = useMemo(() => channels(p.peaks), [p.peaks]);
  const duration = data[0].length / p.peaksPerS;
  const zoomRef = useRef(p.zoom);
  const measureRef = useRef(() => undefined as void);
  useEffect(() => {
    zoomRef.current = p.zoom;
  });

  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const w = WaveSurfer.create({
      container: el,
      media: p.media, // our own player; wavesurfer only follows and seeks it
      peaks: data,
      duration,
      height: 96,
      minPxPerSec: zoomRef.current,
      waveColor: color("--wave"),
      progressColor: color("--wave-played"),
      cursorColor: color("--accent"),
      cursorWidth: 2,
      normalize: false,
      autoScroll: true,
      autoCenter: true,
      dragToSeek: false,
      interact: true,
    });
    const div = document.createElement("div");
    div.setAttribute("part", "cues");
    Object.assign(div.style, { position: "absolute", inset: "0", zIndex: "4", pointerEvents: "none" });
    w.getWrapper().appendChild(div);
    const measure = () => {
      // wavesurfer's own rule: the zoom, or wider when the whole recording fits in the box (it then stretches)
      const pxPerS = Math.max(zoomRef.current, w.getWidth() / duration);
      const left = w.getScroll();
      setView({ from: left / pxPerS, to: (left + w.getWidth()) / pxPerS, pxPerS });
    };
    measureRef.current = measure;
    const offs = [w.on("scroll", measure), w.on("zoom", measure), w.on("redrawcomplete", measure), w.on("ready", measure)];
    const resize = new ResizeObserver(measure);
    resize.observe(el);
    setWs(w);
    setLayer(div);
    return () => {
      offs.forEach((off) => off());
      resize.disconnect();
      w.destroy(); // it leaves our media element alone
      setWs(null);
      setLayer(null);
    };
  }, [p.media, data, duration]);

  useEffect(() => {
    ws?.zoom(p.zoom);
    measureRef.current();
  }, [ws, p.zoom]);

  // the colours follow the look (light / dark) switched in the header
  useEffect(() => {
    if (!ws) return;
    const recolour = () =>
      ws.setOptions({ waveColor: color("--wave"), progressColor: color("--wave-played"), cursorColor: color("--accent") });
    const watch = new MutationObserver(recolour);
    watch.observe(document.documentElement, { attributes: true, attributeFilter: ["data-look"] });
    return () => watch.disconnect();
  }, [ws]);

  // the selected cue scrolls into view when it is chosen elsewhere (list, keys) and lies outside the view
  const sel = p.cues.find((c) => c.id === p.selectedId);
  const selStart = sel?.start_ms;
  useEffect(() => {
    if (!ws || selStart === undefined || !ws.getMediaElement()?.paused) return;
    const t = selStart / 1000;
    const pxPerS = Math.max(zoomRef.current, ws.getWidth() / duration);
    const shown = ws.getWidth() / pxPerS;
    const left = ws.getScroll() / pxPerS;
    if (t < left || t > left + shown * 0.9) ws.setScrollTime(Math.max(0, t - shown * 0.2));
  }, [ws, selStart, duration]);

  const timesOf = (d: Drag): [number, number] => {
    const dMs = (d.dx / view.pxPerS) * 1000;
    const last = snapDown(p.grid.mediaEndMs, p.grid.fps);
    if (d.edge === "start") return [Math.min(Math.max(0, d.start + dMs), stepFrames(d.end, -1, p.grid.fps)), d.end];
    if (d.edge === "end") return [d.start, Math.max(Math.min(last, d.end + dMs), stepFrames(d.start, 1, p.grid.fps))];
    const len = d.end - d.start;
    const st = Math.max(0, Math.min(d.start + dMs, last - len));
    return [st, st + len];
  };

  const down = (e: React.PointerEvent<HTMLDivElement>, c: Cue, edge: Edge) => {
    if (e.button !== 0) return;
    e.stopPropagation(); // a click on a cue selects it; it does not move the play cursor
    e.preventDefault();
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      /* a pointer the browser does not track (synthetic): the drag still works while it stays on the cue */
    }
    p.onSelect(c.id);
    setDrag({ id: c.id, edge, x0: e.clientX, start: c.start_ms, end: c.end_ms, dx: 0 });
  };
  const move = (e: React.PointerEvent<HTMLDivElement>) => {
    if (drag) setDrag({ ...drag, dx: e.clientX - drag.x0 });
  };
  const up = () => {
    if (!drag) return;
    const [st, en] = timesOf(drag);
    setDrag(null);
    if (Math.abs(drag.dx) >= 2) p.onTimes(drag.id, snap(st, p.grid.fps), snap(en, p.grid.fps));
  };

  // only the cues in view (plus a margin) are rendered
  const margin = (view.to - view.from) * 0.5;
  // a cue starting before the view can still reach into it: search back by the longest cue there is
  const longest = p.cues.reduce((m, c) => Math.max(m, c.end_ms - c.start_ms), 0);
  const first = Math.max(0, cueAt(p.cues, (view.from - margin) * 1000 - longest));
  const shown: Cue[] = [];
  for (let i = first; i < p.cues.length; i++) {
    const c = p.cues[i]!;
    if (c.start_ms / 1000 > view.to + margin) break;
    if (c.end_ms / 1000 >= view.from - margin) shown.push(c);
  }

  const cueLayer = (
    <>
      {shown.map((c) => {
        const live = drag?.id === c.id ? timesOf(drag) : null;
        const st = live ? live[0] : c.start_ms;
        const en = live ? live[1] : c.end_ms;
        const flag = p.flagged.get(c.id);
        const part = ["cue", c.id === p.selectedId ? "cue-selected" : "", flag ? `cue-${flag}` : ""].join(" ").trim();
        return (
          <div
            key={c.id}
            part={part}
            data-cue={c.id}
            title={c.lines.join(" / ")}
            ref={place({ left: (st / 1000) * view.pxPerS, width: Math.max(2, ((en - st) / 1000) * view.pxPerS) })}
            onPointerDown={(e) => down(e, c, "body")}
            onPointerMove={move}
            onPointerUp={up}
            onPointerCancel={() => setDrag(null)}
            onDoubleClick={(e) => {
              e.stopPropagation();
              playFrom(p.media, c.start_ms);
            }}
          >
            <span part="cue-text">{c.lines.join(" ")}</span>
            <div part="cue-edge cue-edge-start" aria-label={S.editor.dragStart} onPointerDown={(e) => down(e, c, "start")} onPointerMove={(e) => { e.stopPropagation(); move(e); }} onPointerUp={(e) => { e.stopPropagation(); up(); }} />
            <div part="cue-edge cue-edge-end" aria-label={S.editor.dragEnd} onPointerDown={(e) => down(e, c, "end")} onPointerMove={(e) => { e.stopPropagation(); move(e); }} onPointerUp={(e) => { e.stopPropagation(); up(); }} />
          </div>
        );
      })}
    </>
  );

  return (
    <div className="wave" aria-label={S.editor.waveform}>
      <div ref={host} className="wave-host" />
      {layer && createPortal(cueLayer, layer)}
    </div>
  );
});
