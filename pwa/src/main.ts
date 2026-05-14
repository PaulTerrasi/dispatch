import { startRouter } from "./router";
import { initPullToRefresh } from "./pull-to-refresh";
import { renderArchive } from "./views/archive";
import { renderChat } from "./views/chat";
import { renderDigestDay } from "./views/digest_day";
import { renderRunDetail } from "./views/run_detail";
import { renderRuns } from "./views/runs";
import { renderToday } from "./views/today";

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker
      .register("/sw.js")
      .then((reg) => {
        initPullToRefresh(reg);
      })
      .catch((err) => {
        console.warn("SW registration failed:", err);
        initPullToRefresh(null);
      });

    // When a new SW takes control, reload to apply the new app shell
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      location.reload();
    });
  });
} else {
  window.addEventListener("load", () => initPullToRefresh(null));
}

const mount = document.getElementById("app");
if (!mount) throw new Error("missing #app element");

startRouter(
  [
    { path: "/today", render: renderToday },
    { path: "/archive", render: renderArchive },
    { path: "/chat", render: renderChat },
    { path: "/digest/:date", render: renderDigestDay },
    { path: "/runs", render: renderRuns },
    { path: "/run/:run_id", render: renderRunDetail },
  ],
  mount,
);
