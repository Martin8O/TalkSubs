import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

const DELAY_MS = 450;
const GAP = 8;
const MARGIN = 12;

/** One hover tip for the whole window: an element with a data-tip attribute shows that text after a short pause (at
 * once when reached with the keyboard, or when a tip is already up and the pointer moves to the next control), above
 * it or, without room there, below it, always inside the window (it never covers what is being typed under it). The element points screen readers at the tip while
 * it shows. Placed through the DOM because the page's security rules forbid inline style attributes. */
export function Tips() {
  const [tip, setTip] = useState<{ text: string; el: Element } | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const id = useId();

  useEffect(() => {
    let timer = 0;
    let shown: Element | null = null;
    const target = (e: Event) => (e.target instanceof Element ? e.target.closest("[data-tip]") : null);
    const put = (next: { text: string; el: Element } | null) => {
      shown?.removeAttribute("aria-describedby");
      shown = next && next.el.isConnected ? next.el : null; // a control that went away meanwhile shows nothing
      shown?.setAttribute("aria-describedby", id);
      setTip(shown && next ? next : null);
    };
    const show = (el: Element | null, delay: number) => {
      window.clearTimeout(timer);
      const text = el?.getAttribute("data-tip");
      if (!el || !text) {
        put(null);
        return;
      }
      if (el === shown) return;
      if (shown || delay === 0) put({ text, el });
      else timer = window.setTimeout(() => put({ text, el }), delay);
    };
    const over = (e: Event) => show(target(e), DELAY_MS);
    const focus = (e: Event) => {
      const el = target(e);
      show(el && el.matches(":focus-visible") ? el : null, 0);
    };
    const hide = () => {
      window.clearTimeout(timer);
      put(null);
    };
    document.addEventListener("pointerover", over);
    document.addEventListener("focusin", focus);
    document.addEventListener("pointerdown", hide);
    document.addEventListener("keydown", hide);
    window.addEventListener("scroll", hide, true);
    window.addEventListener("blur", hide);
    window.addEventListener("resize", hide);
    return () => {
      window.clearTimeout(timer);
      shown?.removeAttribute("aria-describedby");
      document.removeEventListener("pointerover", over);
      document.removeEventListener("focusin", focus);
      document.removeEventListener("pointerdown", hide);
      document.removeEventListener("keydown", hide);
      window.removeEventListener("scroll", hide, true);
      window.removeEventListener("blur", hide);
      window.removeEventListener("resize", hide);
    };
  }, [id]);

  useLayoutEffect(() => {
    const el = box.current;
    if (!tip || !el || !tip.el.isConnected) return;
    const at = tip.el.getBoundingClientRect();
    const own = el.getBoundingClientRect();
    const above = at.top - GAP - own.height >= MARGIN;
    const top = above ? at.top - GAP - own.height : Math.min(at.bottom + GAP, window.innerHeight - MARGIN - own.height);
    const centre = at.left + at.width / 2 - own.width / 2;
    const left = Math.min(Math.max(MARGIN, centre), window.innerWidth - MARGIN - own.width);
    el.style.top = `${Math.round(top)}px`;
    el.style.left = `${Math.round(left)}px`;
    el.style.visibility = "visible";
  }, [tip]);

  if (!tip) return null;
  return createPortal(
    <div ref={box} id={id} className="tip" role="tooltip">
      {tip.text}
    </div>,
    document.body,
  );
}
