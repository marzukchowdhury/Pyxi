import { describe, expect, it } from "vitest";
import { ageOn, allLikes, birthdayFromIso, birthdayIso, birthdayProblem, details, EMPTY_ABOUT, lights, looksLikeEmail, MIN_AGE, ownLikes, problems, tooYoung } from "./validate";

const TODAY = new Date(2026, 10, 13); // 13 November 2026
const day = (d: string, month: string, year: string) => ({ day: d, month, year });

describe("an email that looks like one", () => {
  it.each(["sam@example.com", " sam.rivera+pyxi@mail.example.com "])("accepts %s", (e) => expect(looksLikeEmail(e)).toBe(true));
  it.each(["sam", "sam@", "sam@example", "sam @example.com", "@example.com", "sam@example."])("refuses %s", (e) => expect(looksLikeEmail(e)).toBe(false));
});

describe("a date of birth", () => {
  it("is a real day", () => {
    expect(birthdayIso(day("23", "4", "2001"))).toBe("2001-04-23");
    expect(birthdayIso(day("5", "12", "1999"))).toBe("1999-12-05");
    expect(birthdayIso(day("30", "2", "2001"))).toBeNull();
    expect(birthdayIso(day("29", "2", "2000"))).toBe("2000-02-29");
    expect(birthdayIso(day("29", "2", "2001"))).toBeNull();
    expect(birthdayIso(day("", "4", "2001"))).toBeNull();
    expect(birthdayIso(day("23", "", "2001"))).toBeNull();
    expect(birthdayIso(day("23", "4", "01"))).toBeNull();
  });

  it("is in the past, and not absurdly far", () => {
    expect(birthdayProblem(day("23", "4", "2001"), TODAY)).toBeNull();
    expect(birthdayProblem(day("", "", ""), TODAY)).toBeNull(); // optional
    expect(birthdayProblem(day("13", "11", "2026"), TODAY)).toMatch(/past/);
    expect(birthdayProblem(day("1", "1", "2030"), TODAY)).toMatch(/past/);
    expect(birthdayProblem(day("1", "1", "1850"), TODAY)).toMatch(/far back/);
    expect(birthdayProblem(day("31", "6", "2001"), TODAY)).toMatch(/doesn't look right/);
    expect(birthdayProblem(day("23", "", ""), TODAY)).toMatch(/doesn't look right/); // half filled in
  });

  it("gives an age in whole years", () => {
    expect(ageOn("2013-11-13", TODAY)).toBe(13);
    expect(ageOn("2013-11-14", TODAY)).toBe(12);
    expect(ageOn("2001-04-23", TODAY)).toBe(25);
  });

  it("knows who is too young", () => {
    expect(MIN_AGE).toBe(13);
    expect(tooYoung(day("14", "11", "2013"), TODAY)).toBe(true);
    expect(tooYoung(day("13", "11", "2013"), TODAY)).toBe(false);
    expect(tooYoung(day("", "", ""), TODAY)).toBe(false);
    expect(tooYoung(day("1", "1", "2030"), TODAY)).toBe(false); // not a date of birth at all
  });

  it("reads back what is already saved", () => {
    expect(birthdayFromIso("2001-04-23")).toEqual(day("23", "4", "2001"));
    expect(birthdayFromIso("23 April 2001")).toEqual(day("", "", ""));
    expect(birthdayFromIso(undefined)).toEqual(day("", "", ""));
  });
});

describe("things they like", () => {
  it("splits their own words", () => {
    expect(ownLikes("chess, baking and bikes")).toEqual(["chess", "baking", "bikes"]);
    expect(ownLikes("  ")).toEqual([]);
    expect(ownLikes("sandcastles")).toEqual(["sandcastles"]);
  });

  it("joins chips and their own without repeats", () => {
    expect(allLikes(["music", "cooking"], "Cooking, chess")).toEqual(["music", "cooking", "chess"]);
    expect(allLikes([], Array.from({ length: 20 }, (_, i) => `thing ${i}`).join(","))).toHaveLength(12);
  });
});

describe("the card", () => {
  const about = { ...EMPTY_ABOUT, email: "sam@example.com", birthday: day("23", "4", "2001"), likes: ["football"], own: "chess", use: "study" };

  it("requires nothing", () => {
    expect(problems(EMPTY_ABOUT, TODAY)).toEqual({});
    expect(details("", EMPTY_ABOUT, TODAY)).toEqual({});
    expect(lights("", EMPTY_ABOUT, TODAY)).toBe(0);
  });

  it("says what looks wrong, gently", () => {
    expect(problems({ ...about, email: "sam at example", birthday: day("31", "2", "2001") }, TODAY)).toEqual({
      email: "That doesn't look like an email address.",
      birthday: "That date doesn't look right.",
    });
  });

  it("adds a light for each answer", () => {
    expect(lights("Sam", EMPTY_ABOUT, TODAY)).toBe(1);
    expect(lights("Sam", { ...EMPTY_ABOUT, email: "sam@" }, TODAY)).toBe(1); // not yet an email
    expect(lights("Sam", { ...EMPTY_ABOUT, email: "sam@example.com" }, TODAY)).toBe(2);
    expect(lights("Sam", about, TODAY)).toBe(5);
  });

  it("sends only what was answered and looks right", () => {
    expect(details(" Sam ", about, TODAY)).toEqual({ name: "Sam", email: "sam@example.com", date_of_birth: "2001-04-23", likes: ["football", "chess"], use: "study" });
    expect(details("", { ...EMPTY_ABOUT, email: "nope", use: "work" }, TODAY)).toEqual({ use: "work" });
  });
});
