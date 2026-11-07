// The real app, end to end, with the launch key: Electron starts the brain (demo model, its own
// port and data folder, so an installed Pyxi keeps running untouched), its windows connect, an
// Allow card works, and a program without the key is refused. Also checks the Electron
// hardening: sandboxed isolated windows, strict CSP, no popups, no navigating away, and links
// limited to http and https (the shell is stubbed, so nothing opens on this PC).
//
//   npm run build && node e2e/security.mjs
import { _electron as electron } from "playwright-core";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const PORT = process.env.PYXI_E2E_PORT ?? "8799";
const BACKEND = `http://127.0.0.1:${PORT}`;
const data = mkdtempSync(join(tmpdir(), "pyxi-e2e-"));
const profile = mkdtempSync(join(tmpdir(), "pyxi-e2e-profile-"));
const checks = [];
const check = (name, ok, detail = "") => checks.push({ name, ok: !!ok, detail: String(detail).slice(0, 200) });

const env = { ...process.env };
delete env.PYXI_BRAIN_KEY; // the app must make its own
const app = await electron.launch({
  args: [".", `--user-data-dir=${profile}`],
  env: {
    ...env,
    PYXI_BACKEND: BACKEND,
    PYXI_PORT: PORT,
    PYXI_ARGS: "--demo",
    PYXI_DATA_DIR: data,
    PYXI_AUTO_VOICE: "0",
    PYXI_VOICE__TTS: "client",
    PYXI_MUTE: "1",
    PYXI_E2E: "1",
  },
});
const errors = [];
const violations = [];
try {
  const seen = new Map();
  for (let i = 0; i < 160 && seen.size < 2; i++) {
    for (const w of app.windows()) {
      const url = decodeURIComponent(w.url());
      // Cards show on the main screen's overlay only.
      const kind = url.includes("overlay") && url.includes('"primary":true') ? "overlay" : url.includes("panel.html") ? "panel" : null;
      if (kind && !seen.has(kind)) {
        seen.set(kind, w);
        w.on("pageerror", (e) => errors.push(`${kind}: ${e.message}`));
        w.on("console", (m) => /Content Security Policy|Refused to/i.test(m.text()) && violations.push(`${kind}: ${m.text()}`));
      }
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  const overlay = seen.get("overlay");
  const panel = seen.get("panel");
  check("both windows open", overlay && panel);

  // Electron hardening: every window sandboxed and isolated, no Node in the page.
  const prefs = await app.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows().map((w) => {
      const p = w.webContents.getLastWebPreferences() ?? {};
      return { url: w.webContents.getURL().split("?")[0].slice(-30), contextIsolation: p.contextIsolation, nodeIntegration: p.nodeIntegration, sandbox: p.sandbox, webviewTag: p.webviewTag };
    }),
  );
  check("every window: contextIsolation, sandbox, no nodeIntegration, no webview",
    prefs.length >= 2 && prefs.every((p) => p.contextIsolation === true && p.sandbox === true && !p.nodeIntegration && !p.webviewTag), JSON.stringify(prefs));
  check("no Node in the page", await panel.evaluate(() => typeof require === "undefined" && typeof process === "undefined"));
  const csp = await panel.evaluate(() => document.querySelector('meta[http-equiv="Content-Security-Policy"]')?.getAttribute("content") ?? "");
  check("strict CSP on the page", csp.includes("default-src 'none'") && /script-src 'self';/.test(csp) && !csp.includes("unsafe-eval"), csp);
  check("CSP blocks inline script", await panel.evaluate(() => {
    const s = document.createElement("script");
    s.textContent = "window.__inline = 1";
    document.head.appendChild(s);
    return window.__inline === undefined;
  }));
  check("CSP blocks other hosts", await panel.evaluate(() => fetch("https://example.com/").then(() => false, () => true)));
  violations.length = 0; // the two above were meant to be refused

  // Links: stub the real shell so nothing opens on this PC, then try what a page might.
  await app.evaluate(({ shell }) => {
    globalThis.__opened = [];
    shell.openExternal = async (url) => void globalThis.__opened.push(url);
  });
  await panel.evaluate(() => {
    const b = window.pyxiBridge;
    b.openExternal("file:///C:/Windows/System32/calc.exe");
    b.openExternal("ms-settings:privacy");
    b.runAction("open_url", "javascript:alert(1)");
    b.runAction("open_url", "https://example.com/ok");
    b.openExternal("http://example.com/also-ok");
    return window.open("https://example.com/popup") === null;
  });
  const windowsBefore = app.windows().length;
  await panel.evaluate(() => window.open("file:///C:/Windows/win.ini"));
  await new Promise((r) => setTimeout(r, 800));
  const opened = await app.evaluate(() => globalThis.__opened);
  check("openExternal: http and https only", JSON.stringify(opened) === JSON.stringify(["https://example.com/ok", "http://example.com/also-ok", "https://example.com/popup"]), JSON.stringify(opened));
  check("window.open never makes a window", app.windows().length === windowsBefore);

  // The renderer has the key (from the preload), and it isn't in the page URL.
  const key = await overlay.evaluate(() => window.pyxiBridge?.brainKey ?? "");
  check("preload hands the page its launch key", key.length >= 40);
  check("the key is not in the page URL", !overlay.url().includes(key));

  // The windows connected: the panel shows a status other than "connecting".
  await panel.waitForFunction(() => { const t = document.querySelector(".who .status")?.textContent ?? ""; return t && !/offline/i.test(t); }, null, { timeout: 90000 }).catch(() => undefined);
  const status = await panel.locator(".who .status").innerText();
  check("panel connected to the brain", !/offline/i.test(status), status);

  // An outside program, with no key or a wrong one, gets nothing.
  const health = await (await fetch(`${BACKEND}/api/health`)).json();
  check("health answers but says 'not yours'", health.ok && health.authorized === false, JSON.stringify(health));
  check("state refused without the key", (await fetch(`${BACKEND}/api/state`)).status === 401);
  check("state refused with a wrong key", (await fetch(`${BACKEND}/api/state`, { headers: { "X-Pyxi-Key": "guess" } })).status === 401);
  check("quit refused without the key", (await fetch(`${BACKEND}/api/quit`, { method: "POST" })).status === 401);
  const wsOutcome = await new Promise((resolve) => {
    const ws = new WebSocket(`${BACKEND.replace("http", "ws")}/ws`);
    ws.onopen = () => {
      ws.send(JSON.stringify({ type: "text", text: "have Scout fill in the demo form" }));
      resolve("opened");
    };
    ws.onerror = () => resolve("refused");
    ws.onclose = () => resolve("refused");
    setTimeout(() => resolve("timeout"), 5000);
  });
  check("WebSocket without the key refused", wsOutcome === "refused", wsOutcome);

  // The real app's way in still works: an Allow card, answered in the overlay. A keyed local
  // tool (this script, with the key the page holds) asks the demo brain for a test card.
  // The scene may open and close while the card is being answered: note any WebGL canvas
  // (and whether it got a context) the moment it appears, on every screen's overlay (a scene
  // opens on the screen Pyxi looked at, which may not be the main one).
  const allOverlays = app.windows().filter((w) => w.url().includes("overlay"));
  for (const w of allOverlays) await w.evaluate(() => {
    window.__scene = { canvas: false, gl: false };
    const look = () => {
      const c = document.querySelector("canvas");
      if (!c) return;
      window.__scene.canvas = true;
      window.__scene.gl ||= !!(c.getContext("webgl2") || c.getContext("webgl"));
    };
    new MutationObserver(look).observe(document.body, { childList: true, subtree: true });
    look();
  });
  const tool = new WebSocket(`${BACKEND.replace("http", "ws")}/ws?key=${encodeURIComponent(key)}`);
  const answers = [];
  const seenTypes = [];
  tool.onmessage = (m) => {
    const e = JSON.parse(m.data);
    seenTypes.push(e.type);
    if (e.type === "e2e_answer") answers.push(e.value);
  };
  await new Promise((r) => (tool.onopen = r));
  check("a local tool with the key connects", tool.readyState === WebSocket.OPEN);
  await panel.locator('input[placeholder="Ask Pyxi anything"]').fill("how do you work?");
  await panel.keyboard.press("Enter");
  tool.send(JSON.stringify({ type: "e2e_card" }));
  const card = overlay.locator(".request button.yes");
  await card.first().waitFor({ state: "attached", timeout: 30000 }).catch(() => undefined);
  const cardShown = (await card.count()) > 0;
  check("Allow card shown in the overlay", cardShown, cardShown ? await overlay.locator(".request .req-action").first().innerText() : "");
  if (cardShown) {
    await card.first().dispatchEvent("click"); // the overlay is click-through; click the button itself
    for (let i = 0; i < 50 && !answers.length; i++) await new Promise((r) => setTimeout(r, 200));
    check("Allow reached the brain", answers[0] === true, JSON.stringify(answers));
    check("card closed", (await overlay.locator(".request").count()) === 0);
  }
  // The demo answer to "how do you work?" is a 3D scene: WebGL still renders under the strict CSP.
  // The turn starts with a real screenshot of this PC (and its accessibility tree), which can
  // take a while on a busy desktop: wait for the turn itself.
  const asked = Date.now();
  for (let i = 0; i < 600 && !seenTypes.includes("turn_done"); i++) await new Promise((r) => setTimeout(r, 200));
  await new Promise((r) => setTimeout(r, 3000));
  const scenes = await Promise.all(allOverlays.map((w) => w.evaluate(() => window.__scene).catch(() => null)));
  const scene = { canvas: scenes.some((s) => s?.canvas), gl: scenes.some((s) => s?.gl), overlays: allOverlays.length };
  const took = `${Math.round((Date.now() - asked) / 1000)}s`;
  check("3D scene renders under the CSP (WebGL canvas)", scene.canvas && scene.gl, JSON.stringify({ scene, took, events: [...new Set(seenTypes)] }));
  tool.close();
  // Last, as Playwright then waits on the refused navigation: a page can't take the window away.
  const urls = () => app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().map((w) => w.webContents.getURL().split("?")[0]));
  const before = await urls();
  await panel.evaluate(() => { location.href = "https://example.com/phish"; }).catch(() => undefined);
  await overlay.evaluate(() => { location.assign("file:///C:/Windows/win.ini"); }).catch(() => undefined);
  await new Promise((r) => setTimeout(r, 1500));
  const after = await urls();
  check("a page can't navigate the app windows away", JSON.stringify(after) === JSON.stringify(before), JSON.stringify(after).slice(0, 160));
} finally {
  await app.close().catch(() => undefined);
  await new Promise((r) => setTimeout(r, 1500));
  check("brain quit with the app", await fetch(`${BACKEND}/api/health`).then(() => false, () => true));
  rmSync(profile, { recursive: true, force: true });
  rmSync(data, { recursive: true, force: true });
}
check("no CSP violations during the run", violations.length === 0, violations.join(" | "));
const failed = checks.filter((c) => !c.ok);
console.log(JSON.stringify({ checks, errors, violations }, null, 2));
console.log(failed.length ? `FAILED: ${failed.map((c) => c.name).join(", ")}` : `all ${checks.length} checks passed`);
process.exit(failed.length ? 1 : 0);
