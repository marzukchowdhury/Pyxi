import { describe, expect, it } from "vitest";
import type { FormField } from "../shared/protocol";
import { answered, changes, firstOpen, pick, progress, sections, start } from "./formAnswers";

const FIELDS: FormField[] = [
  { id: "q1", label: "First name", section: "Applicant", kind: "text", value: "Ana", from: "saved" },
  { id: "q2", label: "Marital status", section: "Applicant", kind: "choice", options: ["Single", "Married"], value: "Single", from: "form" },
  { id: "q3", label: "Landlord name", section: "Landlord", kind: "text", value: "" },
  { id: "q4", label: "Purpose", section: "Landlord", kind: "multi", options: ["Rent", "Utilities"], value: [] },
  { id: "q5", label: "Notes", section: "", kind: "text", value: "" },
];

describe("the form card", () => {
  it("groups the questions by section, in the form's order", () => {
    const all = sections(FIELDS);
    expect(all.map((s) => [s.name, s.fields.length])).toEqual([["Applicant", 2], ["Landlord", 2], ["The form", 1]]);
  });

  it("opens with what is known typed in, and at the first section with something to answer", () => {
    const answers = start(FIELDS);
    expect(answers).toEqual({ q1: "Ana", q2: "Single", q3: "", q4: [], q5: "" });
    const all = sections(FIELDS);
    expect(progress(all[0], answers)).toBe("2 of 2");
    expect(progress(all[1], answers)).toBe("0 of 2");
    expect(firstOpen(all, answers)).toBe(1);
    expect(firstOpen(all, { q1: "a", q2: "b", q3: "c", q4: ["Rent"], q5: "d" })).toBe(0);
    expect(answered("  ")).toBe(false);
    expect(answered([])).toBe(false);
    expect(answered(["Rent"])).toBe(true);
  });

  it("one choice swaps and clears; several add and take away", () => {
    expect(pick(FIELDS[1], "Single", "Married")).toBe("Married");
    expect(pick(FIELDS[1], "Single", "Single")).toBe("");
    expect(pick(FIELDS[3], [], "Rent")).toEqual(["Rent"]);
    expect(pick(FIELDS[3], ["Rent"], "Utilities")).toEqual(["Rent", "Utilities"]);
    expect(pick(FIELDS[3], ["Rent", "Utilities"], "Rent")).toEqual(["Utilities"]);
  });

  it("counts only the answers that changed", () => {
    const answers = start(FIELDS);
    expect(changes(FIELDS, answers)).toBe(0);
    expect(changes(FIELDS, { ...answers, q1: "Ana " })).toBe(0);
    expect(changes(FIELDS, { ...answers, q3: "Sam Example", q4: ["Rent"], q2: "" })).toBe(3);
  });

  it("never shares an array with the card it was opened from", () => {
    const answers = start(FIELDS);
    (answers.q4 as string[]).push("Rent");
    expect(FIELDS[3].value).toEqual([]);
  });
});
