import { describe, expect, it } from "vitest";
import { tableClip } from "./tableClip";

const BORDERED = '<table border="1" cellpadding="4" style="border-collapse:collapse">';

describe("a table to paste", () => {
  it("carries the cells as text and as an HTML table", () => {
    const clip = tableClip(JSON.stringify({ text: "Region\tSales\r\nWest\t1200", html: "<table><tr><td>Region</td></tr></table>" }));
    expect(clip).toEqual({ text: "Region\tSales\r\nWest\t1200", html: "<table><tr><td>Region</td></tr></table>" });
  });

  it("is nothing when the message isn't one", () => {
    expect(tableClip("not json")).toBeNull();
    expect(tableClip(JSON.stringify({ text: "", html: "<table></table>" }))).toBeNull();
    expect(tableClip(JSON.stringify({ text: "a" }))).toBeNull();
    expect(tableClip(JSON.stringify(["a"]))).toBeNull();
  });

  it("keeps the table whatever the cells say", () => {
    // Words in a cell are only words: these used to cost the whole table.
    const text = "a\tb";
    for (const html of [
      "<table><tr><td>JavaScript: 12%</td><td>turn one = two</td></tr></table>",
      "<table><tr><td>an image, a script, a link</td><td>onclick = off</td></tr></table>",
      "<table><tr><td>1 &lt; 2 &amp;&amp; 3 &gt; 2</td><td>line one<br>line two</td></tr></table>",
      `${BORDERED}<tr><th>Region</th><th>Sales</th></tr><tr><td>West</td><td>1200</td></tr></table>`,
    ]) {
      expect(tableClip(JSON.stringify({ text, html }))).toEqual({ text, html });
    }
  });

  it("drops HTML that is more than a table, keeping the plain cells", () => {
    const text = "a\tb";
    for (const html of [
      "<div>hi</div>",
      "<table><tr><td><img src=x onerror=alert(1)></td></tr></table>",
      "<table><tr><td onclick=\"x()\">a</td></tr></table>",
      "<table><tr><td><script>1</script></td></tr></table>",
      // tags the old list of bad ones let through
      "<table><tr><td><a href=\"https://evil.example\">a</a></td></tr></table>",
      "<table><tr><td><svg><a xlink:href=\"x\">a</a></svg></td></tr></table>",
      "<table><tr><td><style>td{background:url(https://evil.example/x)}</style></td></tr></table>",
      "<table><tr><td><form action=\"https://evil.example\"><input></form></td></tr></table>",
      "<table><tr><td><b>a</b></td></tr></table>",
      "<table><tr><td colspan=\"2\">a</td></tr></table>",
      "<table style=\"background:url(https://evil.example/x)\"><tr><td>a</td></tr></table>",
      "<TABLE><tr><td>a</td></tr></TABLE>",
      "<table><tr><td>a</td></tr></table><meta http-equiv=\"refresh\" content=\"0;url=https://evil.example\">",
      "<table><tr><td>a</td></tr></table><",
      `${BORDERED}<tr><td><video src=x></video></td></tr></table>`,
    ]) {
      expect(tableClip(JSON.stringify({ text, html }))).toEqual({ text, html: "" });
    }
  });

  it("takes a message as long as the brain will send, and no longer", () => {
    const cell = "数".repeat(1000);
    const big = JSON.stringify({ text: cell, html: `<table><tr><td>${cell}</td></tr></table>` });
    expect(big.length).toBeLessThan(2_000_000);
    expect(tableClip(big)?.html).toContain(cell);
    expect(tableClip(JSON.stringify({ text: "a".repeat(2_000_000), html: "<table></table>" }))).toBeNull();
  });
});
