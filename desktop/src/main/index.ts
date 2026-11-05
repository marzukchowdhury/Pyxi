/**
 * Wisp's Electron main process.
 *
 * - One transparent, click-through, always-on-top overlay window per display. The overlay
 *   only catches the mouse while you hover something interactive (the 3D card, controls).
 *   It is excluded from screen capture, so Wisp never sees its own drawings.
 * - A control panel window (chat, team, memory palace, gallery, settings).
 * - Global hotkeys, the cursor feed for the buddy, and the Python brain's lifecycle.
 */
import { type ChildProcess, spawn } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { pasteText, startHoldToTalk, stopHoldToTalk } from "./holdToTalk";
import {
  app,
  BrowserWindow,
  webContents,
  clipboard,
  Notification,
  type Display,
  globalShortcut,
  ipcMain,
  Menu,
  nativeImage,
  screen,
  session,
  shell,
  Tray,
} from "electron";

const BACKEND = process.env.WISP_BACKEND ?? "http://127.0.0.1:8766";
// Opt-in: inspect the live overlay and panel from a debugger (developers only).
if (process.env.WISP_DEBUG_PORT) app.commandLine.appendSwitch("remote-debugging-port", process.env.WISP_DEBUG_PORT);
const REPO = resolve(__dirname, "../../..");
// Each action tries its shortcuts in order and keeps the first one Windows lets us have.
// Other apps (Discord, overlays, launchers) often own a combination already; without a
// fallback the shortcut would silently do nothing.
const HOTKEY_CHOICES = {
  talk: ["Control+Alt+Space", "Control+Shift+Space", "Alt+Shift+Space", "Control+Alt+T", "F8"],
  panel: ["Control+Alt+W", "Control+Shift+W", "Alt+Shift+W"],
  dismiss: ["Control+Alt+Backspace", "Control+Shift+Backspace"],
  privacy: ["Control+Alt+P", "Control+Shift+P"],
} as const;
type HotkeyName = keyof typeof HOTKEY_CHOICES;
const activeHotkeys: Record<HotkeyName, string | null> = { talk: null, panel: null, dismiss: null, privacy: null };

function registerHotkeys(handlers: Record<HotkeyName, () => void>): void {
  for (const name of Object.keys(HOTKEY_CHOICES) as HotkeyName[]) {
    for (const accelerator of HOTKEY_CHOICES[name]) {
      if (globalShortcut.register(accelerator, handlers[name])) {
        activeHotkeys[name] = accelerator;
        break;
      }
      console.warn(`[wisp] ${accelerator} is taken by another app; trying the next option for "${name}".`);
    }
    if (!activeHotkeys[name]) console.warn(`[wisp] no free shortcut for "${name}"; use the panel instead.`);
  }
  console.log("[wisp] hotkeys:", JSON.stringify(activeHotkeys));
}

const pretty = (accelerator: string | null) => (accelerator ?? "unavailable").replace("Control", "Ctrl");

const overlays = new Map<number, BrowserWindow>();
let panel: BrowserWindow | null = null;
let tray: Tray | null = null;
let brain: ChildProcess | null = null;
let primaryId = 0;
let holdToTalk = false;
let muted = process.env.WISP_MUTE === "1";

// Screen sharing (Discord, OBS, Meet): visible by default so people watching see Wisp.
// Turn it off for privacy and the overlay is hidden from every capture, Wisp's own included.
const UI_PREFS = join(REPO, ".wisp", "ui.json");
function loadShareVisible(): boolean {
  if (process.env.WISP_SHOW_OVERLAY_IN_CAPTURE === "1") return true;
  if (process.env.WISP_SHOW_OVERLAY_IN_CAPTURE === "0") return false;
  try {
    return JSON.parse(readFileSync(UI_PREFS, "utf-8")).shareVisible !== false;
  } catch {
    return true;
  }
}
let shareVisible = loadShareVisible();

function rendererUrl(page: "overlay" | "panel", query: Record<string, string>): string | null {
  const dev = process.env.ELECTRON_RENDERER_URL;
  const qs = new URLSearchParams(query).toString();
  return dev ? `${dev}/${page}.html?${qs}` : null;
}

function loadPage(win: BrowserWindow, page: "overlay" | "panel", query: Record<string, string>): void {
  const url = rendererUrl(page, query);
  if (url) void win.loadURL(url);
  else void win.loadFile(join(__dirname, `../renderer/${page}.html`), { query });
}

