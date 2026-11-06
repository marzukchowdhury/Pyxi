/**
 * The full notch reuses the panel's sections, but the panel's stylesheet is written for a
 * window of its own (rules like `button.primary`, `input`, `kbd`, and keyframes named `rise`
 * and `shimmer` that the overlay also defines). Loaded as-is it would restyle the overlay.
 * So it's loaded as text and scoped: every rule is prefixed with the notch's body, page-level
 * rules are dropped, and keyframes get their own names.
 */
import raw from "../panel/panel.css?inline";

const SCOPE = ".notch-full";
const PREFIX = "panel-";

export function scopePanelCss(css: string, scope = SCOPE): string {
  const keyframes = new Set<string>();
  for (const m of css.matchAll(/@keyframes\s+([\w-]+)/g)) keyframes.add(m[1]);
  const renameAnimations = (body: string) =>
    body.replace(/(animation(?:-name)?\s*:\s*)([^;}]+)/g, (_m, prop: string, value: string) =>
      prop + value.replace(/[\w-]+/g, (w) => (keyframes.has(w) ? PREFIX + w : w)),
    );

  const out: string[] = [];
  let i = 0;
  while (i < css.length) {
    const open = css.indexOf("{", i);
    if (open < 0) break;
    const head = css.slice(i, open).replace(/\/\*[\s\S]*?\*\//g, "").trim();
    // find the matching close brace (keyframes nest one level)
    let depth = 0;
    let j = open;
    for (; j < css.length; j++) {
      if (css[j] === "{") depth++;
      else if (css[j] === "}" && --depth === 0) break;
    }
    const body = css.slice(open + 1, j);
    i = j + 1;
    if (head.startsWith("@keyframes")) {
      out.push(`@keyframes ${PREFIX}${head.split(/\s+/)[1]} {${body}}`);
      continue;
    }
    if (head.startsWith("@")) continue; // nothing else in the panel sheet; skip rather than leak
    const selectors = head
      .split(",")
      .map((sel) => sel.trim())
      .filter((sel) => sel && !/^(html|body|#root)\b/.test(sel))
      .map((sel) => (sel === ":root" ? scope : sel === "*" ? `${scope}, ${scope} *` : `${scope} ${sel}`));
    if (selectors.length) out.push(`${selectors.join(", ")} {${renameAnimations(body)}}`);
  }
  return out.join("\n");
}

let installed = false;
export function installPanelStyles(): void {
  if (installed || typeof document === "undefined") return;
  installed = true;
  const style = document.createElement("style");
  style.dataset.pyxi = "panel-in-notch";
  style.textContent = scopePanelCss(raw);
  document.head.appendChild(style);
}
