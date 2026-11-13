import { describe, expect, it } from "vitest";
import type { DiagramItem, DiagramLink, DiagramSpec, DiagramType } from "../shared/protocol";
import { FRAME_W, H_MAX, SIZES, SMALL, diagramTimeline, layoutDiagram, textWidth, wrap, type DiagramLayout, type Pt, type Rect } from "./diagramLayout";
import { rng } from "./sketchPaths";

// Labels as the model is asked to write them (1 to 4 words), and labels at the very limit the
// brain lets through (28 characters, a word of up to 14): src/pyxi/scenes/diagram.py.
const SHORT = ["Ask", "Check order", "Refund", "Approve it", "Send money", "Tell them", "Log it", "Close", "Done"];
const USUAL = ["Customer asks", "Check the order", "Within 30 days?", "Manager approves", "Finance pays out", "Money goes back", "Email the customer", "Close the ticket", "Keep a record"];
const LONG = [
  "Authentication verification", "Reimbursement authorisation", "Wait for the bank to answer", "Manager signs it off today",
  "Administration", "International transfers", "Check the delivery address", "Tell the customer in writing", "Responsibility transferred",
];
const SETS: [string, string[]][] = [["short", SHORT], ["usual", USUAL], ["long", LONG]];

const items = (n: number, labels: string[], extra: (i: number) => Partial<DiagramItem> = () => ({})): DiagramItem[] =>
  Array.from({ length: n }, (_, i) => ({ id: `n${i}`, label: labels[i % labels.length], step: Math.min(4, Math.floor(i / 2)), focus: i === 1 || i === n - 1, ...extra(i) }));
const spec = (type: DiagramType, its: DiagramItem[], more: Partial<DiagramSpec> = {}): DiagramSpec => ({ type, items: its, links: [], ...more });

const inset = (r: Rect, by: number): Rect => ({ x: r.x + by, y: r.y + by, w: r.w - 2 * by, h: r.h - 2 * by });
const overlap = (a: Rect, b: Rect) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
const within = (a: Rect, b: Rect, slack = 0.01) => a.x >= b.x - slack && a.y >= b.y - slack && a.x + a.w <= b.x + b.w + slack && a.y + a.h <= b.y + b.h + slack;

/** Does the segment p-q pass through the rectangle? (Liang-Barsky) */
function crosses(p: Pt, q: Pt, r: Rect): boolean {
  if (r.w <= 0 || r.h <= 0) return false;
  let [t0, t1] = [0, 1];
  const [dx, dy] = [q[0] - p[0], q[1] - p[1]];
  for (const [d, lo, hi] of [[dx, r.x - p[0], r.x + r.w - p[0]], [dy, r.y - p[1], r.y + r.h - p[1]]]) {
    if (Math.abs(d) < 1e-9) {
      if (lo > 0 || hi < 0) return false;
    } else {
      const [a, b] = [lo / d, hi / d];
      t0 = Math.max(t0, Math.min(a, b));
      t1 = Math.min(t1, Math.max(a, b));
      if (t0 > t1) return false;
    }
  }
  return true;
}

