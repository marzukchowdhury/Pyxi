// Launches the real Electron app and checks both windows come up healthy.
import { _electron as electron } from "playwright-core";
import { mkdirSync } from "node:fs";

mkdirSync("../.wisp/shots", { recursive: true });
const app = await electron.launch({ args: ["."], env: { ...process.env, WISP_SHOW_OVERLAY_IN_CAPTURE: "1" } });
const errors = [];
const seen = new Map();
for (let i = 0; i < 40 && seen.size < 2; i++) {
  for (const w of app.windows()) {
    const url = w.url();
    const kind = url.includes("overlay") ? "overlay" : url.includes("panel") ? "panel" : null;
    if (kind && !seen.has(kind)) {
      seen.set(kind, w);
      w.on("pageerror", (e) => errors.push(`${kind}: ${e.message}`));
    }
  }
  await new Promise((r) => setTimeout(r, 250));
}
await new Promise((r) => setTimeout(r, 3500));
const overlay = seen.get("overlay");
const panel = seen.get("panel");
const report = {
  windows: app.windows().length,
  overlayBridge: overlay ? await overlay.evaluate(() => !!window.wispBridge?.electron) : null,
  overlayDisplay: overlay ? await overlay.evaluate(() => new URLSearchParams(location.search).get("display")?.slice(0, 120)) : null,
  panelStatus: panel ? await panel.locator(".who .status").innerText() : null,
  panelDemoBanner: panel ? await panel.locator(".demo-banner").count() : null,
};
const info = await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().map((w) => ({
  title: w.getTitle(), bounds: w.getBounds(), onTop: w.isAlwaysOnTop(), focusable: w.isFocusable(), visible: w.isVisible(),
})));
if (overlay) {
  await overlay.evaluate(() => window.__wisp ?? null);
  await overlay.screenshot({ path: "../.wisp/shots/electron_overlay.png" });
}
if (panel) await panel.screenshot({ path: "../.wisp/shots/electron_panel.png" });
console.log(JSON.stringify({ report, info, errors }, null, 2));
await app.close();
