import { forceRefresh } from "./router";

const THRESHOLD = 80; // px of pull needed to trigger refresh
const MAX_PULL = 100; // px cap on visual translation

export function initPullToRefresh(registration: ServiceWorkerRegistration | null): void {
  const indicator = createIndicator();
  document.body.appendChild(indicator);

  let startY = 0;
  let pulling = false;

  document.addEventListener(
    "touchstart",
    (e) => {
      if (window.scrollY !== 0) return;
      startY = e.touches[0].clientY;
      pulling = true;
    },
    { passive: true },
  );

  document.addEventListener("touchmove", (e) => {
    if (!pulling) return;
    const delta = e.touches[0].clientY - startY;
    if (delta <= 0) {
      pulling = false;
      resetIndicator(indicator);
      return;
    }
    // Resist pull with square-root damping so it feels springy
    const visual = Math.min(Math.sqrt(delta) * 5, MAX_PULL);
    indicator.style.transform = `translateX(-50%) translateY(${visual}px)`;
    indicator.style.opacity = String(Math.min(delta / THRESHOLD, 1));

    if (delta > 4) {
      e.preventDefault();
    }
  });

  document.addEventListener("touchend", async (e) => {
    if (!pulling) return;
    pulling = false;
    const delta = e.changedTouches[0].clientY - startY;
    if (delta < THRESHOLD) {
      resetIndicator(indicator);
      return;
    }
    await triggerRefresh(indicator, registration);
  });
}

async function triggerRefresh(
  indicator: HTMLElement,
  registration: ServiceWorkerRegistration | null,
): Promise<void> {
  // Snap to resting position and start spinner
  indicator.style.transform = "translateX(-50%) translateY(56px)";
  indicator.style.opacity = "1";
  indicator.classList.add("spinning");

  if (registration) {
    try {
      await registration.update();
    } catch {
      // non-fatal: network may be offline
    }

    if (registration.waiting) {
      // New deployment available — activate it; controllerchange → location.reload()
      registration.waiting.postMessage({ type: "SKIP_WAITING" });
      return; // reload will happen via controllerchange listener in main.ts
    }
  }

  // No new SW: re-render current view with fresh API data
  forceRefresh();

  // Brief pause so spinner is visible, then hide
  await delay(600);
  resetIndicator(indicator);
}

function resetIndicator(indicator: HTMLElement): void {
  indicator.classList.remove("spinning");
  indicator.style.transform = "translateX(-50%) translateY(0px)";
  indicator.style.opacity = "0";
}

function createIndicator(): HTMLElement {
  const wrap = document.createElement("div");
  wrap.className = "ptr-indicator";
  wrap.setAttribute("aria-hidden", "true");
  const spinner = document.createElement("div");
  spinner.className = "ptr-spinner";
  wrap.appendChild(spinner);
  return wrap;
}

function delay(ms: number): Promise<void> {
  return new Promise((res) => setTimeout(res, ms));
}
