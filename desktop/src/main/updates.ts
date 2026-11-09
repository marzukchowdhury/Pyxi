// Updates without reinstalling: the installed app checks Pyxi's GitHub releases and downloads a
// new version quietly in the background (only the changed parts, thanks to the blockmap). The
// notch then offers "Update app", and Pyxi also installs it by itself once the PC has been idle
// for a few minutes and Pyxi isn't busy (talking, thinking, or running a job). Either way a small
// "Updating Pyxi" window shows the progress, and Pyxi opens again on its own.
// Nothing happens in development, or without a release.
// What happened is written to .pyxi/updates.log in Pyxi's data folder, for support.
import { appendFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { app, ipcMain, powerMonitor } from "electron";
import electronUpdater from "electron-updater";
import { scrubLine } from "./scrub";
import { goodMomentToUpdate } from "./updateTiming";

const { autoUpdater } = electronUpdater;
const EVERY = 4 * 60 * 60 * 1000; // check again every four hours while Pyxi is open
const LOOK_EVERY = 30 * 1000;
// A Mac only installs an update signed by the same Developer ID as the app (Squirrel.Mac), and
// the releases carry no latest-mac.yml yet. Turn on once Mac builds are signed and published.
const MAC_UPDATES = false;

function note(line: string): void {
  try {
    const dir = join(app.getPath("userData"), ".pyxi");
    mkdirSync(dir, { recursive: true });
    appendFileSync(join(dir, "updates.log"), `${new Date().toISOString()} ${scrubLine(line)}\n`);
  } catch {
    /* logging must never break updating */
  }
}

export function startUpdates(
  tell: (channel: string, ...args: unknown[]) => void,
  busy: () => Promise<boolean> = async () => false,
): void {
  if (!app.isPackaged) return;
  if (process.platform === "darwin" && !MAC_UPDATES) {
    note("automatic updates are off in this Mac build: it isn't signed yet (docs/MAC-PORT.md)");
    return;
  }
  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = true; // quitting Pyxi normally also applies a waiting update
  let installing = false;
  let watcher: NodeJS.Timeout | undefined;
  const install = (why: string) => {
    if (installing) return;
    installing = true;
    clearInterval(watcher);
    note(`installing (${why})`);
    // Not silent: the installer shows its "Updating Pyxi" progress window, then opens Pyxi again
    // (resources/installer.nsh). The brain is stopped by before-quit.
    autoUpdater.quitAndInstall(false, true);
  };
  autoUpdater.on("checking-for-update", () => note(`checking (this is ${app.getVersion()})`));
  autoUpdater.on("update-not-available", () => note("up to date"));
  autoUpdater.on("update-available", (info) => note(`downloading ${info.version}`));
  autoUpdater.on("update-downloaded", (info) => {
    note(`ready: ${info.version}`);
    tell("update-ready", { version: info.version });
    clearInterval(watcher);
    watcher = setInterval(async () => {
      if (await goodMomentToUpdate(powerMonitor.getSystemIdleTime(), busy)) install("idle");
    }, LOOK_EVERY);
  });
  autoUpdater.on("error", (err) => note(`error: ${err?.message ?? err}`));
  const check = () => void autoUpdater.checkForUpdates().catch(() => undefined);
  setTimeout(check, 20_000); // after startup settles, not during it
  setInterval(check, EVERY);
  ipcMain.on("install-update", () => install("Update app"));
}

/** "Check for Updates…" from the tray: check now and say what came of it, in plain words. */
export async function checkNow(): Promise<string> {
  if (!app.isPackaged) return "Updates come with the installed app (this is the developer version).";
  try {
    const result = await autoUpdater.checkForUpdates();
    const latest = result?.updateInfo?.version;
    if (!latest || latest === app.getVersion()) return `You're up to date (Pyxi ${app.getVersion()}).`;
    return `Pyxi ${latest} is downloading. It installs by itself when you're away for a few minutes, or press Update app in the notch.`;
  } catch (err) {
    note(`check from the tray failed: ${(err as Error)?.message ?? err}`);
    return "Couldn't check for updates right now. Is the internet connection up?";
  }
}