function displayInfo(d: Display) {
  // Physical pixel origin of this display: the brain speaks physical px, the overlay speaks CSS px.
  const phys = process.platform === "win32" ? screen.dipToScreenRect(null, d.bounds) : d.bounds;
  return {
    id: d.id,
    primary: d.id === primaryId,
    bounds: d.bounds,
    scaleFactor: d.scaleFactor,
    physOrigin: { x: phys.x, y: phys.y },
    physSize: { width: phys.width, height: phys.height },
  };
}

function createOverlay(d: Display): void {
  const win = new BrowserWindow({
    ...d.bounds,
    transparent: true,
    frame: false,
    resizable: false,
    movable: false,
    focusable: false,
    skipTaskbar: true,
    hasShadow: false,
    alwaysOnTop: true,
    backgroundColor: "#00000000",
    webPreferences: { preload: join(__dirname, "../preload/index.js"), backgroundThrottling: false },
  });
  win.setAlwaysOnTop(true, "screen-saver");
  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  win.setIgnoreMouseEvents(true, { forward: true });
  // Visible to screen sharing unless the user turned that off. When visible, the overlay blanks
  // for a moment while Wisp takes its own screenshot, so it never reads its own drawings.
  win.setContentProtection(!shareVisible);
  const info = displayInfo(d);
  loadPage(win, "overlay", { display: JSON.stringify(info), backend: BACKEND, mute: muted ? "1" : "0" });
  overlays.set(d.id, win);
  // A crashed page reloads; a closed overlay comes back while its screen is still there, so
  // Wisp never ends up talking to a window that's gone.
  win.webContents.on("render-process-gone", () => {
    if (!win.isDestroyed()) win.reload();
  });
  win.on("closed", () => {
    if (overlays.get(d.id) === win) overlays.delete(d.id);
    if ((app as unknown as { quitting?: boolean }).quitting) return;
    setTimeout(() => {
      const still = screen.getAllDisplays().find((x) => x.id === d.id);
      if (still && !overlays.has(d.id)) createOverlay(still);
    }, 600);
  });
}

/** Overlay windows that are still alive (closed ones are dropped as they're found). */
function liveOverlays(): BrowserWindow[] {
  const out: BrowserWindow[] = [];
  for (const [id, win] of overlays) {
    if (win.isDestroyed() || win.webContents.isDestroyed()) overlays.delete(id);
    else out.push(win);
  }
  return out;
}

function createPanel(): void {
  const work = screen.getPrimaryDisplay().workArea;
  panel = new BrowserWindow({
    width: 440,
    height: 760,
    x: work.x + work.width - 460,
    y: work.y + work.height - 780,
    frame: false,
    show: false,
    resizable: true,
    minWidth: 380,
    minHeight: 520,
    backgroundColor: "#00000000",
    backgroundMaterial: "acrylic",
    titleBarStyle: "hidden",
    webPreferences: { preload: join(__dirname, "../preload/index.js") },
  });
  loadPage(panel, "panel", { backend: BACKEND });
  panel.on("close", (e) => {
    if (!(app as unknown as { quitting?: boolean }).quitting) {
      e.preventDefault();
      panel?.hide();
    }
  });
}

/** Everything lives in the notch now; Ctrl+Alt+W and the tray icon unfold it. */
function toggleNotch(): void {
  const win = overlays.get(primaryId);
  if (win && !win.isDestroyed()) win.webContents.send("notch-toggle");
}

/** The old separate panel window, still available from the tray for people who like it. */
function togglePanel(): void {
  if (!panel) return;
  if (panel.isVisible() && panel.isFocused()) panel.hide();
  else {
    panel.show();
    panel.focus();
  }
}

function broadcast(channel: string, ...args: unknown[]): void {
  for (const win of [...overlays.values(), panel]) {
    if (win && !win.isDestroyed() && !win.webContents.isDestroyed()) win.webContents.send(channel, ...args);
  }
}

async function backendUp(): Promise<boolean> {
  try {
    const res = await fetch(`${BACKEND}/api/health`);
    return res.ok;
  } catch {
    return false;
  }
}

