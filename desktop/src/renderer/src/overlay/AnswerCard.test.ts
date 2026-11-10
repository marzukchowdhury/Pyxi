import { describe, expect, it } from "vitest";
import type { AnswerCard } from "../shared/protocol";
import { cardButton, cardImage, cardKicker, cardSources, lingerMs, webLink } from "./answerCardText";

const pick: AnswerCard = {
  id: "answer_1",
  kind: "answer",
  title: "Anker Soundcore Space A40",
  figure: "$49.99",
  note: "Price per RTINGS; shops vary",
  facts: ["Strong noise cancelling for the price", "10 hours a charge", "No wear detection"],
  sources: [
    { name: "RTINGS", url: "https://www.rtings.com/headphones/reviews/best/cheap-wireless-earbuds" },
    { name: "", url: "https://www.pcmag.com/picks/the-best-cheap-wireless-earbuds" },
    { name: "RTINGS again", url: "https://www.rtings.com/headphones/reviews/best/cheap-wireless-earbuds" },
    { name: "A script", url: "javascript:alert(1)" },
    { name: "A file", url: "file:///C:/Windows/win.ini" },
  ],
  button: { name: "Check on Amazon", url: "https://www.amazon.ca/s?k=Anker+Soundcore+Space+A40" },
  image: null,
  agent: null,
  ok: true,
};

describe("the answer card", () => {
  it("opens web links only", () => {
    expect(webLink("https://www.amazon.ca/s?k=x")).toBe("https://www.amazon.ca/s?k=x");
    for (const bad of ["javascript:alert(1)", "file:///C:/x", "ms-settings:privacy", "not a link", "", null]) {
      expect(webLink(bad)).toBeNull();
    }
  });

  it("lists each real source once, by name", () => {
    expect(cardSources(pick)).toEqual([
      { name: "RTINGS", url: "https://www.rtings.com/headphones/reviews/best/cheap-wireless-earbuds" },
      { name: "pcmag.com", url: "https://www.pcmag.com/picks/the-best-cheap-wireless-earbuds" },
    ]);
  });

  it("has one button, and only when it leads somewhere real", () => {
    expect(cardButton(pick)).toEqual({ name: "Check on Amazon", url: "https://www.amazon.ca/s?k=Anker+Soundcore+Space+A40" });
    expect(cardButton({ ...pick, button: null })).toBeNull();
    expect(cardButton({ ...pick, button: { name: "Run", url: "file:///C:/evil.exe" } })).toBeNull();
  });

  it("shows only a picture the brain handed over, never one from the web", () => {
    expect(cardImage("data:image/jpeg;base64,/9j/4AAQ")).toBe("data:image/jpeg;base64,/9j/4AAQ");
    expect(cardImage("https://example.com/earbuds.jpg")).toBeNull();
    expect(cardImage("data:image/svg+xml;base64,PHN2Zz4=")).toBeNull();
    expect(cardImage(null)).toBeNull();
  });

  it("says whose card it is", () => {
    expect(cardKicker(pick)).toBe("Pyxi's pick");
    const result: AnswerCard = { ...pick, kind: "result", agent: "Scout", facts: Array.from({ length: 9 }, (_, i) => `Table ${i + 1}`) };
    expect(cardKicker(result)).toBe("Scout finished");
    expect(cardKicker({ ...result, ok: false })).toBe("Scout couldn't finish");
    // A nine-item result stays up longer than a three-fact pick.
    expect(lingerMs(result)).toBeGreaterThan(lingerMs(pick));
    expect(lingerMs({ ...result, facts: Array.from({ length: 40 }, () => "row") })).toBe(120_000);
  });
});
