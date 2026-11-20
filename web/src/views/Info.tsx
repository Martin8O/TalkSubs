import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";

import { s } from "../strings";

/** A small (i) button that shows an explanation next to a field; closes on Escape, a second click or a click elsewhere. */
export function Info({ text }: { text: string }) {
  const S = s();
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLSpanElement>(null);
  const pop = useRef<HTMLSpanElement>(null);
  const id = useId();

  useEffect(() => {
    if (!open) return;
    const away = (e: Event) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", away);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("pointerdown", away);
      document.removeEventListener("keydown", key);
    };
  }, [open]);

  // keep the bubble inside the window: slide it sideways by what would stick out (set through the DOM,
  // because the page's security rules forbid inline style attributes)
  useLayoutEffect(() => {
    const el = pop.current;
    if (!open || !el) return;
    el.style.translate = "0";
    const r = el.getBoundingClientRect();
    const margin = 12;
    const dx = r.right > window.innerWidth - margin ? window.innerWidth - margin - r.right : 0;
    el.style.translate = `${Math.max(dx, margin - r.left)}px 0`;
  }, [open]);

  const toggle = () => setOpen(!open);

  return (
    <span className="info" ref={box}>
      <button
        type="button"
        className="info-btn"
        aria-label={S.info.more}
        aria-expanded={open}
        aria-controls={id}
        onClick={toggle}
      >
        i
      </button>
      {open && (
        <span id={id} ref={pop} role="note" className="info-pop">
          {text}
        </span>
      )}
    </span>
  );
}
