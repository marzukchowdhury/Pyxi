// Updates without reinstalling: the installed app checks Pyxi's GitHub releases, downloads a new
// version quietly in the background (only the changed parts, thanks to the blockmap), then the
// notch offers "Restart to update". Nothing happens in development, or without a release.
// What happened is written to .pyxi/updates.log in Pyxi's data folder, for support.
import { appendFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { app, ipcMain } from "electron";
import electronUpdater from "electron-updater";
import { scrubLine } from "./scrub";

const { autoUpdater } = electronUpdater;
const EVERY = 4 * 60 * 60 * 1000; // check again every four hours while Pyxi is open

function note(line: string): void {
  try {
    const dir = join(app.getPath("userData"), ".pyxi");
    mkdirSync(dir, { recursive: true });
    appendFileSync(join(dir, "updates.log"), `${new Date().toISOString()} ${scrubLine(line)}\n`);
  } catch {
    /* logging must never break updating */
  }
}

export function startUpdates(tell: (channel: string, ...args: unknown[]) => void): void {
  if (!app.isPackaged) return;
  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = true; // quitting Pyxi normally also applies a waiting update
  autoUpdater.on("checking-for-update", () => note(`checking (this is ${app.getVersion()})`));
  autoUpdater.on("update-not-available", () => note("up to date"));
  autoUpdater.on("update-available", (info) => note(`downloading ${info.version}`));
  autoUpdater.on("update-downloaded", (info) => {
    note(`ready: ${info.version}`);
    tell("update-ready", { version: info.version });
  });
  autoUpdater.on("error", (err) => note(`error: ${err?.message ?? err}`));
  const check = () => void autoUpdater.checkForUpdates().catch(() => undefined);
  setTimeout(check, 20_000); // after startup settles, not during it
  setInterval(check, EVERY);
  ipcMain.on("install-update", () => {
    note("installing");
    // Silent install, then Pyxi opens again on its own. The brain is stopped by before-quit.
    autoUpdater.quitAndInstall(true, true);
  });
}
