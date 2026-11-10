/**
 * Pyxi's Electron main process.
 *
 * - One transparent, click-through, always-on-top overlay window per display. The overlay
 *   only catches the mouse while you hover something interactive (the 3D card, controls).
 *   It is excluded from screen capture, so Pyxi never sees its own drawings.
 * - A control panel window (chat, team, memory palace, gallery, settings).
 * - Global hotkeys, the cursor feed for the buddy, and the Python brain's lifecycle.
 */
import { type ChildProcess, spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { existsSync, mkdirSync, openSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chordLabel, chordsFrom, pasteText, startHoldToTalk, stopHoldToTalk } from "./holdToTalk";
import { MAC_HOTKEYS, MAC_RUNNABLE, macKeyName, needsPermissions, permissionRows, settingsUrl, trayTemplatePng } from "./mac";
import { trayTemplate } from "./trayMenu";
import { checkNow, startUpdates } from "./updates";
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
  systemPreferences,
  Tray,
} from "electron";

/** The Mac build differs in a few places, each marked with IS_MAC; Windows runs as before. */
const IS_MAC = process.platform === "darwin";
const BACKEND = process.env.PYXI_BACKEND ?? "http://127.0.0.1:8766";
// Opt-in: inspect the live overlay and panel from a debugger (developers only).
if (process.env.PYXI_DEBUG_PORT) app.commandLine.appendSwitch("remote-debugging-port", process.env.PYXI_DEBUG_PORT);
const REPO = resolve(__dirname, "../../..");
// Where Pyxi keeps its data and runs its brain: the repo while developing, the user's own app
// data folder (%APPDATA%/Pyxi) once installed, where a .env can also go.
const HOME = app.isPackaged ? app.getPath("userData") : REPO;
// The brain's launch key, new at every start. Any program on the PC can connect to 127.0.0.1, so
// the brain refuses every request without it (pyxi/brainkey.py). Only the brain (through its
// environment) and Pyxi's own pages (through the preload) ever get it.
let brainKey = process.env.PYXI_BRAIN_KEY || randomBytes(32).toString("base64url");
const keyed = (init: RequestInit = {}): RequestInit => ({ ...init, headers: { "X-Pyxi-Key": brainKey } });
// Each action tries its shortcuts in order and keeps the first one Windows lets us have.
// Other apps (Discord, overlays, launchers) often own a combination already; without a
// fallback the shortcut would silently do nothing.
const WINDOWS_HOTKEYS = {
  talk: ["Control+Alt+Space", "Control+Shift+Space", "Alt+Shift+Space", "Control+Alt+T", "F8"],
  panel: ["Control+Alt+W", "Control+Shift+W", "Alt+Shift+W"],
  dismiss: ["Control+Alt+Backspace", "Control+Shift+Backspace"],
  privacy: ["Control+Alt+P", "Control+Shift+P"],
  stop: ["Control+Alt+Pause", "Control+Alt+End", "Control+Shift+Pause"],
} as const;
type HotkeyName = keyof typeof WINDOWS_HOTKEYS;
// A Mac keyboard has no Pause or End key: Stop is Control+Option+Escape there.
const HOTKEY_CHOICES: Record<HotkeyName, readonly string[]> = IS_MAC ? MAC_HOTKEYS : WINDOWS_HOTKEYS;
const activeHotkeys: Record<HotkeyName, string | null> = { talk: null, panel: null, dismiss: null, privacy: null, stop: null };

function registerHotkeys(handlers: Record<HotkeyName, () => void>): void {
  for (const name of Object.keys(HOTKEY_CHOICES) as HotkeyName[]) {
    for (const accelerator of HOTKEY_CHOICES[name]) {
      let ok = false;
      try {
        ok = globalShortcut.register(accelerator, handlers[name]);
      } catch (err) {
        // Electron throws (not false) for a key it can't use on this keyboard, e.g. "Pause".
        // Without this, every option after it, and every hotkey after it, went unregistered.
        console.warn(`[pyxi] ${accelerator} can't be a shortcut here (${(err as Error).message}); trying the next.`);
      }
      if (ok) {
        activeHotkeys[name] = accelerator;
        break;
      }
      console.warn(`[pyxi] ${accelerator} is taken by another app; trying the next option for "${name}".`);
    }
    if (!activeHotkeys[name]) console.warn(`[pyxi] no free shortcut for "${name}"; use the panel instead.`);
  }
  console.log("[pyxi] hotkeys:", JSON.stringify(activeHotkeys));
}

