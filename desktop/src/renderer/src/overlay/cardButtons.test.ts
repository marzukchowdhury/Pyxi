import { describe, expect, it } from "vitest";
import { type CardFacts, cardButtons, MAX_BUTTONS, retryLine, settingsTarget } from "./cardButtons";
import { micBlocked, micBlockedCard, micProblem } from "./dictationText";

const card = (part: Partial<CardFacts>): CardFacts => ({ ok: false, actions: [], files: [], link: null, calendar: null, retry: null, ...part });
const labels = (part: Partial<CardFacts>) => cardButtons(card(part)).map((b) => b.label);

describe("a result card's buttons", () => {
  it("offers Try again only on a failure that says what it will run again", () => {
    expect(labels({ actions: ["retry"], retry: "Scout: compare two laptops" })).toEqual(["Try again"]);
    // No words for what would be run: no button (the brain did not vouch that nothing was done).
    expect(labels({ actions: ["retry"] })).toEqual([]);
    expect(labels({ actions: ["retry"], retry: "" })).toEqual([]);
    // Never on something that worked.
    expect(labels({ ok: true, actions: ["retry"], retry: "Send the email to Sam" })).toEqual([]);
  });

  it("says what Try again will run, on the card and on the button", () => {
    const [b] = cardButtons(card({ actions: ["retry"], retry: "What is on my calendar today?" }));
    expect(b.hint).toBe(retryLine("What is on my calendar today?"));
    expect(b.hint).toContain("What is on my calendar today?");
  });

  it("opens Settings at the part that mends it", () => {
    expect(labels({ actions: ["settings:files"] })).toEqual(["Open Settings"]);
    expect(settingsTarget(["settings:files"])).toEqual({ tab: "settings", section: "files" });
    expect(settingsTarget(["settings:accounts"])).toEqual({ tab: "settings", section: "accounts" });
    expect(settingsTarget(["settings:budget"])).toEqual({ tab: "settings", section: "budget" });
    expect(settingsTarget(["settings"])).toEqual({ tab: "settings", section: null });
    // A part this version does not know still opens Settings, at the top.
    expect(settingsTarget(["settings:something-new"])).toEqual({ tab: "settings", section: null });
    expect(settingsTarget(["google", "link"])).toBeNull();
    // The voice has a tab of its own.
    expect(settingsTarget(["settings:voice"])).toEqual({ tab: "voice", section: null });
    expect(labels({ actions: ["settings:voice"] })).toEqual(["Open Voice settings"]);
  });

  it("puts Open microphone settings on a blocked microphone's card, and only there", () => {
    const blocked = micBlockedCard("NotAllowedError: Permission denied");
    expect(blocked).not.toBeNull();
    expect(blocked!.body).toBe(micProblem("NotAllowedError: Permission denied"));
    expect(blocked!.title).toBe("The microphone is blocked");
    expect(cardButtons(blocked!)).toEqual([{ kind: "mic", label: "Open microphone settings", primary: true }]);
    // No microphone, or one in use: the Windows privacy page would not help, so no card.
    expect(micBlockedCard("NotFoundError: Requested device not found")).toBeNull();
    expect(micBlockedCard("NotReadableError: Could not start audio source")).toBeNull();
    expect(micBlockedCard("TypeError: boom")).toBeNull();
    expect(micBlocked("SecurityError: x")).toBe(true);
  });

  it("keeps the buttons that were there before", () => {
    expect(labels({ actions: ["google"] })).toEqual(["Sign in to Google"]);
    expect(labels({ actions: ["choose_plan"], account: "Choose a plan" })).toEqual(["Choose a plan"]);
    expect(labels({ ok: true, actions: ["update"] })).toEqual(["Restart to update"]);
    expect(labels({ ok: true, actions: ["link"], link: "https://example.com/doc" })).toEqual(["Open"]);
    expect(labels({ ok: true, actions: ["link", "calendar"], link: "https://meet.google.com/abc-defg-hij", calendar: "Add it" })).toEqual(["Join Google Meet", "Add to calendar"]);
  });

  it("never shows more than two, and what mends a failure comes first", () => {
    const many = card({ actions: ["folder", "send_back", "calendar", "link", "retry", "settings:accounts", "google", "update"], link: "https://example.com", files: ["a.docx"], calendar: "Add it", retry: "Ask again" });
    const shown = cardButtons(many);
    expect(shown).toHaveLength(MAX_BUTTONS);
    expect(shown.map((b) => b.kind)).toEqual(["update", "google"]);
    expect(labels({ actions: ["folder", "send_back", "link", "settings:files"], link: "https://example.com", files: ["a.docx"] })).toEqual(["Open Settings", "Open"]);
    expect(labels({ actions: ["folder", "send_back"], files: ["a.docx"] })).toEqual(["Open file", "Show in folder"]);
  });
});

describe("a site that turned Scout away", () => {
  it("offers the same work in their own browser, or adding Pyxi to it", () => {
    expect(labels({ ok: false, actions: ["own_tab"] })).toEqual(["Do it in my browser"]);
    expect(labels({ ok: false, actions: ["browser_setup"] })).toEqual(["Add Pyxi to my browser"]);
    // never on a result that worked
    expect(labels({ ok: true, actions: ["own_tab"] })).toEqual([]);
  });
});