/** Everything the layout promises. Returns what is wrong, in words. */
function problems(l: DiagramLayout, s: DiagramSpec, o: { whole?: boolean; minSize?: number } = {}): string[] {
  const bad: string[] = [];
  const frame: Rect = { x: 0, y: 0, w: l.w, h: l.h };
  const name = (i: number | null) => (i == null ? "-" : s.items[i]?.label);
  if (l.w !== FRAME_W || l.h > H_MAX + 1e-6) bad.push(`frame is ${l.w} x ${l.h.toFixed(1)}`);
  if (l.size < (o.minSize ?? SMALL[SMALL.length - 1]) - 1e-9) bad.push(`type is ${l.size}`);
  for (const n of l.nodes) if (!within(n, frame)) bad.push(`box "${name(n.item)}" leaves the frame`);
  for (const t of l.texts) if (!within(t, frame)) bad.push(`text "${t.lines.join(" ")}" leaves the frame`);
  for (const w of l.wires) for (const [x, y] of w.points) if (x < -0.01 || y < -0.01 || x > l.w + 0.01 || y > l.h + 0.01) bad.push("a line leaves the frame");
  l.nodes.forEach((a, i) => l.nodes.forEach((b, j) => j > i && overlap(inset(a, -0.25), inset(b, -0.25)) && bad.push(`boxes "${name(a.item)}" and "${name(b.item)}" overlap`)));
  l.texts.forEach((a, i) => l.texts.forEach((b, j) => j > i && overlap(inset(a, -0.1), inset(b, -0.1)) && bad.push(`texts "${a.lines.join(" ")}" and "${b.lines.join(" ")}" overlap`)));
  for (const t of l.texts) {
    for (const line of t.lines) if (textWidth(line, t.size, t.role === "head" || t.role === "center") > t.w + 1e-6) bad.push(`"${line}" is wider than its room`);
    for (const n of l.nodes) {
      const own = t.role === "label" && t.item === n.item && n.shape !== "dot";
      if (own ? !within(t, inset(n, 0.5)) : overlap(t, inset(n, -0.2))) bad.push(`text "${t.lines.join(" ")}" ${own ? "spills out of" : "sits on"} box "${name(n.item)}"`);
    }
  }
  l.wires.forEach((w) => {
    for (let k = 1; k < w.points.length; k++) {
      const [p, q] = [w.points[k - 1], w.points[k]];
      if (!w.curve && Math.abs(p[0] - q[0]) > 0.01 && Math.abs(p[1] - q[1]) > 0.01) bad.push("a slanted connector");
      for (const n of l.nodes) if (!(w.role === "rule" && n.shape === "dot") && crosses(p, q, inset(n, 0.15))) bad.push(`a line runs through box "${name(n.item)}"`);
      for (const t of l.texts) if (crosses(p, q, inset(t, 0.05))) bad.push(`a line runs through text "${t.lines.join(" ")}"`);
    }
  });
  if (s.type === "flowchart") {
    // No two links lie along each other.
    const runs = l.wires.flatMap((w, wi) => w.points.slice(1).map((q, k) => ({ wi, p: w.points[k], q })));
    runs.forEach((a, i) => runs.forEach((b, j) => {
      if (j <= i || a.wi === b.wi) return;
      for (const axis of [0, 1] as const) {
        const other = 1 - axis;
        if (Math.abs(a.p[axis] - a.q[axis]) > 0.01 || Math.abs(b.p[axis] - b.q[axis]) > 0.01 || Math.abs(a.p[axis] - b.p[axis]) > 0.3) continue;
        const lo = Math.max(Math.min(a.p[other], a.q[other]), Math.min(b.p[other], b.q[other]));
        const hi = Math.min(Math.max(a.p[other], a.q[other]), Math.max(b.p[other], b.q[other]));
        if (hi - lo > 0.3) bad.push("two links lie along each other");
      }
    }));
  }
  const shown = Math.min(s.items.length, 9);
  if (l.nodes.length !== shown || new Set(l.nodes.map((n) => n.item)).size !== shown) bad.push(`${l.nodes.length} boxes for ${shown} items`);
  if (l.nodes.filter((n) => n.focus).length > 2) bad.push("more than two accents");
  if (o.whole) {
    s.items.forEach((it, i) => {
      const t = l.texts.find((t) => t.role === "label" && t.item === i);
      if (t?.lines.join(" ") !== it.label) bad.push(`"${it.label}" is shown as "${t?.lines.join(" ")}"`);
      const n = l.nodes.find((n) => n.item === i);
      if (n?.step !== it.step) bad.push(`"${it.label}" draws at the wrong step`);
    });
    if (l.notes.length) bad.push(`notes: ${l.notes.join("; ")}`);
  }
  return [...new Set(bad)];
}

