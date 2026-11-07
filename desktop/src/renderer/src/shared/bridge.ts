// window.pyxiBridge comes from the Electron preload. In a plain browser (vite.web.config.ts)
// a mock stands in, so every page can be previewed and tested without Electron.

export interface DisplayInfo {
  id: number;
  primary: boolean;
  bounds: { x: number; y: number; width: number; height: number };
  scaleFactor: number;
  physOrigin: { x: number; y: number };
  physSize: { width: number; height: number };
}

export interface Bridge {
  electron: boolean;
  /** The brain's launch key: every request to the brain must carry it (see pyxi/brainkey.py). */
  brainKey: string;
  onCursor(fn: (p: { x: number; y: number }) => void): () => void;
  onPtt(fn: () => void): () => void;
  onPttDown(fn: () => void): () => void;
  onPttUp(fn: () => void): () => void;
  onPttCancel(fn: () => void): () => void;
  onQuickType(fn: () => void): () => void;
  /** A new version finished downloading in the background (installed app only). */
  onUpdateReady(fn: (info: { version: string }) => void): () => void;
  installUpdate(): void;
  /** Mouse clicks anywhere on the desktop, in physical screen px (only used for guides). */
  onGlobalClick(fn: (p: { x: number; y: number }) => void): () => void;
  /** Quick actions from the brain: open a URL, copy text, or paste a draft into the focused field. */
  runAction(kind: "open_url" | "copy" | "insert", value: string): void;
  /** Open a file Pyxi made, or show it in Explorer (only files in Pyxi's own folders). */
  openFile(path: string): void;
  showInFolder(path: string): void;
  /** A quiet Windows notification; clicking it opens the file. */
  notify(title: string, body: string, file?: string): void;
  focusOverlay(): void;
  onDismiss(fn: () => void): () => void;
  onPrivacyToggle(fn: () => void): () => void;
  onMuteToggle(fn: (muted: boolean) => void): () => void;
  /** Ctrl+Alt+W or the tray icon: unfold or fold the full notch. */
  onNotchToggle(fn: () => void): () => void;
  /** Whether screen sharing (Discord, OBS, Meet) shows Pyxi's overlay. */
  shareVisible(): Promise<boolean>;
  setShareVisible(value: boolean): void;
  onShareVisible(fn: (value: boolean) => void): () => void;
  setInteractive(interactive: boolean): void;
  togglePanel(): void;
  ptt(): void;
  openExternal(url: string): void;
  displays(): Promise<DisplayInfo[]>;
  /** One voice across monitors: other overlays send clips to the main screen's player. */
  voicePlay(clip: { id: string; b64: string | null; text: string }): void;
  onVoicePlay(fn: (clip: { id: string; from: number; b64: string | null; text: string }) => void): () => void;
  voiceEvent(msg: { id: string; from: number; kind: "start" | "end" | "cancel" }): void;
  onVoiceEvent(fn: (msg: { id: string; kind: "start" | "end" | "cancel" }) => void): () => void;
  /** No ids: stop everything. With ids: only those clips from this window. */
  voiceStop(ids?: string[]): void;
  onVoiceStop(fn: (msg: { from: number; ids?: string[] }) => void): () => void;
  /** The main screen started saying a line: other monitors reveal its cue-card points now. */
  sayStarted(key: string): void;
  onSayStarted(fn: (key: string) => void): () => void;
  /** Hand the keyboard back to the app the user was in. */
  releaseFocus(): void;
  hotkeys(): Promise<Record<"talk" | "panel" | "dismiss" | "privacy" | "stop" | "hold", string | null>>;
}

declare global {
  interface Window {
    pyxiBridge?: Bridge;
  }
}

