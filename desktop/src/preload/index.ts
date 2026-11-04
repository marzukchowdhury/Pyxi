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
  onDismiss: (fn: Listener<void>) => on("dismiss", fn),
  onPrivacyToggle: (fn: Listener<void>) => on("privacy-toggle", fn),
  setInteractive: (interactive: boolean) => ipcRenderer.send("set-interactive", interactive),
  togglePanel: () => ipcRenderer.send("toggle-panel"),
  ptt: () => ipcRenderer.send("ptt"),
  openExternal: (url: string) => ipcRenderer.send("open-external", url),
  displays: () => ipcRenderer.invoke("displays"),
});
