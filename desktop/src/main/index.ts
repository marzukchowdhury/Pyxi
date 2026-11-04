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
import { existsSync } from "node:fs";
import { join, resolve } from "node:path";
import {
  app,
  BrowserWindow,
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
const REPO = resolve(__dirname, "../../..");
const HOTKEYS = {
  talk: "Control+Alt+Space",
  panel: "Control+Alt+W",
  dismiss: "Control+Alt+Backspace",
  privacy: "Control+Alt+P",
};

const overlays = new Map<number, BrowserWindow>();
let panel: BrowserWindow | null = null;
let tray: Tray | null = null;
let brain: ChildProcess | null = null;
let primaryId = 0;

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
  // Hide the overlay from screenshots (including Wisp's own): it never sees its own drawings.
  if (process.env.WISP_SHOW_OVERLAY_IN_CAPTURE !== "1") win.setContentProtection(true);
  const info = displayInfo(d);
  loadPage(win, "overlay", { display: JSON.stringify(info), backend: BACKEND });
  overlays.set(d.id, win);
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

function togglePanel(): void {
  if (!panel) return;
  if (panel.isVisible() && panel.isFocused()) panel.hide();
  else {
    panel.show();
    panel.focus();
  }
}

function broadcast(channel: string, ...args: unknown[]): void {
  for (const win of [...overlays.values(), panel]) win?.webContents.send(channel, ...args);
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
    for (const win of overlays.values()) win.webContents.send("cursor", p);
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
  screen.on("display-added", (_e, d) => createOverlay(d));
  screen.on("display-removed", (_e, d) => {
    overlays.get(d.id)?.destroy();
    overlays.delete(d.id);
  });

  globalShortcut.register(HOTKEYS.talk, () => broadcast("ptt"));
  globalShortcut.register(HOTKEYS.panel, togglePanel);
  globalShortcut.register(HOTKEYS.dismiss, () => broadcast("dismiss"));
  globalShortcut.register(HOTKEYS.privacy, () => broadcast("privacy-toggle"));

  tray = new Tray(trayIcon());
  tray.setToolTip("Wisp");
  tray.on("click", togglePanel);
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: "Open panel  (Ctrl+Alt+W)", click: togglePanel },
      { label: "Talk  (Ctrl+Alt+Space)", click: () => broadcast("ptt") },
      { label: "Dismiss drawings  (Ctrl+Alt+Backspace)", click: () => broadcast("dismiss") },
      { type: "separator" },
      { label: "Quit Wisp", click: () => app.quit() },
    ]),
  );
  setTimeout(() => panel?.show(), 600);
});

// Overlay asks to catch the mouse while you hover its interactive bits.
ipcMain.on("set-interactive", (e, interactive: boolean) => {
  const win = BrowserWindow.fromWebContents(e.sender);
  win?.setIgnoreMouseEvents(!interactive, { forward: true });
  if (win && interactive) win.setFocusable(true);
  if (win && !interactive) win.setFocusable(false);
});
ipcMain.on("toggle-panel", togglePanel);
ipcMain.on("ptt", () => broadcast("ptt"));
ipcMain.on("open-external", (_e, url: string) => {
  if (/^https?:\/\//.test(url)) void shell.openExternal(url);
});
ipcMain.handle("displays", () => screen.getAllDisplays().map(displayInfo));

app.on("before-quit", () => {
  (app as unknown as { quitting?: boolean }).quitting = true;
  globalShortcut.unregisterAll();
  brain?.kill();
});
app.on("window-all-closed", () => app.quit());
