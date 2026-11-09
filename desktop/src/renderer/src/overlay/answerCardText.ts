// Words and rules for the answer card (AnswerCard.tsx): what it calls itself, which links and
// pictures it will show, and how long it stays.
import type { AnswerCard, CardLink } from "../shared/protocol";

/** A web address, or null: a card never opens anything else (the main process checks again). */
export function webLink(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.toString() : null;
  } catch {
    return null;
  }
}

/** The picture, only if it is one the brain made itself (a small JPEG or PNG handed over as a
 * data: address). The card never loads anything from the web. */
export function cardImage(src: string | null | undefined): string | null {
  return src && /^data:image\/(jpeg|png);base64,[A-Za-z0-9+/=]+$/.test(src) ? src : null;
}

/** The sources worth a chip: web links only, each address once. */
export function cardSources(card: AnswerCard): CardLink[] {
  const seen = new Set<string>();
  const out: CardLink[] = [];
  for (const s of card.sources ?? []) {
    const url = webLink(s.url);
    if (!url || seen.has(url)) continue;
    seen.add(url);
    out.push({ name: s.name.trim() || new URL(url).hostname.replace(/^www\./, ""), url });
  }
  return out;
}

/** The one button, if it has somewhere real to go. */
export function cardButton(card: AnswerCard): CardLink | null {
  const url = webLink(card.button?.url);
  return url ? { name: card.button!.name.trim() || "Open", url } : null;
}

/** The small line above the title: whose card this is. */
export function cardKicker(card: AnswerCard): string {
  if (card.kind === "answer") return "Pyxi's pick";
  const who = card.agent?.trim() || "Pyxi";
  return card.ok ? `${who} finished` : `${who} couldn't finish`;
}

/** How long the card stays once nobody is talking and the pointer is off it. A pick stays long
 * enough to reach for its button; a long result gets reading time for every row. */
export function lingerMs(card: AnswerCard): number {
  if (card.kind === "answer") return 45_000;
  return Math.min(120_000, 20_000 + card.facts.length * 4_000);
}