/** Every case of a type, for 2 to 9 items and the three kinds of label. */
function sweep(make: (n: number, labels: string[]) => DiagramSpec[], o: { minUsual?: number } = {}) {
  const failures: string[] = [];
  let count = 0;
  for (const [kind, labels] of SETS)
    for (let n = 2; n <= 9; n++)
      for (const s of make(n, labels)) {
        count++;
        const l = layoutDiagram(s);
        const bad = problems(l, s, { whole: true, minSize: kind === "long" ? SMALL[SMALL.length - 1] : (o.minUsual ?? SIZES[SIZES.length - 1]) });
        if (bad.length) failures.push(`${s.type} n=${n} ${kind}: ${bad.slice(0, 3).join(" | ")}${s.links.length ? " " + s.links.map((x) => `${x.from}>${x.to}`).join(" ") : ""}`);
      }
  expect(failures.slice(0, 8)).toEqual([]);
  return count;
}

// ---- shapes of content, inside the limits the brain enforces

/** Every tree of n nodes up to four levels deep, as parent lists (a sample when there are many). */
function trees(n: number, rand: () => number, howMany: number): DiagramLink[][] {
  const out: DiagramLink[][] = [];
  const star = Array.from({ length: n - 1 }, (_, i) => ({ from: "n0", to: `n${i + 1}` }));
  out.push(star);
  for (let k = 0; k < howMany; k++) {
    const depth = [0];
    const links: DiagramLink[] = [];
    for (let i = 1; i < n; i++) {
      const able = depth.map((d, j) => (d < 3 ? j : -1)).filter((j) => j >= 0);
      const p = able[Math.floor(rand() * able.length)];
      depth.push(depth[p] + 1);
      links.push({ from: `n${p}`, to: `n${i}` });
    }
    out.push(links);
  }
  return out;
}

/** A flowchart inside the limits: at most 6 levels, 3 side by side, 3 ways out of an item, 12 links. */
function flow(n: number, rand: () => number): DiagramLink[] | null {
  const levels = Math.min(5, 2 + Math.floor(rand() * Math.min(4, n - 1)));
  const rank = [0];
  const size = new Array(levels).fill(0);
  size[0] = 1;
  for (let i = 1; i < n; i++) {
    // Fill each level once, then spread the rest.
    const r = i < levels ? i : 1 + Math.floor(rand() * (levels - 1));
    rank.push(r);
    size[r]++;
  }
  if (size.some((c) => c > 3)) return null;
  const links: DiagramLink[] = [];
  const outs = new Array(n).fill(0);
  const add = (u: number, v: number) => {
    if (outs[u] >= 3 || links.length >= 12 || links.some((l) => l.from === `n${u}` && l.to === `n${v}`)) return false;
    outs[u]++;
    links.push({ from: `n${u}`, to: `n${v}` });
    return true;
  };
  for (let v = 1; v < n; v++) {
    const ups = rank.map((r, u) => (r === rank[v] - 1 ? u : -1)).filter((u) => u >= 0 && outs[u] < 3);
    if (!ups.length) return null;
    add(ups[Math.floor(rand() * ups.length)], v);
  }
  for (let k = Math.floor(rand() * 4); k > 0; k--) {
    const [u, v] = [Math.floor(rand() * n), Math.floor(rand() * n)];
    if (rank[u] < rank[v]) add(u, v); // a merge, or a link that skips levels
  }
  const words = ["yes", "no", "maybe", "over $100", "approved", "on request"];
  return links.map((l) => (outs[Number(l.from.slice(1))] > 1 ? { ...l, label: words[Math.floor(rand() * words.length)] } : l));
}

