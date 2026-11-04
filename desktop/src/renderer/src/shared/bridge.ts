// window.wispBridge comes from the Electron preload. In a plain browser (vite.web.config.ts)
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
  onCursor(fn: (p: { x: number; y: number }) => void): () => void;
  onPtt(fn: () => void): () => void;
  onDismiss(fn: () => void): () => void;
  onPrivacyToggle(fn: () => void): () => void;
  setInteractive(interactive: boolean): void;
  togglePanel(): void;
  ptt(): void;
  openExternal(url: string): void;
  displays(): Promise<DisplayInfo[]>;
}

declare global {
  interface Window {
    wispBridge?: Bridge;
  }
}

function mockBridge(): Bridge {
  const pttListeners = new Set<() => void>();
  const noop = () => () => {};
  return {
    electron: false,
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
    onDismiss: noop,
    onPrivacyToggle: noop,
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
  };
}

export const bridge: Bridge = window.wispBridge ?? mockBridge();

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
