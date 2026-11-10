/** What the brain sends for "paste a table": the cells as tab-separated text and as an HTML table. */
export interface TableClip {
  text: string;
  html: string;
}

/** The longest message taken (MAX_MESSAGE in src/pyxi/companion/paste.py: the brain refuses more). */
const MAX = 2_000_000;
/** The one opening tag with attributes the brain writes (a table with drawn borders, for documents). */
const BORDERED = '<table border="1" cellpadding="4" style="border-collapse:collapse">';
const BARE_TAGS = /<\/?(?:table|tr|th|td)>|<br>/g;

/** Only a table, tag for tag as the brain writes it: after its own tags are taken out, no "<" is
 * left. Text in a cell arrives escaped, so anything else that opens a tag did not come from it. */
function onlyATable(html: string): boolean {
  const bordered = html.startsWith(BORDERED);
  if (!bordered && !html.startsWith("<table>")) return false;
  return !(bordered ? html.slice(BORDERED.length) : html).replace(BARE_TAGS, "").includes("<");
}

/** The two clipboard forms from the brain's message, or null when it isn't one (nothing is pasted then). */
export function tableClip(value: string): TableClip | null {
  if (typeof value !== "string" || value.length > MAX) return null;
  try {
    const parsed: unknown = JSON.parse(value);
    if (!parsed || typeof parsed !== "object") return null;
    const { text, html } = parsed as Record<string, unknown>;
    if (typeof text !== "string" || typeof html !== "string" || !text) return null;
    // Anything more than that table: the plain cells only, nothing that could run or load in a page.
    return { text, html: onlyATable(html) ? html : "" };
  } catch {
    return null;
  }
}