const pretty = (accelerator: string | null) =>
  IS_MAC ? (macKeyName(accelerator) ?? "unavailable") : (accelerator ?? "unavailable").replace("Control", "Ctrl");

const overlays = new Map<number, BrowserWindow>();
let panel: BrowserWindow | null = null;
/** The firefly: its own small window that follows the cursor (see createFirefly). */
let firefly: BrowserWindow | null = null;
const FIREFLY_SIZE = 160;
let tray: Tray | null = null;
let brain: ChildProcess | null = null;
let primaryId = 0;
let holdToTalk = false;
let muted = process.env.PYXI_MUTE === "1";

// Screen sharing (Discord, OBS, Meet): visible by default so people watching see Pyxi.
// Turn it off for privacy and the overlay is hidden from every capture, Pyxi's own included.
const UI_PREFS = join(HOME, ".pyxi", "ui.json");
function loadShareVisible(): boolean {
  if (process.env.PYXI_SHOW_OVERLAY_IN_CAPTURE === "1") return true;
  if (process.env.PYXI_SHOW_OVERLAY_IN_CAPTURE === "0") return false;
  try {
    return JSON.parse(readFileSync(UI_PREFS, "utf-8")).shareVisible !== false;
  } catch {
    return true;
  }
}
let shareVisible = loadShareVisible();

// Hold-to-talk and dictation chords: PYXI_TALK_CHORD / PYXI_DICTATE_CHORD, else .pyxi/hotkeys.json
// ({"talk": "ctrl+option", "dictate": "ctrl+cmd"}), else Ctrl+Alt and Ctrl+Win (on a Mac,
// Control+Option and Control+Command: the same keys by position).
function loadChords(): ReturnType<typeof chordsFrom> {
  let saved: { talk?: string; dictate?: string } | undefined;
  try {
    saved = JSON.parse(readFileSync(join(HOME, ".pyxi", "hotkeys.json"), "utf-8"));
  } catch {
    saved = undefined;
  }
  return chordsFrom(saved, process.env);
}
const chords = loadChords();

// ---------------------------------------------------------------- the Mac's permissions card
// Pyxi only reads what's allowed and opens the right page of System Settings; the user flips the
// switch. Shown at first start until everything's granted (or they close it with "Later").
const MAC_PERMISSIONS_SEEN = join(HOME, ".pyxi", "mac-permissions.json");
let permissionsWin: BrowserWindow | null = null;

function macPermissionRows() {
  return permissionRows({
    screen: systemPreferences.getMediaAccessStatus("screen"),
    accessibility: systemPreferences.isTrustedAccessibilityClient(false),
    microphone: systemPreferences.getMediaAccessStatus("microphone"),
  });
}

function openPermissions(): void {
  if (!IS_MAC) return;
  if (permissionsWin && !permissionsWin.isDestroyed()) {
    permissionsWin.show();
    permissionsWin.focus();
    return;
  }
  permissionsWin = new BrowserWindow({
    width: 480,
    height: 640,
    resizable: false,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    title: "Pyxi needs a few permissions",
    show: false,
    webPreferences: { ...SAFE_PREFERENCES },
  });
  loadPage(permissionsWin, "permissions", {});
  permissionsWin.once("ready-to-show", () => {
    permissionsWin?.show();
    app.focus({ steal: true });
  });
  permissionsWin.on("closed", () => (permissionsWin = null));
}

function maybeShowPermissions(): void {
  if (!IS_MAC) return;
  let seen = false;
  try {
    seen = JSON.parse(readFileSync(MAC_PERMISSIONS_SEEN, "utf-8")).later === true;
  } catch {
    seen = false;
  }
  if (!seen && needsPermissions(macPermissionRows())) openPermissions();
}

/** Only the permissions page itself may ask these. */
const fromPermissions = (e: Electron.IpcMainEvent | Electron.IpcMainInvokeEvent) =>
  !!permissionsWin && !permissionsWin.isDestroyed() && e.sender === permissionsWin.webContents;

/** Is this one of Pyxi's own pages (the built files, or the dev server while developing)? */
function ownPage(url: string): boolean {
  const dev = process.env.ELECTRON_RENDERER_URL;
  if (dev && url.startsWith(dev.replace(/\/$/, "") + "/")) return true;
  if (!url.startsWith("file://")) return false;
  try {
    const path = resolve(fileURLToPath(url)).toLowerCase();
    return path.startsWith(resolve(join(__dirname, "../renderer")).toLowerCase());
  } catch {
    return false;
  }
}

