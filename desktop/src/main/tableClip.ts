/** What the brain sends for "paste a table": the cells as tab-separated text and as an HTML table. */
export interface TableClip {
  text: string;
  html: string;
}

const MAX = 2_000_000;

/** The two clipboard forms from the brain's message, or null when it isn't one (nothing is pasted then). */
export function tableClip(value: string): TableClip | null {
  if (typeof value !== "string" || value.length > MAX) return null;
  try {
    const parsed: unknown = JSON.parse(value);
    if (!parsed || typeof parsed !== "object") return null;
    const { text, html } = parsed as Record<string, unknown>;
    if (typeof text !== "string" || typeof html !== "string" || !text) return null;
    // Only a table: nothing that could run or load when pasted into a page.
    if (!/^<table[\s>]/i.test(html) || /<\s*(script|img|iframe|link|object|embed)\b|\son\w+\s*=|javascript:/i.test(html)) {
      return { text, html: "" };
    }
    return { text, html };
  } catch {
    return null;
  }
}
