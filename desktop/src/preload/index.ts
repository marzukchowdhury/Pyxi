// The only bridge between the renderer pages and Electron: a small, explicit API.
import { contextBridge, ipcRenderer } from "electron";

type Listener<T> = (value: T) => void;

function on<T>(channel: string, fn: Listener<T>): () => void {
  const handler = (_e: unknown, value: T) => fn(value);
  ipcRenderer.on(channel, handler);
  return () => ipcRenderer.removeListener(channel, handler);
}

contextBridge.exposeInMainWorld("pyxiBridge", {
  electron: true,
  // Asked once at load. The main process only gives it to Pyxi's own pages.
  brainKey: String(ipcRenderer.sendSync("brain-key") ?? ""),
  onCursor: (fn: Listener<{ x: number; y: number }>) => on("cursor", fn),
  onPtt: (fn: Listener<void>) => on("ptt", fn),
  onPttDown: (fn: Listener<void>) => on("ptt-down", fn),
  onPttUp: (fn: Listener<void>) => on("ptt-up", fn),
  onPttCancel: (fn: Listener<void>) => on("ptt-cancel", fn),
  onQuickType: (fn: Listener<void>) => on("quick-type", fn),
  onUpdateReady: (fn: Listener<{ version: string }>) => on("update-ready", fn),
  installUpdate: () => ipcRenderer.send("install-update"),
  onGlobalClick: (fn: Listener<{ x: number; y: number }>) => on("global-click", fn),
  focusOverlay: () => ipcRenderer.send("focus-overlay"),
  runAction: (kind: string, value: string) => ipcRenderer.send("action", kind, value),
  openFile: (path: string) => ipcRenderer.send("open-file", path),
  showInFolder: (path: string) => ipcRenderer.send("show-in-folder", path),
  fileInfo: (path: string) => ipcRenderer.invoke("file-info", path),
  dragFile: (path: string) => ipcRenderer.send("drag-file", path),
  notify: (title: string, body: string, file?: string) => ipcRenderer.send("notify", title, body, file),
  onDismiss: (fn: Listener<void>) => on("dismiss", fn),
  onPrivacyToggle: (fn: Listener<void>) => on("privacy-toggle", fn),
  onMuteToggle: (fn: Listener<boolean>) => on("mute", fn),
  onNotchToggle: (fn: Listener<void>) => on("notch-toggle", fn),
  onNotchOpen: (fn: Listener<string>) => on("notch-open", fn),
  shareVisible: () => ipcRenderer.invoke("share-visible"),
  setShareVisible: (value: boolean) => ipcRenderer.send("set-share-visible", value),
  onShareVisible: (fn: Listener<boolean>) => on("share-visible", fn),
  setInteractive: (interactive: boolean) => ipcRenderer.send("set-interactive", interactive),
  togglePanel: () => ipcRenderer.send("toggle-panel"),
  ptt: () => ipcRenderer.send("ptt"),
  openExternal: (url: string) => ipcRenderer.send("open-external", url),
  displays: () => ipcRenderer.invoke("displays"),
  hotkeys: () => ipcRenderer.invoke("hotkeys"),
  voicePlay: (clip: unknown) => ipcRenderer.send("voice-play", clip),
  onVoicePlay: (fn: Listener<unknown>) => on("voice-play", fn),
  voiceEvent: (msg: unknown) => ipcRenderer.send("voice-event", msg),
  onVoiceEvent: (fn: Listener<unknown>) => on("voice-event", fn),
  voiceStop: (ids?: string[]) => ipcRenderer.send("voice-stop", ids),
  onVoiceStop: (fn: Listener<unknown>) => on("voice-stop", fn),
  sayStarted: (key: string) => ipcRenderer.send("say-started", key),
  onSayStarted: (fn: Listener<string>) => on("say-started", fn),
  releaseFocus: () => ipcRenderer.send("release-focus"),
  fireflyMove: (x: number, y: number) => ipcRenderer.send("firefly-move", x, y),
  fireflyState: (s: unknown) => ipcRenderer.send("firefly-state", s),
  onFireflyState: (fn: Listener<unknown>) => on("firefly-state", fn),
});
