// What the form card works out before it draws: the questions in their sections, how full
// each section is, and which one to open first. Kept apart from the component so it is tested.
import type { FormField } from "../shared/protocol";

export type Answers = Record<string, string | string[]>;

export interface Section {
  name: string;
  fields: FormField[];
}

/** The questions grouped by section, in the form's own order. */
export function sections(fields: FormField[]): Section[] {
  const out: Section[] = [];
  for (const f of fields) {
    const name = f.section || "The form";
    const last = out[out.length - 1];
    if (last && last.name === name) last.fields.push(f);
    else out.push({ name, fields: [f] });
  }
  return out;
}

export function start(fields: FormField[]): Answers {
  return Object.fromEntries(fields.map((f) => [f.id, Array.isArray(f.value) ? [...f.value] : f.value ?? ""]));
}

export function answered(value: string | string[] | undefined): boolean {
  return Array.isArray(value) ? value.length > 0 : !!value && value.trim() !== "";
}

/** "3 of 12": how much of a section has an answer. */
export function progress(section: Section, answers: Answers): string {
  const done = section.fields.filter((f) => answered(answers[f.id])).length;
  return `${done} of ${section.fields.length}`;
}

/** The section to open first: the first one with something still to answer. */
export function firstOpen(all: Section[], answers: Answers): number {
  const i = all.findIndex((s) => s.fields.some((f) => !answered(answers[f.id])));
  return i < 0 ? 0 : i;
}

/** A tick box pressed: one choice swaps (pressing the chosen one again clears it); with
 * several allowed, it is added or taken away. */
export function pick(field: FormField, current: string | string[] | undefined, option: string): string | string[] {
  if (field.kind === "multi") {
    const have = Array.isArray(current) ? current : [];
    return have.includes(option) ? have.filter((o) => o !== option) : [...have, option];
  }
  return current === option ? "" : option;
}

/** How many answers differ from what the card opened with. */
export function changes(fields: FormField[], answers: Answers): number {
  const same = (a: string | string[], b: string | string[]) =>
    Array.isArray(a) || Array.isArray(b) ? [...(a as string[])].sort().join("\u0001") === [...(b as string[])].sort().join("\u0001") : a.trim() === b.trim();
  return fields.filter((f) => !same(answers[f.id] ?? "", f.value ?? "")).length;
}