function mockBridge(): Bridge {
  const pttListeners = new Set<() => void>();
  const noop = () => () => {};
  return {
    voicePlay: () => {},
    onVoicePlay: noop,
    voiceEvent: () => {},
    onVoiceEvent: noop,
    voiceStop: () => {},
    onVoiceStop: noop,
    sayStarted: () => {},
    onSayStarted: noop,
    releaseFocus: () => {},
    electron: false,
    // Browser preview: pass the key of a brain started by hand (.pyxi/brain.key) as ?key=.
    brainKey: new URLSearchParams(location.search).get("key") ?? "",
    onCursor(fn) {
      const h = (e: MouseEvent) => fn({ x: e.clientX, y: e.clientY });
      window.addEventListener("mousemove", h);
      return () => window.removeEventListener("mousemove", h);
    },
    onPtt(fn) {
      pttListeners.add(fn);
      const key = (e: KeyboardEvent) => e.ctrlKey && e.altKey && e.code === "Space" && fn();
      window.addEventListener("keydown", key);
      return () => {
        pttListeners.delete(fn);
        window.removeEventListener("keydown", key);
      };
    },
    // Browser preview: hold Ctrl + Alt works while the page has focus.
    onPttDown(fn) {
      const h = (e: KeyboardEvent) => e.ctrlKey && e.altKey && (e.key === "Control" || e.key === "Alt") && !e.repeat && fn();
      window.addEventListener("keydown", h);
      return () => window.removeEventListener("keydown", h);
    },
    onPttUp(fn) {
      const h = (e: KeyboardEvent) => (e.key === "Control" || e.key === "Alt") && fn();
      window.addEventListener("keyup", h);
      return () => window.removeEventListener("keyup", h);
    },
    onPttCancel: noop,
    onGlobalClick(fn) {
      const h = (e: MouseEvent) => fn({ x: e.clientX, y: e.clientY });
      window.addEventListener("mousedown", h);
      return () => window.removeEventListener("mousedown", h);
    },
    // Browser preview: double-tap Ctrl while the page has focus.
    onUpdateReady: () => () => {},
    installUpdate: () => {},
    onQuickType(fn) {
      let last = 0;
      const h = (e: KeyboardEvent) => {
        if (e.key !== "Control" || e.repeat) return;
        const now = performance.now();
        if (now - last < 360) {
          last = 0;
          fn();
        } else last = now;
      };
      window.addEventListener("keyup", h);
      return () => window.removeEventListener("keyup", h);
    },
    focusOverlay() {},
    openFile(path) {
      console.info("open", path);
    },
    showInFolder(path) {
      console.info("show in folder", path);
    },
    notify() {},
    runAction(kind, value) {
      if (kind === "open_url") window.open(value, "_blank");
      else void navigator.clipboard?.writeText(value);
    },
    onDismiss: noop,
    onPrivacyToggle: noop,
    onMuteToggle: noop,
    async shareVisible() {
      return true;
    },
    setShareVisible() {},
    onShareVisible: noop,
    onNotchToggle(fn) {
      const h = (e: KeyboardEvent) => e.ctrlKey && e.altKey && e.code === "KeyW" && fn();
      window.addEventListener("keydown", h);
      return () => window.removeEventListener("keydown", h);
    },
    setInteractive() {},
    togglePanel() {},
    ptt() {
      pttListeners.forEach((fn) => fn());
    },
    openExternal(url) {
      window.open(url, "_blank");
    },
    async displays() {
      return [currentDisplay()];
    },
    async hotkeys() {
      return { talk: "Control+Alt+Space", panel: "Control+Alt+W", dismiss: "Control+Alt+Backspace", privacy: "Control+Alt+P", stop: "Control+Alt+Pause", hold: "Control+Alt" };
    },
  };
}

export const bridge: Bridge = window.pyxiBridge ?? mockBridge();

const params = new URLSearchParams(location.search);

export function currentDisplay(): DisplayInfo {
  const raw = params.get("display");
  if (raw) return JSON.parse(raw) as DisplayInfo;
  // Browser preview: pretend the viewport is a primary display at the origin.
  return {
    id: 0,
    primary: true,
    bounds: { x: 0, y: 0, width: innerWidth, height: innerHeight },
    scaleFactor: Number(params.get("scale") ?? 1),
    physOrigin: { x: 0, y: 0 },
    physSize: { width: innerWidth, height: innerHeight },
  };
}

export const backendUrl = params.get("backend") ?? "http://127.0.0.1:8766";

/** A brain URL that carries the launch key (WebSocket, <img>, textures and fetches alike). */
export function brainUrl(path: string, base: string = backendUrl): string {
  const url = new URL(path, base);
  url.searchParams.set("key", bridge.brainKey ?? "");
  return url.toString();
}

/** "Control+Alt+Space" -> ["Ctrl", "Alt", "Space"] for display. */
export function keysOf(accelerator: string | null | undefined): string[] {
  return accelerator ? accelerator.split("+").map((k) => (k === "Control" ? "Ctrl" : k)) : [];
}

/** Physical screen px (what the brain sends) -> CSS px inside this overlay window. */
export function toLocal(x: number, y: number, d: DisplayInfo = currentDisplay()): [number, number] {
  return [(x - d.physOrigin.x) / d.scaleFactor, (y - d.physOrigin.y) / d.scaleFactor];
}

export function rectToLocal(r: [number, number, number, number], d: DisplayInfo = currentDisplay()) {
  const [x, y] = toLocal(r[0], r[1], d);
  return { x, y, w: r[2] / d.scaleFactor, h: r[3] / d.scaleFactor };
}

/** Is a physical-px point on this overlay's display? */
export function onThisDisplay(x: number, y: number, d: DisplayInfo = currentDisplay()): boolean {
  return x >= d.physOrigin.x && y >= d.physOrigin.y && x < d.physOrigin.x + d.physSize.width && y < d.physOrigin.y + d.physSize.height;
}

/** Cursor points from Electron are global DIP; convert to this window's CSS px. */
export function cursorToLocal(p: { x: number; y: number }, d: DisplayInfo = currentDisplay()): [number, number] | null {
  if (!bridge.electron) return [p.x, p.y];
  const { x, y, width, height } = d.bounds;
  if (p.x < x || p.y < y || p.x >= x + width || p.y >= y + height) return null;
  return [p.x - x, p.y - y];
}