async function ensureBrain(): Promise<void> {
  if (await backendUp()) return;
  const py = process.env.WISP_PYTHON ?? join(REPO, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
  if (!existsSync(py)) {
    console.warn(`[wisp] Python not found at ${py}; start the brain yourself: wisp serve`);
    return;
  }
  const args = ["-m", "wisp", "serve", ...(process.env.WISP_ARGS?.split(" ").filter(Boolean) ?? [])];
  brain = spawn(py, args, { cwd: REPO, stdio: "inherit", windowsHide: true });
  brain.on("exit", (code) => console.log(`[wisp] brain exited (${code})`));
}

function startCursorFeed(): void {
  let last = { x: -1, y: -1 };
  setInterval(() => {
    const p = screen.getCursorScreenPoint();
    if (p.x === last.x && p.y === last.y) return;
    last = p;
    for (const win of liveOverlays()) win.webContents.send("cursor", p);
  }, 16);
}

function trayIcon(): Electron.NativeImage {
  // A tiny violet dot, drawn in code so the repo needs no binary assets.
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32"><defs><radialGradient id="g"><stop offset="0" stop-color="#e9d5ff"/><stop offset="1" stop-color="#7c3aed"/></radialGradient></defs><circle cx="16" cy="16" r="12" fill="url(#g)"/></svg>`;
  return nativeImage.createFromDataURL(`data:image/svg+xml;base64,${Buffer.from(svg).toString("base64")}`);
}

app.whenReady().then(async () => {
  session.defaultSession.setPermissionRequestHandler((_wc, permission, cb) => cb(permission === "media"));
  primaryId = screen.getPrimaryDisplay().id;
  await ensureBrain();
  for (const d of screen.getAllDisplays()) createOverlay(d);
  createPanel();
  startCursorFeed();
  // Monitors plugged in or out, a new main screen, or new scaling: every overlay's idea of its
  // display (origin, scale, primary) would be stale, so they're all rebuilt from scratch.
  let rebuild: NodeJS.Timeout | undefined;
  const rebuildOverlays = () => {
    clearTimeout(rebuild);
    rebuild = setTimeout(() => {
      primaryId = screen.getPrimaryDisplay().id;
      for (const win of overlays.values()) if (!win.isDestroyed()) win.destroy();
      overlays.clear();
      for (const d of screen.getAllDisplays()) createOverlay(d);
    }, 500);
  };
  screen.on("display-added", rebuildOverlays);
  screen.on("display-removed", rebuildOverlays);
  screen.on("display-metrics-changed", (_e, _d, changed) => {
    if (changed.some((c) => c === "bounds" || c === "scaleFactor" || c === "workArea")) rebuildOverlays();
  });

  // Hold Ctrl + left Alt to talk (the main way to use Wisp). The shortcut below stays as a
  // press-to-toggle fallback, and also covers keyboards where the hook can't start.
  holdToTalk = startHoldToTalk({
    down: () => broadcast("ptt-down"),
    up: () => broadcast("ptt-up"),
    cancel: () => broadcast("ptt-cancel"),
    doubleTap: () => {
      // The notch lives on the primary overlay; give it the keyboard, then open the text box.
      const win = overlays.get(primaryId);
      if (!win || win.isDestroyed()) return;
      win.setFocusable(true);
      win.focus();
      win.webContents.send("quick-type");
    },
    click: (x, y) => {
      const win = overlays.get(primaryId);
      if (win && !win.isDestroyed()) win.webContents.send("global-click", { x, y });
    },
  });
  registerHotkeys({
    talk: () => broadcast("ptt"),
    panel: toggleNotch,
    dismiss: () => broadcast("dismiss"),
    privacy: () => broadcast("privacy-toggle"),
  });

  tray = new Tray(trayIcon());
  tray.setToolTip("Wisp");
  tray.on("click", toggleNotch);
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: `Open Wisp  (${pretty(activeHotkeys.panel)})`, click: toggleNotch },
      { label: "Open as a separate window", click: togglePanel },
      { label: `Talk  (${pretty(activeHotkeys.talk)})`, click: () => broadcast("ptt") },
      { label: `Dismiss drawings  (${pretty(activeHotkeys.dismiss)})`, click: () => broadcast("dismiss") },
      {
        label: "Mute Wisp's voice",
        type: "checkbox",
        checked: muted,
        click: (item) => {
          muted = item.checked;
          broadcast("mute", muted);
        },
      },
      { type: "separator" },
      { label: "Quit Wisp", click: () => app.quit() },
    ]),
  );
  // No window pops up at launch: the notch on the top edge is the whole interface.
});

// Overlay asks to catch the mouse while you hover its interactive bits.
ipcMain.on("set-interactive", (e, interactive: boolean) => {
  const win = BrowserWindow.fromWebContents(e.sender);
  win?.setIgnoreMouseEvents(!interactive, { forward: true });
  if (win && interactive) win.setFocusable(true);
  // Keep the keyboard while the notch's text box is in use, even after the mouse leaves it.
  if (win && !interactive && !win.isFocused()) win.setFocusable(false);
});
ipcMain.on("toggle-panel", togglePanel);

