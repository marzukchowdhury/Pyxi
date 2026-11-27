import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { ENGINES, keySteps, looksLikeKey, packButton, packLine } from "./voiceText";

const here = dirname(fileURLToPath(import.meta.url));
// Words that send someone to a settings file or a command line.
const DEVELOPER = /\.env\b|setup-voice|pyxi\/|pyxi serve|terminal|command line|API_KEY|\brun <code>/i;

describe("the Voice tab's set-up words", () => {
  it("offers a download for a missing voice, and a fresh copy for one that is here", () => {
    expect(packButton(null, false)).toEqual({ label: "Download the voice (about 350 MB)", busy: false });
    expect(packButton(null, true)).toEqual({ label: "Download the voice again", busy: false });
    expect(packLine(null, false).text).toContain("downloads once");
  });

  it("says how far along a download is, and cannot be pressed twice", () => {
    const going = { state: "downloading" as const, percent: 42, message: "" };
    expect(packButton(going, false)).toEqual({ label: "Downloading, 42 percent", busy: true });
    expect(packLine(going, false).tone).toBe("plain");
  });

  it("shows the brain's plain line when a download fails, and lets them try again", () => {
    const failed = { state: "failed" as const, percent: 0, message: "The voice couldn't be downloaded. Check the internet connection, then press Download again." };
    expect(packLine(failed, false)).toEqual({ text: failed.message, tone: "warn" });
    expect(packButton(failed, false)).toEqual({ label: "Try the download again", busy: false });
    // No line from the brain: still words, never an empty box.
    expect(packLine({ ...failed, message: "" }, false).text).toMatch(/try again/);
  });

  it("says when the voice is ready", () => {
    expect(packLine({ state: "ready", percent: 100, message: "Pyxi's voice is ready." }, true)).toEqual({ text: "Pyxi's voice is ready.", tone: "good" });
  });

  it("only sends something that could be a key", () => {
    expect(looksLikeKey("made-up-voice-key-0123456789")).toBe(true);
    expect(looksLikeKey("  ")).toBe(false);
    expect(looksLikeKey("short")).toBe(false);
    expect(looksLikeKey("two words that are long enough")).toBe(false);
  });

  it("never sends anyone to a settings file or a command", () => {
    const words = [
      ...ENGINES.flatMap((e) => [e.name, e.blurb]),
      ...ENGINES.filter((e) => e.site).flatMap((e) => keySteps(e.name)),
      packButton(null, false).label,
      packButton(null, true).label,
      packLine(null, false).text,
      packLine(null, true).text,
      packLine({ state: "downloading", percent: 1, message: "" }, false).text,
      packLine({ state: "failed", percent: 0, message: "" }, false).text,
    ];
    for (const w of words) {
      expect(w).not.toMatch(DEVELOPER);
      expect(w).not.toMatch(/—/);
    }
    // And nothing written straight into the tab does either.
    for (const file of ["VoiceStudio.tsx", "voiceText.ts"]) {
      const source = readFileSync(join(here, file), "utf8");
      expect(source).not.toMatch(DEVELOPER);
    }
  });

  it("keeps the key box a password box that is emptied on save", () => {
    const source = readFileSync(join(here, "VoiceStudio.tsx"), "utf8");
    expect(source).toMatch(/<input type="password"[^\n]{0,200}autoComplete="off"/);
    expect(source).toMatch(/brain\.send\(\{ type: "voice_key", engine, key: key\.trim\(\) \}\);\s*setKey\(""\)/);
  });
});
