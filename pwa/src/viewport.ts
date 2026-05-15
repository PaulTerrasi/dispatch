// Keeps a `--viewport-h` CSS variable on <html> in sync with the visible
// viewport height. The chat layout uses this to shrink around the on-screen
// keyboard so the input bar stays anchored above it instead of being covered.
//
// `interactive-widget=resizes-content` in the viewport meta already does this
// for the layout viewport on modern browsers — this is the fallback path for
// older Safari and gives us a single source of truth either way.

export function initViewportTracker(): void {
  const vv = window.visualViewport;
  const root = document.documentElement;

  const apply = (): void => {
    const h = vv ? vv.height : window.innerHeight;
    root.style.setProperty("--viewport-h", `${h}px`);
  };

  apply();
  if (vv) {
    vv.addEventListener("resize", apply);
    vv.addEventListener("scroll", apply);
  } else {
    window.addEventListener("resize", apply);
  }
}
