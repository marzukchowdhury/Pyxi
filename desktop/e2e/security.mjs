// The real app, end to end, with the launch key: Electron starts the brain (demo model, its own
// port and data folder, so an installed Pyxi keeps running untouched), its windows connect, an
// Allow card works, and a program without the key is refused.
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
      }
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  const overlay = seen.get("overlay");
  const panel = seen.get("panel");
  check("both windows open", overlay && panel);

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
  await panel.locator('input[placeholder="Ask Pyxi anything"]').fill("how do you work?");
  await panel.keyboard.press("Enter");
  const tool = new WebSocket(`${BACKEND.replace("http", "ws")}/ws?key=${encodeURIComponent(key)}`);
  const answers = [];
  tool.onmessage = (m) => {
    const e = JSON.parse(m.data);
    if (e.type === "e2e_answer") answers.push(e.value);
  };
  await new Promise((r) => (tool.onopen = r));
  check("a local tool with the key connects", tool.readyState === WebSocket.OPEN);
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
  tool.close();
} finally {
  await app.close().catch(() => undefined);
  await new Promise((r) => setTimeout(r, 1500));
  check("brain quit with the app", await fetch(`${BACKEND}/api/health`).then(() => false, () => true));
  rmSync(profile, { recursive: true, force: true });
  rmSync(data, { recursive: true, force: true });
}
const failed = checks.filter((c) => !c.ok);
console.log(JSON.stringify({ checks, errors }, null, 2));
console.log(failed.length ? `FAILED: ${failed.map((c) => c.name).join(", ")}` : `all ${checks.length} checks passed`);
process.exit(failed.length ? 1 : 0);