// One voice across monitors: clips from other overlays play on the main screen's queue, and
// its "started / ended / cancelled" go back to the window that queued them.
const primaryOverlay = () => {
  const win = overlays.get(primaryId);
  return win && !win.isDestroyed() ? win : liveOverlays()[0];
};
ipcMain.on("voice-play", (e, clip: Record<string, unknown>) => {
  primaryOverlay()?.webContents.send("voice-play", { ...clip, from: e.sender.id });
});
ipcMain.on("voice-event", (_e, msg: { from: number }) => {
  const target = webContents.fromId(msg.from);
  if (target && !target.isDestroyed()) target.send("voice-event", msg);
});
ipcMain.on("voice-stop", (e, ids?: string[]) => {
  primaryOverlay()?.webContents.send("voice-stop", { from: e.sender.id, ids });
});
ipcMain.on("say-started", (e, key: string) => {
  for (const win of liveOverlays()) if (win.webContents.id !== e.sender.id) win.webContents.send("say-started", key);
});
// After typing to Wisp, the keyboard goes back to the app you were in.
ipcMain.on("release-focus", (e) => {
  const win = BrowserWindow.fromWebContents(e.sender);
  if (!win || win.isDestroyed()) return;
  win.blur();
  win.setFocusable(false);
});
ipcMain.on("ptt", () => broadcast("ptt"));
ipcMain.on("open-external", (_e, url: string) => {
  if (/^https?:\/\//.test(url)) void shell.openExternal(url);
});
ipcMain.handle("displays", () => screen.getAllDisplays().map(displayInfo));
// Quick actions. Inserting pastes into whatever field has focus, then puts your clipboard back.
ipcMain.on("action", async (_e, kind: string, value: string) => {
  if (typeof value !== "string" || !value) return;
  if (kind === "open_url" && /^https?:\/\//i.test(value)) void shell.openExternal(value);
  else if (kind === "copy") clipboard.writeText(value);
  else if (kind === "insert") {
    const focused = BrowserWindow.getFocusedWindow();
    if (focused) {
      // Typing went to Wisp's own window (the notch text box); hand focus back first.
      focused.blur();
      await new Promise((r) => setTimeout(r, 180));
    }
    const previous = await Promise.resolve(clipboard.readText()); // async in newer Electron
    clipboard.writeText(value);
    await new Promise((r) => setTimeout(r, 60));
    pasteText();
    setTimeout(() => clipboard.writeText(previous), 900);
  }
});
// Files Wisp made: open them or show them in Explorer, but only inside Wisp's own folders.
function wispFile(path: unknown): string | null {
  if (typeof path !== "string" || !path) return null;
  const full = resolve(path);
  const allowed = [join(app.getPath("documents"), "Wisp"), join(REPO, ".wisp", "outputs")].map((d) => resolve(d).toLowerCase());
  return allowed.some((d) => full.toLowerCase().startsWith(d + "\\") || full.toLowerCase().startsWith(d + "/")) && existsSync(full) ? full : null;
}
ipcMain.on("open-file", (_e, path: unknown) => {
  const file = wispFile(path);
  if (file) void shell.openPath(file);
});
ipcMain.on("show-in-folder", (_e, path: unknown) => {
  const file = wispFile(path);
  if (file) shell.showItemInFolder(file);
});
ipcMain.on("notify", (_e, title: string, body: string, path?: string) => {
  if (!Notification.isSupported()) return;
  const note = new Notification({ title: String(title).slice(0, 80), body: String(body).slice(0, 200), silent: true });
  const file = wispFile(path);
  if (file) note.on("click", () => void shell.openPath(file));
  note.show();
});
ipcMain.handle("share-visible", () => shareVisible);
ipcMain.on("set-share-visible", (_e, value: boolean) => {
  shareVisible = !!value;
  for (const win of liveOverlays()) win.setContentProtection(!shareVisible);
  try {
    mkdirSync(join(REPO, ".wisp"), { recursive: true });
    writeFileSync(UI_PREFS, JSON.stringify({ shareVisible }));
  } catch (err) {
    console.warn("[wisp] couldn't save the screen-share setting:", err);
  }
  broadcast("share-visible", shareVisible);
});
ipcMain.handle("hotkeys", () => ({ ...activeHotkeys, hold: holdToTalk ? "Control+Alt" : null }));
// The notch's text box needs keyboard focus, which a non-focusable overlay can't take on its own.
ipcMain.on("focus-overlay", (e) => {
  const win = BrowserWindow.fromWebContents(e.sender);
  if (!win) return;
  win.setFocusable(true);
  win.focus();
});

app.on("before-quit", () => {
  stopHoldToTalk();
  (app as unknown as { quitting?: boolean }).quitting = true;
  globalShortcut.unregisterAll();
  brain?.kill();
});
app.on("window-all-closed", () => app.quit());