/** Opens a link in the user's browser: http and https only (never file:, ms-settings:, etc.). */
function openExternalSafe(url: unknown): void {
  if (typeof url !== "string") return;
  try {
    const parsed = new URL(url);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") void shell.openExternal(parsed.toString());
  } catch {
    /* not a URL */
  }
}

// Every window runs Pyxi's own pages with no Node in the page, isolated from the preload, in
// Chromium's sandbox. The preload is the only bridge (see preload/index.ts).
const SAFE_PREFERENCES = {
  preload: join(__dirname, "../preload/index.js"),
  contextIsolation: true,
  nodeIntegration: false,
  nodeIntegrationInWorker: false,
  sandbox: true,
  webSecurity: true,
  allowRunningInsecureContent: false,
  webviewTag: false,
  navigateOnDragDrop: false,
} as const;

type Page = "overlay" | "panel" | "firefly" | "permissions";

function rendererUrl(page: Page, query: Record<string, string>): string | null {
  const dev = process.env.ELECTRON_RENDERER_URL;
  const qs = new URLSearchParams(query).toString();
  return dev ? `${dev}/${page}.html?${qs}` : null;
}

function loadPage(win: BrowserWindow, page: Page, query: Record<string, string>): void {
  const url = rendererUrl(page, query);
  if (url) void win.loadURL(url);
  else void win.loadFile(join(__dirname, `../renderer/${page}.html`), { query });
}

/** Where an overlay sits: the whole display, or on a Mac below the menu bar (and the camera notch
 * of a MacBook) and above the Dock, so Pyxi's notch is never hidden behind either. */
function overlayArea(d: Display): Electron.Rectangle {
  return IS_MAC ? d.workArea : d.bounds;
}

function displayInfo(d: Display) {
  if (IS_MAC) {
    // On a Mac the brain speaks points (Accessibility and Quartz do), the same unit as CSS px
    // here, so the scale between them is 1 whatever the Retina factor.
    const area = overlayArea(d);
    return {
      id: d.id,
      primary: d.id === primaryId,
      bounds: area,
      scaleFactor: 1,
      physOrigin: { x: area.x, y: area.y },
      physSize: { width: area.width, height: area.height },
    };
  }
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
    ...overlayArea(d),
    // A Mac panel floats over full-screen apps and never takes the app's focus by being shown.
    ...(IS_MAC ? { type: "panel" } : {}),
    transparent: true,
    frame: false,
    resizable: false,
    movable: false,
    // Show desktop (Win+D) and "minimize all" must never take the notch away.
    minimizable: false,
    focusable: false,
    skipTaskbar: true,
    hasShadow: false,
    alwaysOnTop: true,
    backgroundColor: "#00000000",
    webPreferences: { ...SAFE_PREFERENCES, backgroundThrottling: false },
  });
  win.setAlwaysOnTop(true, "screen-saver");
  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  win.setIgnoreMouseEvents(true, { forward: true });
  // Visible to screen sharing unless the user turned that off. When visible, the overlay blanks
  // for a moment while Pyxi takes its own screenshot, so it never reads its own drawings.
  win.setContentProtection(!shareVisible);
  const info = displayInfo(d);
  loadPage(win, "overlay", { display: JSON.stringify(info), backend: BACKEND, mute: muted ? "1" : "0" });
  overlays.set(d.id, win);
  // A crashed page reloads; a closed overlay comes back while its screen is still there, so
  // Pyxi never ends up talking to a window that's gone.
  win.webContents.on("render-process-gone", () => {
    if (!win.isDestroyed()) win.reload();
  });
  // If Windows minimizes it anyway (Win+D, Win+M), bring it straight back without stealing focus.
  win.on("minimize", () => {
    setTimeout(() => {
      if (win.isDestroyed()) return;
      win.restore();
      win.showInactive();
      win.setAlwaysOnTop(true, "screen-saver");
    }, 150);
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

/**
 * The firefly lives in its own small window that moves with the cursor. Drawn inside the
 * full-screen overlay, every frame of it made Windows recompose a transparent window the size of
 * the screen (a fifth of a core while the mouse moved); moving a small window costs nearly
 * nothing. It never takes the mouse or the keyboard, and it follows the screen-share setting.
 */
function createFirefly(): void {
  const at = screen.getCursorScreenPoint();
  const win = new BrowserWindow({
    x: at.x,
    y: at.y,
    width: FIREFLY_SIZE,
    height: FIREFLY_SIZE,
    ...(IS_MAC ? { type: "panel" } : {}),
    transparent: true,
    frame: false,
    resizable: false,
    movable: false,
    minimizable: false,
    maximizable: false,
    focusable: false,
    skipTaskbar: true,
    hasShadow: false,
    alwaysOnTop: true,
    show: false,
    backgroundColor: "#00000000",
    webPreferences: { ...SAFE_PREFERENCES, backgroundThrottling: false },
  });
  win.setAlwaysOnTop(true, "screen-saver");
  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  win.setIgnoreMouseEvents(true);
  win.setContentProtection(!shareVisible);
  loadPage(win, "firefly", {});
  // Where to appear before the mouse next moves (the cursor feed only sends changes).
  win.webContents.on("did-finish-load", () => {
    if (!win.isDestroyed()) win.webContents.send("cursor", screen.getCursorScreenPoint());
  });
  win.once("ready-to-show", () => {
    if (!win.isDestroyed()) win.showInactive();
  });
  firefly = win;
  win.webContents.on("render-process-gone", () => {
    if (!win.isDestroyed()) win.reload();
  });
  win.on("minimize", () => {
    setTimeout(() => {
      if (win.isDestroyed()) return;
      win.restore();
      win.showInactive();
      win.setAlwaysOnTop(true, "screen-saver");
    }, 150);
  });
  win.on("closed", () => {
    if (firefly === win) firefly = null;
    if ((app as unknown as { quitting?: boolean }).quitting) return;
    setTimeout(() => {
      if (!firefly) createFirefly();
    }, 600);
  });
}

function liveFirefly(): BrowserWindow | null {
  return firefly && !firefly.isDestroyed() && !firefly.webContents.isDestroyed() ? firefly : null;
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
    // Frosted glass: Windows 11's acrylic, the Mac's own vibrancy.
    ...(IS_MAC ? { vibrancy: "under-window" as const, visualEffectState: "active" as const } : { backgroundMaterial: "acrylic" as const }),
    titleBarStyle: "hidden",
    webPreferences: { ...SAFE_PREFERENCES },
  });
  loadPage(panel, "panel", { backend: BACKEND });
  panel.on("close", (e) => {
    if (!(app as unknown as { quitting?: boolean }).quitting) {
      e.preventDefault();
      panel?.hide();
    }
  });
}

