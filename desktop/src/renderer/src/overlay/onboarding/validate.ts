/**
 * The tour's gentle checks: an email that looks like one, a date of birth that is a real day in
 * the past, and whether that makes them old enough. The brain checks all of it again before it
 * keeps anything (pyxi/onboarding.py); these are so the card can say so at once.
 */

/** Pyxi is for people this age and over (docs/PRIVACY.md, Children). */
export const MIN_AGE = 13;

export const LIKES = ["music", "football", "cooking", "coding", "gaming", "travel", "films", "fitness", "reading", "cars"];
export const USES: [id: string, label: string][] = [
  ["study", "Study"],
  ["work", "Work"],
  ["creating", "Creating"],
  ["everything", "A bit of everything"],
];
export const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

export function looksLikeEmail(text: string): boolean {
  return /^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$/.test(text.trim()) && text.trim().length <= 200;
}

export interface Birthday {
  day: string;
  month: string; // "1".."12", or ""
  year: string;
}

export const NO_BIRTHDAY: Birthday = { day: "", month: "", year: "" };

export function birthdayEmpty(b: Birthday): boolean {
  return !b.day.trim() && !b.month && !b.year.trim();
}

/** "2001-04-23", or null when the three boxes are not a real day. */
export function birthdayIso(b: Birthday): string | null {
  const [d, m, y] = [Number(b.day), Number(b.month), Number(b.year)];
  if (!/^\d{1,2}$/.test(b.day.trim()) || !/^\d{4}$/.test(b.year.trim()) || !Number.isInteger(m) || m < 1 || m > 12) return null;
  const date = new Date(Date.UTC(y, m - 1, d));
  if (date.getUTCFullYear() !== y || date.getUTCMonth() !== m - 1 || date.getUTCDate() !== d) return null; // 30 February
  return `${String(y).padStart(4, "0")}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}

/** Why this date of birth can't be right, or null (an empty one is fine: it is optional). */
export function birthdayProblem(b: Birthday, today: Date = new Date()): string | null {
  if (birthdayEmpty(b)) return null;
  const iso = birthdayIso(b);
  if (!iso) return "That date doesn't look right.";
  if (iso >= isoDay(today)) return "A date of birth is in the past.";
  if (Number(b.year) < 1900) return "That year looks too far back.";
  return null;
}

export function isoDay(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** Whole years between an ISO date of birth and today. */
export function ageOn(iso: string, today: Date = new Date()): number {
  const [y, m, d] = iso.split("-").map(Number);
  const before = today.getMonth() + 1 < m || (today.getMonth() + 1 === m && today.getDate() < d);
  return today.getFullYear() - y - (before ? 1 : 0);
}

export function tooYoung(b: Birthday, today: Date = new Date()): boolean {
  const iso = birthdayIso(b);
  return !!iso && birthdayProblem(b, today) === null && ageOn(iso, today) < MIN_AGE;
}

/** "2001-04-23" back into the three boxes (what is already saved, for someone updating). */
export function birthdayFromIso(iso: string | undefined): Birthday {
  const m = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec((iso ?? "").trim());
  return m ? { year: m[1], month: String(Number(m[2])), day: String(Number(m[3])) } : NO_BIRTHDAY;
}

/** Their own words in the "something else" box, as separate likes: "chess, baking and bikes". */
export function ownLikes(text: string): string[] {
  return text
    .split(/,|;|\band\b|\n/i)
    .map((w) => w.replace(/\s+/g, " ").trim().replace(/[.]+$/, "").slice(0, 40))
    .filter(Boolean);
}

/** Chips and their own words together: no repeats (whatever the capitals), twelve at most. */
export function allLikes(chips: string[], own: string): string[] {
  const out: string[] = [];
  for (const w of [...chips, ...ownLikes(own)]) if (!out.some((x) => x.toLowerCase() === w.toLowerCase())) out.push(w);
  return out.slice(0, 12);
}

export interface About {
  email: string;
  birthday: Birthday;
  likes: string[];
  own: string;
  use: string;
}

export const EMPTY_ABOUT: About = { email: "", birthday: NO_BIRTHDAY, likes: [], own: "", use: "" };

/** What the card says under a field that looks wrong. Nothing here is required. */
export function problems(a: About, today: Date = new Date()): { email?: string; birthday?: string } {
  const out: { email?: string; birthday?: string } = {};
  if (a.email.trim() && !looksLikeEmail(a.email)) out.email = "That doesn't look like an email address.";
  const b = birthdayProblem(a.birthday, today);
  if (b) out.birthday = b;
  return out;
}

/** How many lights are in the jar: one for each thing they have told Pyxi (five at most). */
export function lights(name: string, a: About, today: Date = new Date()): number {
  const p = problems(a, today);
  return (
    (name.trim() ? 1 : 0) +
    (a.email.trim() && !p.email ? 1 : 0) +
    (!birthdayEmpty(a.birthday) && !p.birthday && birthdayIso(a.birthday) ? 1 : 0) +
    (allLikes(a.likes, a.own).length ? 1 : 0) +
    (a.use ? 1 : 0)
  );
}

/** What goes to the brain: only what was answered and looks right. */
export function details(name: string, a: About, today: Date = new Date()): { name?: string; email?: string; date_of_birth?: string; likes?: string[]; use?: string } {
  const p = problems(a, today);
  const out: ReturnType<typeof details> = {};
  if (name.trim()) out.name = name.trim();
  if (a.email.trim() && !p.email) out.email = a.email.trim();
  const iso = birthdayIso(a.birthday);
  if (iso && !p.birthday) out.date_of_birth = iso;
  const likes = allLikes(a.likes, a.own);
  if (likes.length) out.likes = likes;
  if (a.use) out.use = a.use;
  return out;
}
