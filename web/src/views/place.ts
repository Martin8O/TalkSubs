// Position an element from computed numbers. The page's security policy forbids style attributes in markup,
// so layout numbers (virtual list rows, cue boxes on the waveform) are set through the element's style object.
export function place(css: Partial<Record<"top" | "left" | "width" | "height", number>>) {
  return (el: HTMLElement | null) => {
    if (!el) return;
    for (const [k, v] of Object.entries(css)) el.style.setProperty(k, `${v}px`);
  };
}