describe("typed diagrams: tidy by construction", () => {
  it("measures and wraps text the same way every time", () => {
    expect(wrap("Check the order", 100, 3, 3)).toEqual(["Check the order"]);
    expect(wrap("Check the order", 20, 3, 3)).toEqual(["Check", "the order"]); // the widest line as narrow as it can be
    expect(wrap("Reimbursement", 10, 3, 3)).toBeNull(); // a word is never broken
    expect(wrap("one two three four five six", 12, 3, 2)).toBeNull(); // nor squeezed into too few lines
    const lines = wrap("Wait for the bank to answer", 30, 2.6, 3)!;
    expect(lines.every((line) => textWidth(line, 2.6) <= 30)).toBe(true);
    expect(lines.join(" ")).toBe("Wait for the bank to answer");
  });

  it("steps: 2 to 9 in a row or in rows, joined in order", () => {
    sweep((n, labels) => [spec("steps", items(n, labels))]);
    const l = layoutDiagram(spec("steps", items(7, USUAL)));
    expect(l.wires).toHaveLength(6);
    expect(l.wires.every((w) => w.arrow)).toBe(true);
    expect(new Set(l.nodes.map((n) => n.y)).size).toBeGreaterThan(1); // seven do not fit in one row
  });

  it("timeline: names take turns above and below the line", () => {
    sweep((n, labels) => [
      spec("timeline", items(n, labels, (i) => ({ when: `${1960 + i * 7}` }))),
      spec("timeline", items(n, labels, (i) => ({ when: `${i + 10} March 2024` }))), // the longest date allowed
      spec("timeline", items(n, labels)),
    ]);
    const l = layoutDiagram(spec("timeline", items(5, USUAL, (i) => ({ when: `Day ${i + 1}` }))));
    const base = l.wires[0].points[0][1];
    const side = (i: number) => Math.sign(l.texts.find((t) => t.role === "label" && t.item === i)!.y - base);
    expect([0, 1, 2, 3, 4].map(side)).toEqual([-1, 1, -1, 1, -1]);
    expect(l.texts.filter((t) => t.role === "when")).toHaveLength(5);
  });

  it("comparison in two columns: every split up to five a side", () => {
    sweep((n, labels) => {
      const out: DiagramSpec[] = [];
      for (let left = Math.max(1, n - 5); left <= Math.min(5, n - 1); left++)
        out.push(
          spec("comparison", items(n, labels, (i) => ({ side: i < left ? "left" : "right" })), { columns: ["Renting", "Buying a home"] }),
          spec("comparison", items(n, labels, (i) => ({ side: i < left ? "left" : "right" })), { columns: ["Renting for now", "Buying your home"] }), // 16 characters: the limit
        );
      return out;
    });
    // A column is nearly half the board wide, so its lines may be short phrases (44 characters).
    const phrases = [
      "Selling costs eat the gains if you leave early", "Upfront: a down payment and the closing costs", "Flexible: you can move out at short notice",
      "Repairs are always the landlord's job to do", "The rent goes up most years and never stops",
    ].map((p) => p.slice(0, 44).trim());
    for (let n = 2; n <= 9; n++)
      for (let left = Math.max(1, n - 5); left <= Math.min(5, n - 1); left++) {
        const s = spec("comparison", items(n, phrases, (i) => ({ side: i < left ? "left" : "right" })), { columns: ["Renting for now", "Buying your home"] });
        expect(problems(layoutDiagram(s), s, { whole: true })).toEqual([]);
      }
    const l = layoutDiagram(spec("comparison", items(6, USUAL, (i) => ({ side: i % 2 ? "right" : "left" })), { columns: ["Renting", "Buying"] }));
    expect(l.texts.filter((t) => t.role === "head").map((t) => t.lines[0])).toEqual(["Renting", "Buying"]);
    const xs = (side: number) => l.nodes.filter((n) => n.item % 2 === side).map((n) => n.x);
    expect(Math.max(...xs(0))).toBeLessThan(Math.min(...xs(1))); // left stays left of right
  });

  it("comparison on two axes: every item stays in its own quarter", () => {
    const rand = rng(7);
    const failures: string[] = [];
    sweep((n, labels) => {
      const out: DiagramSpec[] = [];
      for (let k = 0; k < 12; k++) {
        const per = [0, 0, 0, 0];
        const its = items(n, labels, () => {
          for (;;) {
            const [x, y] = k === 0 ? [50 + (rand() < 0.5 ? -1 : 1) * 49, 50 + (rand() < 0.5 ? -1 : 1) * 49] : [rand() * 100, rand() * 100];
            const q = (x >= 50 ? 1 : 0) + (y >= 50 ? 2 : 0);
            if (per[q] < 4) { per[q]++; return { x, y }; }
          }
        });
        const s = spec("comparison", its, { axes: { x: ["Cheap", "Expensive"], y: ["Slow", "Fast"] } });
        const l = layoutDiagram(s);
        const [ax, ay] = [l.wires[1].points[0][0], l.wires[0].points[0][1]];
        its.forEach((it, i) => {
          const node = l.nodes.find((nd) => nd.item === i)!;
          if (node.x + node.w / 2 > ax !== it.x! >= 50 || node.y + node.h / 2 < ay !== it.y! >= 50) failures.push(`${it.label} left its quarter`);
        });
        out.push(s);
      }
      return out;
    });
    expect(failures).toEqual([]);
    const l = layoutDiagram(spec("comparison", items(4, SHORT, (i) => ({ x: 20, y: 80 - i })), { axes: { x: ["Cheap", "Costly"], y: ["Slow", "Fast"] } }));
    expect(l.texts.filter((t) => t.role === "axis").map((t) => t.lines[0]).sort()).toEqual(["Cheap", "Costly", "Fast", "Slow"]);
  });

  it("tree: any shape up to four levels, never a diagonal", () => {
    const rand = rng(11);
    const count = sweep((n, labels) => trees(n, rand, 30).map((links) => spec("tree", items(n, labels), { links })), { minUsual: SMALL[0] });
    expect(count).toBeGreaterThan(600);
    // A small tree stands root on top; a wide one becomes an indented list rather than cramped boxes.
    const small = layoutDiagram(spec("tree", items(4, USUAL), { links: [{ from: "n0", to: "n1" }, { from: "n0", to: "n2" }, { from: "n0", to: "n3" }] }));
    expect(new Set(small.nodes.map((n) => n.y)).size).toBe(2);
    const wide = layoutDiagram(spec("tree", items(9, USUAL), { links: trees(9, rand, 0)[0] }));
    expect(new Set(wide.nodes.map((n) => n.y)).size).toBe(9);
    expect(wide.wires.every((w) => !w.arrow)).toBe(true); // a hierarchy, not a flow
  });

  it("loop: stations round an oval, each arc ending on the next", () => {
    // Eight stations at most (the brain turns a ninth away): nine long names leave no room for the arcs.
    sweep((n, labels) => (n > 8 ? [] : [spec("loop", items(n, labels)), spec("loop", items(n, labels), { center: "Trust builds" }), spec("loop", items(n, labels), { center: "Reputation and trust" })]));
    const nine = spec("loop", items(9, LONG));
    expect(problems(layoutDiagram(nine), nine)).toEqual([]); // still no collision, only shorter words
    const l = layoutDiagram(spec("loop", items(4, USUAL), { center: "Habit" }));
    expect(l.wires).toHaveLength(4);
    expect(l.wires.every((w) => w.curve && w.arrow)).toBe(true);
    expect(l.texts.find((t) => t.role === "center")?.lines).toEqual(["Habit"]);
    // Clockwise from the top: the second station is to the right of the first.
    expect(l.nodes[1].x).toBeGreaterThan(l.nodes[0].x);
  });

  // Random flowcharts inside the limits. DIAGRAM_STRESS=6000 DIAGRAM_SEED=977 runs about a
  // hundred thousand of them (a quarter of a minute) after a change to the layout.
  it("flowchart: branches, merges and links that skip a level", { timeout: 300_000 }, () => {
    const rand = rng(Number(process.env.DIAGRAM_SEED ?? 23));
    const tries = Number(process.env.DIAGRAM_STRESS ?? 150);
    let made = 0;
    sweep((n, labels) => {
      const out: DiagramSpec[] = [];
      for (let k = 0; k < tries; k++) {
        const links = flow(n, rand);
        if (links) out.push(spec("flowchart", items(n, labels), { links }));
      }
      made += out.length;
      return out;
    }, { minUsual: SMALL[SMALL.length - 1] });
    expect(made).toBeGreaterThan(900);
    if (process.env.DIAGRAM_STRESS) console.info(`${made} flowcharts checked`);
    // The shape says what an item is: where it starts and ends, and where it branches.
    const refund = spec("flowchart", items(6, ["Refund asked", "Within 30 days?", "Declined", "Over $100?", "Manager signs", "Money sent"]), {
      links: [
        { from: "n0", to: "n1" }, { from: "n1", to: "n2", label: "no" }, { from: "n1", to: "n3", label: "yes" },
        { from: "n3", to: "n4", label: "yes" }, { from: "n3", to: "n5", label: "no" }, { from: "n4", to: "n5" },
      ],
    });
    const l = layoutDiagram(refund);
    expect(problems(l, refund, { whole: true, minSize: SIZES[SIZES.length - 1] })).toEqual([]);
    expect(l.nodes.map((n) => n.shape)).toEqual(["pill", "decision", "pill", "decision", "box", "pill"]);
    expect(l.texts.filter((t) => t.role === "link").map((t) => t.lines[0]).sort()).toEqual(["no", "no", "yes", "yes"]);
    expect(l.nodes[0].y).toBeLessThan(l.nodes[1].y); // top to bottom
  });

  it("outside the limits it still never collides: it cuts and says so", () => {
    const wild = ["Supercalifragilisticexpialidocious", "WWWWWWWWWWWWWW MMMMMMMMMMMMMM WWWWWWWWWWWWWW", "a b c d e f g h i j k l m n o p q r s t u v w x y z", "x"];
    const rand = rng(5);
    const failures: string[] = [];
    for (const type of ["steps", "flowchart", "timeline", "comparison", "tree", "loop"] as DiagramType[])
      for (const n of [1, 2, 5, 9, 14]) {
        const its = items(n, wild, (i) => ({ focus: true, side: i % 3 ? "left" : "right", when: "the twenty-first century", x: rand() * 100, y: rand() * 100 }));
        const links = its.map((it, i) => ({ from: it.id, to: its[(i * 7 + 3) % n].id, label: "an unreasonably long label" })); // loops and tangles
        for (const more of [{}, { axes: { x: ["Inexpensiveness", "Costliness"], y: ["Sluggishness", "Speediness"] } as DiagramSpec["axes"] }]) {
          const s = spec(type, its, { links, columns: ["A very long column name indeed", "Another long column name"], center: "The middle of everything there is", ...more });
          const l = layoutDiagram(s);
          const bad = problems({ ...l, h: Math.min(l.h, H_MAX) }, s).filter((b) => !b.includes("leaves the frame") && !b.includes("lie along") && !b.includes("type is"));
          if (bad.length) failures.push(`${type} n=${n}: ${bad.slice(0, 3).join(" | ")}`);
          if (l.nodes.filter((nd) => nd.focus).length > 2) failures.push(`${type}: accents`);
        }
      }
    expect(failures).toEqual([]);
    expect(layoutDiagram(spec("steps", items(14, SHORT))).notes).toEqual(["showing 9 of 14 items"]);
  });

  it("inks a step in order: the link, then its box, then the words", () => {
    const l = layoutDiagram(spec("steps", [
      { id: "a", label: "Ask", step: 0 }, { id: "b", label: "Check", step: 1 }, { id: "c", label: "Pay", step: 1 },
    ]));
    const first = diagramTimeline(l, 0);
    expect([...first.delays.keys()].sort()).toEqual(["node0", "text0"]);
    const second = diagramTimeline(l, 1);
    const at = (k: string) => second.delays.get(k)!;
    expect(at("wire0")).toBeLessThan(at("node1"));
    expect(at("node1")).toBeLessThan(at("text1"));
    expect(at("text1")).toBeLessThan(at("node2"));
    expect(second.total).toBeGreaterThan(at("text2"));
  });
});
