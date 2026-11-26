import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { WINDOWS_PAGES, windowsPageUrl } from "./windowsSettings";

const here = dirname(fileURLToPath(import.meta.url));

describe("the Windows settings pages Pyxi may open", () => {
  it("opens the microphone privacy page by name", () => {
    expect(windowsPageUrl("microphone", "win32")).toBe("ms-settings:privacy-microphone");
  });

  it("is a list of exactly one page", () => {
    expect(Object.entries(WINDOWS_PAGES)).toEqual([["microphone", "ms-settings:privacy-microphone"]]);
  });

  it("opens nothing else, whatever a window sends", () => {
    for (const sent of [
      "ms-settings:privacy-microphone", // an address, even the right one: only names are taken
      "ms-settings:privacy-webcam",
      "ms-settings:",
      "file:///C:/Windows/System32/cmd.exe",
      "https://example.com",
      "calculator:",
      "Microphone",
      "microphone ",
      "camera",
      "constructor",
      "__proto__",
      "toString",
      "",
      null,
      undefined,
      1,
      {},
      ["microphone"],
      { toString: () => "microphone" },
    ]) {
      expect(windowsPageUrl(sent, "win32")).toBeNull();
    }
  });

  it("does nothing on a system that is not Windows", () => {
    expect(windowsPageUrl("microphone", "darwin")).toBeNull();
    expect(windowsPageUrl("microphone", "linux")).toBeNull();
  });

  it("leaves the link opener at http and https only, and takes names only from Pyxi's own pages", () => {
    const main = readFileSync(join(here, "index.ts"), "utf8");
    const opener = main.slice(main.indexOf("function openExternalSafe"), main.indexOf("function openWindowsPage"));
    expect(opener).toContain('parsed.protocol === "http:" || parsed.protocol === "https:"');
    expect(opener).not.toContain("ms-settings");
    const page = main.slice(main.indexOf("function openWindowsPage"));
    const body = page.slice(0, page.indexOf("\n}\n"));
    expect(body).toContain("ownPage(sender)");
    expect(body).toContain("windowsPageUrl(page)");
    // The only address it can open is the one the list gave back.
    expect(body.match(/shell\.openExternal\(([^)]*)\)/)?.[1]).toBe("url");
    // And nothing else in the app opens a settings address.
    expect(main.match(/ms-settings/g) ?? []).toHaveLength(1); // the comment on openExternalSafe
  });
});
