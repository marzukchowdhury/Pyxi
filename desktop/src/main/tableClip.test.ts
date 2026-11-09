import { describe, expect, it } from "vitest";
import { tableClip } from "./tableClip";

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

  it("drops HTML that is more than a table, keeping the plain cells", () => {
    const text = "a\tb";
    for (const html of [
      "<div>hi</div>",
      "<table><tr><td><img src=x onerror=alert(1)></td></tr></table>",
      "<table><tr><td onclick=\"x()\">a</td></tr></table>",
      "<table><tr><td><script>1</script></td></tr></table>",
    ]) {
      expect(tableClip(JSON.stringify({ text, html }))).toEqual({ text, html: "" });
    }
  });
});