/** Stop everything: every teammate, the hands, Scout's browser, open cards and anything queued.
 * Asked of the brain directly (with the launch key), so it works even if a window is stuck. */
async function stopEverything(): Promise<void> {
  try {
    await fetch(`${BACKEND}/api/stop`, keyed({ method: "POST" }));
  } catch (err) {
    console.warn("[pyxi] stop everything: the brain didn't answer", err);
  }
}

/** Everything lives in the notch now; Ctrl+Alt+W and the tray icon unfold it. */
function toggleNotch(): void {
  const win = overlays.get(primaryId);
  if (win && !win.isDestroyed()) win.webContents.send("notch-toggle");
}

/** The notch, unfolded on one of its tabs (the tray's "Open Settings"). */
function openNotchOn(tab: string): void {
  const win = overlays.get(primaryId);
  if (win && !win.isDestroyed()) win.webContents.send("notch-open", tab);
}

let paused = false;
/** Pause Pyxi: it stops looking at the screen until resumed (the privacy hotkey does the same). */
function togglePause(): void {
  paused = !paused;
  broadcast("privacy-toggle");
  buildTrayMenu();
}

function buildTrayMenu(): void {
  if (!tray) return;
  tray.setContextMenu(
    Menu.buildFromTemplate(
      trayTemplate({
        version: app.getVersion(),
        hotkey: (name) => pretty(activeHotkeys[name]),
        paused,
        muted,
        openPyxi: toggleNotch,
        openSettings: () => openNotchOn("settings"),
        togglePause,
        setMuted: (value) => {
          muted = value;
          broadcast("mute", muted);
        },
        stopEverything: () => void stopEverything(),
        openUrl: (url) => void shell.openExternal(url),
        checkForUpdates: async () => {
          const said = await checkNow();
          new Notification({ title: "Pyxi updates", body: said }).show();
        },
        quit: () => app.quit(),
        ...(IS_MAC ? { openPermissions } : {}),
      }),
    ),
  );
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

/** The brain on our port: its version and whether it took our key, or null when nothing answers. */
/** Is Pyxi busy for them: talking, listening, thinking, or a job running? An update waits. */
async function brainBusy(): Promise<boolean> {
  try {
    const res = await fetch(`${BACKEND}/api/state`, keyed());
    if (!res.ok) return false; // no brain of ours: nothing to interrupt
    const s = (await res.json()) as { state?: string; jobs?: { status?: string }[] };
    return (s.state ?? "idle") !== "idle" || (s.jobs ?? []).some((j) => j.status === "queued" || j.status === "running");
  } catch {
    return false;
  }
}

async function brainHealth(): Promise<{ version: string; ours: boolean } | null> {
  try {
    const res = await fetch(`${BACKEND}/api/health`, keyed());
    if (!res.ok) return null;
    const body = (await res.json()) as { version?: string; authorized?: boolean };
    // Brains before the launch key had no "authorized" and took anything.
    return { version: String(body.version ?? ""), ours: body.authorized !== false };
  } catch {
    return null;
  }
}

/** Developing: a brain started by hand (pyxi serve) wrote its own key to .pyxi/brain.key. */
function handStartedKey(): string | null {
  if (app.isPackaged || process.env.PYXI_BRAIN_KEY) return null;
  try {
    return readFileSync(join(HOME, ".pyxi", "brain.key"), "utf-8").trim() || null;
  } catch {
    return null;
  }
}

async function ensureBrain(): Promise<void> {
  let running = await brainHealth();
  const fileKey = running && !running.ours ? handStartedKey() : null;
  if (fileKey) {
    brainKey = fileKey;
    running = await brainHealth();
  }
  // Reuse a brain only if it's ours and this version. After an update an old brain can still be
  // running (left behind when the installer closed the app): ask it to quit, then start our own.
  if (running?.ours && (running.version === app.getVersion() || !app.isPackaged)) return;
  if (running !== null) {
    await fetch(`${BACKEND}/api/quit`, keyed({ method: "POST" })).catch(() => undefined);
    // A brain with another key ignores that, but quits by itself when its app has gone.
    for (let i = 0; i < 50 && (await brainHealth()) !== null; i++) await new Promise((r) => setTimeout(r, 200));
  }
  // The installed app carries its own Python with the brain already in it (resources/python).
  const py =
    process.env.PYXI_PYTHON ??
    (app.isPackaged
      ? join(process.resourcesPath, "python", IS_MAC ? "bin/python3" : "python.exe")
      : join(REPO, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python"));
  if (!existsSync(py)) {
    console.warn(`[pyxi] Python not found at ${py}; start the brain yourself: pyxi serve`);
    return;
  }
  const args = ["-m", "pyxi", "serve", ...(process.env.PYXI_ARGS?.split(" ").filter(Boolean) ?? [])];
  mkdirSync(join(HOME, ".pyxi"), { recursive: true });
  // Installed, there's no terminal: the brain's log goes to .pyxi/brain.log for support.
  const log = app.isPackaged ? openSync(join(HOME, ".pyxi", "brain.log"), "a") : null;
  brain = spawn(py, args, {
    cwd: HOME,
    stdio: log === null ? "inherit" : ["ignore", log, log],
    windowsHide: true,
    // PYXI_APP_PID: the brain quits when this app does, so it's never left running on its own.
    env: {
      ...process.env,
      PYTHONUTF8: "1",
      PYTHONNOUSERSITE: "1",
      PYXI_APP_PID: String(process.pid),
      PYXI_BRAIN_KEY: brainKey,
    },
  });
  brain.on("exit", (code) => {
    console.log(`[pyxi] brain exited (${code})`);
    // 75: the brain asked to be restarted (it just joined the beta, so its models changed).
    if (code === 75) setTimeout(() => void ensureBrain(), 300);
  });
}

/** Wakes the cursor feed (the mouse hook calls it on every move). */
let wakeCursorFeed = (): void => undefined;

/** The cursor, to every overlay, about 60 times a second while the mouse moves. Once it has
 * been still for half a second the feed stops until the mouse hook reports a move, so an idle
 * Pyxi doesn't wake 60 times a second. Without the hook it checks four times a second. */
function startCursorFeed(): void {
  let last = { x: -1, y: -1 };
  let timer: NodeJS.Timeout | null = null;
  let still = 0;
  const tick = (): void => {
    const p = screen.getCursorScreenPoint();
    if (p.x !== last.x || p.y !== last.y) {
      last = p;
      still = 0;
      for (const win of liveOverlays()) win.webContents.send("cursor", p);
      liveFirefly()?.webContents.send("cursor", p);
    } else if (++still > 15 && timer) {
      clearInterval(timer);
      timer = null;
      if (!holdToTalk) setTimeout(wakeCursorFeed, 250);
    }
  };
  wakeCursorFeed = () => {
    if (timer) return;
    still = 0;
    // About 30 times a second: the firefly is drawn that often, and hover needs no more.
    timer = setInterval(tick, 33);
  };
  wakeCursorFeed();
}

function trayIcon(): Electron.NativeImage {
  if (IS_MAC) {
    // The menu bar wants a template image (black with alpha; macOS tints it for light and dark),
    // 22 points tall, with a Retina version. Drawn in code and packed as PNG: an SVG data URL
    // comes out as an empty image in Electron (main's Windows tray fix loads PNGs the same way).
    const img = nativeImage.createFromBuffer(trayTemplatePng(22), { scaleFactor: 1 });
    img.addRepresentation({ scaleFactor: 2, buffer: trayTemplatePng(44) });
    img.setTemplateImage(true);
    if (img.isEmpty()) console.warn("[pyxi] menu bar icon came out empty");
    return img;
  }
  // The firefly in small, drawn in code so the repo needs no binary assets.
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="-16 -16 32 32"><defs><radialGradient id="g"><stop offset="0" stop-color="#ffe08a" stop-opacity=".9"/><stop offset=".55" stop-color="#ffc861" stop-opacity=".35"/><stop offset="1" stop-color="#ffc861" stop-opacity="0"/></radialGradient></defs><circle r="15" fill="url(#g)"/><ellipse cx="-4.5" cy="-6" rx="5" ry="2.8" transform="rotate(-32 -4.5 -6)" fill="#fffaeb" fill-opacity=".55"/><ellipse cx="4.5" cy="-6" rx="5" ry="2.8" transform="rotate(32 4.5 -6)" fill="#fffaeb" fill-opacity=".55"/><circle r="6" fill="#fff3cf"/></svg>`;
  return nativeImage.createFromDataURL(`data:image/svg+xml;base64,${Buffer.from(svg).toString("base64")}`);
}

// No page can open new windows, navigate an app window away, or embed a webview. Links a page
// asks to open go to the user's browser (http and https only).
app.on("web-contents-created", (_e, contents) => {
  contents.setWindowOpenHandler(({ url }) => {
    openExternalSafe(url);
    return { action: "deny" };
  });
  const stay = (e: Electron.Event, url: string) => {
    if (!ownPage(url)) e.preventDefault();
  };
  contents.on("will-navigate", stay);
  contents.on("will-redirect", stay);
  contents.on("will-attach-webview", (e) => e.preventDefault());
});

// One Pyxi at a time: opening it again (a second double-click) unfolds the notch of the one
// that's running instead of starting a second notch, hotkeys and brain.
if (!app.requestSingleInstanceLock()) {
  app.exit(0);
} else {
  app.on("second-instance", () => toggleNotch());
}

app.whenReady().then(async () => {
  if (!app.hasSingleInstanceLock()) return;
  // A Mac menu bar helper: no Dock icon (the menu bar icon and the notch are the interface).
  if (IS_MAC) app.dock?.hide();
  startUpdates(broadcast, brainBusy);
  // The microphone, for Pyxi's own pages only; nothing else (camera, location, notifications...).
  session.defaultSession.setPermissionRequestHandler((_wc, permission, cb, details) =>
    cb(permission === "media" && ownPage(details.requestingUrl ?? "")),
  );
  // Permission checks (no prompt) pass for Pyxi's own pages only.
  session.defaultSession.setPermissionCheckHandler((_wc, _permission, origin) => origin.startsWith("file://") || ownPage(origin + "/"));
  primaryId = screen.getPrimaryDisplay().id;
  await ensureBrain();
  for (const d of screen.getAllDisplays()) createOverlay(d);
  createFirefly();
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
      liveFirefly()?.moveTop(); // above the new overlays
    }, 500);
  };
  screen.on("display-added", rebuildOverlays);
  screen.on("display-removed", rebuildOverlays);
  screen.on("display-metrics-changed", (_e, _d, changed) => {
    if (changed.some((c) => c === "bounds" || c === "scaleFactor" || c === "workArea")) rebuildOverlays();
  });

  // Hold Ctrl + left Alt to talk (the main way to use Pyxi). The shortcut below stays as a
  // press-to-toggle fallback, and also covers keyboards where the hook can't start.
  holdToTalk = startHoldToTalk(
    {
    down: () => broadcast("ptt-down"),
    up: () => broadcast("ptt-up"),
    cancel: () => broadcast("ptt-cancel"),
    // Hold Ctrl + left Windows: dictation, typed into whatever app has focus.
    dictateDown: () => broadcast("dictate-down"),
    dictateUp: () => broadcast("dictate-up"),
    dictateCancel: () => broadcast("dictate-cancel"),
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
    move: () => wakeCursorFeed(),
    },
    { talk: chords.talk, dictate: chords.dictate },
  );
  registerHotkeys({
    talk: () => broadcast("ptt"),
    panel: toggleNotch,
    dismiss: () => broadcast("dismiss"),
    privacy: togglePause,
    stop: () => void stopEverything(),
  });

  tray = new Tray(trayIcon());
  tray.setToolTip("Pyxi");
  tray.on("click", toggleNotch);
  buildTrayMenu();
  maybeShowPermissions();
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
// The preload asks for the launch key once per page; only Pyxi's own pages get it.
ipcMain.on("brain-key", (e) => {
  e.returnValue = ownPage(e.senderFrame?.url ?? "") ? brainKey : "";
});

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
// After typing to Pyxi, the keyboard goes back to the app you were in.
ipcMain.on("release-focus", (e) => {
  const win = BrowserWindow.fromWebContents(e.sender);
  if (!win || win.isDestroyed()) return;
  win.blur();
  win.setFocusable(false);
});
ipcMain.on("ptt", () => broadcast("ptt"));
ipcMain.on("open-external", (_e, url: unknown) => openExternalSafe(url));
ipcMain.handle("displays", () => screen.getAllDisplays().map(displayInfo));
// Quick actions. Inserting pastes into whatever field has focus, then puts your clipboard back.
ipcMain.on("action", async (_e, kind: string, value: string) => {
  if (typeof value !== "string" || !value) return;
  if (kind === "open_url") openExternalSafe(value);
  else if (kind === "copy") clipboard.writeText(value);
  else if (kind === "insert") {
    const focused = BrowserWindow.getFocusedWindow();
    if (focused) {
      // Typing went to Pyxi's own window (the notch text box); hand focus back first.
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
// Files Pyxi made: open them, show them in Explorer or drag them out, but only inside Pyxi's own
// folders, or listed by the brain in .pyxi/made.json (Pixel and Ledger save beside the original,
// on the Desktop or in Downloads). A page can't name any other file on the PC.
function madeByPyxi(full: string): boolean {
  try {
    const list = JSON.parse(readFileSync(join(HOME, ".pyxi", "made.json"), "utf-8"));
    return Array.isArray(list) && list.some((p) => typeof p === "string" && resolve(p).toLowerCase() === full.toLowerCase());
  } catch {
    return false;
  }
}
function pyxiFile(path: unknown): string | null {
  if (typeof path !== "string" || !path) return null;
  const full = resolve(path);
  const allowed = [join(app.getPath("documents"), "Pyxi"), join(HOME, ".pyxi", "outputs")].map((d) => resolve(d).toLowerCase());
  const inside = allowed.some((d) => full.toLowerCase().startsWith(d + "\\") || full.toLowerCase().startsWith(d + "/"));
  return (inside || madeByPyxi(full)) && existsSync(full) ? full : null;
}
// What a result card shows about a file: its name, size, folder, and Windows' own thumbnail (the
// PDF's first page, the photo itself) or else its file-type icon.
const dragIcons = new Map<string, Electron.NativeImage>();
ipcMain.handle("file-info", async (_e, path: unknown) => {
  const file = pyxiFile(path);
  if (!file) return null;
  let size = 0;
  try {
    size = statSync(file).size;
  } catch {
    return null;
  }
  let thumb: string | null = null;
  try {
    const img = await nativeImage.createThumbnailFromPath(file, { width: 220, height: 280 });
    if (!img.isEmpty()) thumb = img.toDataURL();
  } catch {
    // no thumbnail handler for this kind of file
  }
  let icon: string | null = null;
  try {
    const ico = await app.getFileIcon(file, { size: "large" });
    dragIcons.set(file.toLowerCase(), ico);
    icon = ico.toDataURL();
  } catch {
    // no icon either: the card draws a badge
  }
  const folder = dirname(file);
  return { name: basename(file), size, folder, folderName: basename(folder) || folder, thumb, icon };
});
// Drag the file out of the card into an email, a chat or a folder.
ipcMain.on("drag-file", (e, path: unknown) => {
  const file = pyxiFile(path);
  if (!file) return;
  e.sender.startDrag({ file, icon: dragIcons.get(file.toLowerCase()) ?? trayIcon() });
});
// Opening runs whatever Windows associates with the file, so programs and scripts are never
// opened, even inside Pyxi's folders (a downloaded attachment could be one): shown in Explorer.
const RUNNABLE = /\.(exe|com|bat|cmd|scr|pif|cpl|msi|msp|msc|ps1|psm1|vbs|vbe|js|jse|wsf|wsh|hta|lnk|url|reg|jar|appx|appref-ms|application|gadget|scf|inf|chm|dll|sys)$/i;
function openSafe(file: string): void {
  if (RUNNABLE.test(file) || (IS_MAC && MAC_RUNNABLE.test(file))) shell.showItemInFolder(file);
  else void shell.openPath(file);
}
ipcMain.on("open-file", (_e, path: unknown) => {
  const file = pyxiFile(path);
  if (file) openSafe(file);
});
ipcMain.on("show-in-folder", (_e, path: unknown) => {
  const file = pyxiFile(path);
  if (file) shell.showItemInFolder(file);
});
ipcMain.on("notify", (_e, title: string, body: string, path?: string) => {
  if (!Notification.isSupported()) return;
  const note = new Notification({ title: String(title).slice(0, 80), body: String(body).slice(0, 200), silent: true });
  const file = pyxiFile(path);
  if (file) note.on("click", () => openSafe(file));
  note.show();
});
ipcMain.handle("share-visible", () => shareVisible);
ipcMain.on("set-share-visible", (_e, value: boolean) => {
  shareVisible = !!value;
  for (const win of liveOverlays()) win.setContentProtection(!shareVisible);
  liveFirefly()?.setContentProtection(!shareVisible);
  try {
    mkdirSync(join(HOME, ".pyxi"), { recursive: true });
    writeFileSync(UI_PREFS, JSON.stringify({ shareVisible }));
  } catch (err) {
    console.warn("[pyxi] couldn't save the screen-share setting:", err);
  }
  broadcast("share-visible", shareVisible);
});
// The firefly moves its own window (it runs the spring); only the firefly page may.
ipcMain.on("firefly-move", (e, x: unknown, y: unknown) => {
  const win = liveFirefly();
  if (!win || e.sender !== win.webContents || typeof x !== "number" || typeof y !== "number") return;
  if (!Number.isFinite(x) || !Number.isFinite(y)) return;
  // setBounds, not setPosition: crossing to a screen with other scaling must not resize it.
  win.setBounds({ x: Math.round(x), y: Math.round(y), width: FIREFLY_SIZE, height: FIREFLY_SIZE });
});
// Pyxi's mood and voice level for the firefly, from the main screen's overlay.
ipcMain.on("firefly-state", (e, s: unknown) => {
  if (!liveOverlays().some((w) => w.webContents === e.sender) || typeof s !== "object" || s === null) return;
  liveFirefly()?.webContents.send("firefly-state", s);
});
ipcMain.handle("hotkeys", () =>
  IS_MAC
    ? {
        ...Object.fromEntries(Object.entries(activeHotkeys).map(([k, v]) => [k, macKeyName(v)])),
        hold: holdToTalk ? chordLabel(chords.talk) : null,
        dictate: holdToTalk ? chordLabel(chords.dictate) : null,
      }
    : {
        ...activeHotkeys,
        hold: holdToTalk ? chordLabel(chords.talk) : null,
        dictate: holdToTalk ? chordLabel(chords.dictate) : null,
      },
);
// The Mac's permissions card (permissions.html): what's allowed, and links to System Settings.
ipcMain.handle("mac-permissions", (e) => (IS_MAC && fromPermissions(e) ? macPermissionRows() : []));
ipcMain.on("mac-open-settings", (e, key: unknown) => {
  const url = IS_MAC && fromPermissions(e) ? settingsUrl(key) : null;
  if (url) void shell.openExternal(url);
});
// The microphone is the one macOS lets an app ask for itself: its own prompt, the user's answer.
ipcMain.handle("mac-ask-microphone", async (e) =>
  IS_MAC && fromPermissions(e) ? systemPreferences.askForMediaAccess("microphone") : false,
);
ipcMain.on("mac-permissions-later", (e) => {
  if (!IS_MAC || !fromPermissions(e)) return;
  try {
    mkdirSync(join(HOME, ".pyxi"), { recursive: true });
    writeFileSync(MAC_PERMISSIONS_SEEN, JSON.stringify({ later: true }));
  } catch (err) {
    console.warn("[pyxi] couldn't save the permissions choice:", err);
  }
  permissionsWin?.close();
});
// Screen Recording only takes effect once the app starts again (macOS says so too).
ipcMain.on("mac-restart", (e) => {
  if (!IS_MAC || !fromPermissions(e)) return;
  app.relaunch();
  app.quit();
});
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
