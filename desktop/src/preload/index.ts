// The only bridge between the renderer pages and Electron: a small, explicit API.
import { contextBridge, ipcRenderer } from "electron";

type Listener<T> = (value: T) => void;

function on<T>(channel: string, fn: Listener<T>): () => void {
  const handler = (_e: unknown, value: T) => fn(value);
  ipcRenderer.on(channel, handler);
  return () => ipcRenderer.removeListener(channel, handler);
}

contextBridge.exposeInMainWorld("wispBridge", {
  electron: true,
  onCursor: (fn: Listener<{ x: number; y: number }>) => on("cursor", fn),
  onPtt: (fn: Listener<void>) => on("ptt", fn),
  onPttDown: (fn: Listener<void>) => on("ptt-down", fn),
  onPttUp: (fn: Listener<void>) => on("ptt-up", fn),
  onPttCancel: (fn: Listener<void>) => on("ptt-cancel", fn),
  onQuickType: (fn: Listener<void>) => on("quick-type", fn),
  onGlobalClick: (fn: Listener<{ x: number; y: number }>) => on("global-click", fn),
  focusOverlay: () => ipcRenderer.send("focus-overlay"),
  runAction: (kind: string, value: string) => ipcRenderer.send("action", kind, value),
  openFile: (path: string) => ipcRenderer.send("open-file", path),
  showInFolder: (path: string) => ipcRenderer.send("show-in-folder", path),
  notify: (title: string, body: string, file?: string) => ipcRenderer.send("notify", title, body, file),
  onDismiss: (fn: Listener<void>) => on("dismiss", fn),
  onPrivacyToggle: (fn: Listener<void>) => on("privacy-toggle", fn),
  onMuteToggle: (fn: Listener<boolean>) => on("mute", fn),
  onNotchToggle: (fn: Listener<void>) => on("notch-toggle", fn),
  shareVisible: () => ipcRenderer.invoke("share-visible"),
  setShareVisible: (value: boolean) => ipcRenderer.send("set-share-visible", value),
  onShareVisible: (fn: Listener<boolean>) => on("share-visible", fn),
  setInteractive: (interactive: boolean) => ipcRenderer.send("set-interactive", interactive),
  togglePanel: () => ipcRenderer.send("toggle-panel"),
  ptt: () => ipcRenderer.send("ptt"),
  openExternal: (url: string) => ipcRenderer.send("open-external", url),
  displays: () => ipcRenderer.invoke("displays"),
  hotkeys: () => ipcRenderer.invoke("hotkeys"),
});
