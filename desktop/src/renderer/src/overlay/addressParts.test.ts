import { describe, expect, it, vi } from "vitest";
import { onSpoken, parts, problem, sizeFor, spoken, tidy } from "./addressParts";

describe("the address, a part at a time", () => {
  it("splits at the @ and keeps each character's place", () => {
    const p = parts("sam_21@example.com");
    expect(p.name.map((c) => c.ch).join("")).toBe("sam_21");
    expect(p.domain.map((c) => c.ch).join("")).toBe("example.com");
    expect(p.at).toBe(6);
    expect(p.domain[0].at).toBe(7); // the caret lands in the whole address, not the part
  });

  it("knows digits from letters from marks", () => {
    expect(parts("a_2@x.io").name.map((c) => c.kind)).toEqual(["letter", "mark", "digit"]);
  });

  it("marks the characters that look like another", () => {
    const alike = (a: string) => [...parts(a).name, ...parts(a).domain].filter((c) => c.alike).map((c) => c.ch).join("");
    expect(alike("bill10@example.org")).toBe("ll10l");
    expect(alike("Ian.O@abc.net")).toBe("IO");
    expect(alike("sam@abc.net")).toBe("");
    const rn = parts("barney@abc.net").name.filter((c) => c.alike);
    expect(rn.map((c) => c.ch).join("")).toBe("rn");
    expect(rn[0].alike).toBe("r then n, not m");
    expect(parts("l1@abc.net").name.map((c) => c.alike)).toEqual(["small L", "the digit one"]);
  });

  it("an address with no @ yet is all name", () => {
    const p = parts("sam.example.com");
    expect(p.at).toBe(-1);
    expect(p.domain).toEqual([]);
    expect(p.name).toHaveLength(15);
  });
});

describe("what looks wrong", () => {
  it("passes real addresses", () => {
    for (const a of ["sam@example.com", "dana.lee+work@mail.example.co.uk", "o'neil_21@example.org"]) expect(problem(a)).toBeNull();
  });

  it("says it in a few words", () => {
    expect(problem("")).toBe("It is empty.");
    expect(problem("sam example.com")).toBe("There is a space in it.");
    expect(problem("sam.example.com")).toBe("There is no @ in it.");
    expect(problem("sam@@example.com")).toBe("There is more than one @.");
    expect(problem("@example.com")).toBe("Nothing comes before the @.");
    expect(problem("sam@")).toBe("Nothing comes after the @.");
    expect(problem("sam@example")).toBe("The part after the @ needs a dot, like example.com.");
    expect(problem("sam@example..com")).toBe("There are two dots in a row.");
    expect(problem("sam.@example.com")).toBe("A dot is in the wrong place.");
    expect(problem("sam@example.c0m")).toBe("The ending after the last dot looks wrong.");
    expect(problem("sa(m@example.com")).toBe("It has a character an address can't have.");
  });
});

describe("typing in the box", () => {
  it("drops what came along with a pasted address", () => {
    expect(tidy("  sam@example.com. ")).toBe("sam@example.com");
    expect(tidy("<sam@example.com>")).toBe("sam@example.com");
    expect(tidy("mailto:sam@example.com")).toBe("sam@example.com");
  });

  it("draws long addresses smaller", () => {
    expect(sizeFor("sam@example.com")).toBe("big");
    expect(sizeFor("dana.lee.at.work@example.co.uk")).toBe("mid");
    expect(sizeFor("a.very.long.name.indeed@department.example.org")).toBe("small");
  });
});

describe("words said while the card is up", () => {
  it("reach the card that is listening, and stop when it is gone", () => {
    const heard = vi.fn();
    const off = onSpoken(heard);
    spoken({ action: "fill", address: "sam@example.com" });
    off();
    spoken({ action: "confirm" });
    expect(heard).toHaveBeenCalledTimes(1);
    expect(heard).toHaveBeenCalledWith({ action: "fill", address: "sam@example.com" });
  });
});
