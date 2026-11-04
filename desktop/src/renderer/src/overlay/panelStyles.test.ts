import { describe, expect, it } from "vitest";
import { scopePanelCss } from "./panelStyles";

describe("panel styles inside the notch", () => {
  const css = `:root { --ink: #fff; }
html, body, #root { margin: 0; }
* { box-sizing: border-box; }
button.primary, .chips button { height: 32px; animation: rise .3s ease; }
@keyframes rise { from { opacity: 0; } }
.mark { animation: mark-pulse 2s infinite, rise 1s; }
@keyframes mark-pulse { 50% { opacity: .5; } }`;
  const out = scopePanelCss(css, ".nb");

  it("scopes every rule and drops page-level ones", () => {
    expect(out).toContain(".nb {");
    expect(out).toContain(".nb button.primary, .nb .chips button {");
    expect(out).toContain(".nb, .nb * {");
    expect(out).not.toMatch(/(^|\n)html|body \{/);
  });

  it("renames keyframes so they can't clash with the overlay's", () => {
    expect(out).toContain("@keyframes panel-rise");
    expect(out).toContain("animation: panel-rise .3s ease");
    expect(out).toContain("animation: panel-mark-pulse 2s infinite, panel-rise 1s");
  });
});
