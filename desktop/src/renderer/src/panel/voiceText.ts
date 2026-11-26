// What the Voice tab says about getting a voice working. Kept apart from the drawing so every
// line can be tested: nothing here may send someone to a settings file or a command.
import type { VoicePack, VoicePublic } from "../shared/protocol";

export type Engine = VoicePublic["engine"];

export const ENGINES: { id: Engine; name: string; blurb: string; site?: string }[] = [
  { id: "kokoro", name: "On this PC", blurb: "Free and private. Works offline." },
  { id: "elevenlabs", name: "ElevenLabs", blurb: "The most human-sounding. Uses your own ElevenLabs account and its credits.", site: "https://elevenlabs.io/app/settings/api-keys" },
  { id: "gemini", name: "Google Gemini", blurb: "Expressive, acts out the tone of each line. Uses your own Google AI account.", site: "https://aistudio.google.com/apikey" },
];

export const PACK_SIZE = "about 350 MB";

/** The voice pack's button: what it says, and whether it can be pressed. */
export function packButton(pack: VoicePack | null, installed: boolean): { label: string; busy: boolean } {
  if (pack?.state === "downloading") return { label: `Downloading, ${pack.percent} percent`, busy: true };
  if (installed) return { label: "Download the voice again", busy: false };
  return { label: pack?.state === "failed" ? "Try the download again" : `Download the voice (${PACK_SIZE})`, busy: false };
}

/** The line under the button: how it is going, or why it failed and what to do. */
export function packLine(pack: VoicePack | null, installed: boolean): { text: string; tone: "plain" | "good" | "warn" } {
  if (pack?.state === "downloading") return { text: "You can keep using Pyxi while it downloads. The Windows voice talks until it is here.", tone: "plain" };
  if (pack?.state === "failed") return { text: pack.message || "The voice couldn't be downloaded just now. Press the button to try again.", tone: "warn" };
  if (pack?.state === "ready") return { text: pack.message || "Pyxi's voice is ready.", tone: "good" };
  if (installed) return { text: "If the voice sounds broken or won't play, this fetches a fresh copy.", tone: "plain" };
  return { text: "Pyxi's own voice runs on this PC. It downloads once, and the Windows voice talks until it is here.", tone: "plain" };
}

/** The steps for a cloud voice: all done in the browser and in this box. */
export function keySteps(name: string): string[] {
  return [
    `Press Open ${name} and sign in to your own ${name} account.`,
    "Create a key there and copy it.",
    "Paste it below and press Save. It is kept encrypted on this PC and never shown again.",
  ];
}

/** A pasted key that is worth sending to be saved (the brain checks it properly). */
export function looksLikeKey(text: string): boolean {
  const t = text.trim();
  return t.length >= 12 && !/\s/.test(t);
}
