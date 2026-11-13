/**
 * A typed diagram on the sketch board. The layout (scene/diagramLayout.ts) says where every
 * box, label and connector goes; this draws them with the board's own ink, so each one inks in
 * as its step is spoken: thin lines, one accent on the one or two items that matter.
 */
import type { DNode, DText, DWire, DiagramLayout, Pt } from "../scene/diagramLayout";
import { POINT } from "../scene/diagramLayout";
import { smooth } from "../scene/sketchPaths";

const LINE = 1.22;
const f = (n: number) => n.toFixed(1);

/** The outline of a box, in board px. */
export function nodePath(n: DNode, k: number): string {
  const [x, y, w, h] = [n.x * k, n.y * k, n.w * k, n.h * k];
  if (n.shape === "decision") {
    const p = POINT * k;
    return `M ${f(x + p)} ${f(y)} H ${f(x + w - p)} L ${f(x + w)} ${f(y + h / 2)} L ${f(x + w - p)} ${f(y + h)} H ${f(x + p)} L ${f(x)} ${f(y + h / 2)} Z`;
  }
  const r = n.shape === "pill" ? h / 2 : Math.min(1.3 * k, h / 2);
  const a = `A ${f(r)} ${f(r)} 0 0 1`;
  return `M ${f(x + r)} ${f(y)} H ${f(x + w - r)} ${a} ${f(x + w)} ${f(y + r)} V ${f(y + h - r)} ${a} ${f(x + w - r)} ${f(y + h)} H ${f(x + r)} ${a} ${f(x)} ${f(y + h - r)} V ${f(y + r)} ${a} ${f(x + r)} ${f(y)} Z`;
}

/** A connector: right-angled runs with softened corners (or one smooth arc), and its arrowhead. */
export function wirePath(w: DWire, k: number): { d: string; head: string | null } {
  const pts = w.points.map(([x, y]) => [x * k, y * k] as Pt);
  let d: string;
  if (w.curve) d = smooth(pts);
  else {
    d = `M ${f(pts[0][0])} ${f(pts[0][1])}`;
    for (let i = 1; i < pts.length - 1; i++) {
      const [p, c, n] = [pts[i - 1], pts[i], pts[i + 1]];
      const r = Math.min(1.2 * k, Math.hypot(c[0] - p[0], c[1] - p[1]) / 2, Math.hypot(n[0] - c[0], n[1] - c[1]) / 2);
      const toward = (to: Pt) => { const len = Math.hypot(to[0] - c[0], to[1] - c[1]) || 1; return `${f(c[0] + ((to[0] - c[0]) / len) * r)} ${f(c[1] + ((to[1] - c[1]) / len) * r)}`; };
      d += ` L ${toward(p)} Q ${f(c[0])} ${f(c[1])} ${toward(n)}`;
    }
    d += ` L ${f(pts[pts.length - 1][0])} ${f(pts[pts.length - 1][1])}`;
  }
  if (!w.arrow || pts.length < 2) return { d, head: null };
  const [tx, ty] = pts[pts.length - 1];
  const [px, py] = pts[pts.length - 2];
  const a = Math.atan2(ty - py, tx - px);
  const size = Math.max(5.5, k * 1.5);
  const wing = (s: number) => `${f(tx - Math.cos(a + s) * size)} ${f(ty - Math.sin(a + s) * size)}`;
  return { d, head: `M ${wing(0.45)} L ${f(tx)} ${f(ty)} L ${wing(-0.45)}` };
}

interface Props {
  layout: DiagramLayout;
  scale: number; // board px per frame unit
  step: number;
  delays: Map<string, number> | undefined; // start times of what the current step draws
  onAsk: (item: number) => void;
}

export function DiagramArt({ layout, scale, step, delays, onAsk }: Props) {
  const thin = 1.3 * Math.max(0.8, scale / 5.6);
  // delay < 0: it was drawn in an earlier step, so it is shown finished.
  const when = (key: string, at: number) => (at === step ? delays?.get(key) ?? 0 : -1);
  const t = (delay: number, extra = 0) => (delay < 0 ? { animationDelay: "0s", animationDuration: "0s" } : { animationDelay: `${(delay + extra).toFixed(2)}s` });
  const dots = new Set(layout.nodes.filter((n) => n.shape === "dot" && n.focus).map((n) => n.item));
  return (
    <>
      {layout.wires.map((w, i) => {
        if (w.step > step) return null;
        const delay = when(`wire${i}`, w.step);
        const { d, head } = wirePath(w, scale);
        const style = { ...t(delay), strokeWidth: w.role === "rule" ? thin * 0.8 : thin };
        return (
          <g key={`w${i}`} className="sk-shape">
            <path className={`sk-line ${w.role === "rule" ? "dg-rule" : "dg-wire"}`} d={d} pathLength={1} style={style} />
            {head && <path className="sk-line dg-wire" d={head} pathLength={1} style={{ ...t(delay, 0.3), strokeWidth: thin }} />}
          </g>
        );
      })}
      {layout.nodes.map((n, i) => {
        if (n.step > step) return null;
        const delay = when(`node${i}`, n.step);
        const focus = n.focus ? " focus" : "";
        if (n.shape === "dot") return <circle key={`n${i}`} className={`dg-dot${focus}`} cx={(n.x + n.w / 2) * scale} cy={(n.y + n.h / 2) * scale} r={(n.w / 2) * scale} style={t(delay)} />;
        if (n.shape === "plain" && !n.focus) return null;
        const d = nodePath(n, scale);
        return (
          <g key={`n${i}`} className="sk-shape">
            {n.focus && <path className="sk-fill dg-fill" d={d} style={t(delay, 0.3)} />}
            <path className={`sk-line dg-box${focus}`} d={d} pathLength={1} style={{ ...t(delay), strokeWidth: n.focus ? thin * 1.25 : thin }} />
          </g>
        );
      })}
      {layout.texts.map((text, i) => (text.step > step ? null : <Words key={`t${i}`} text={text} scale={scale} style={t(when(`text${i}`, text.step))} accent={text.role === "label" && text.item != null && dots.has(text.item)} onAsk={onAsk} />))}
    </>
  );
}

function Words({ text, scale, style, accent, onAsk }: { text: DText; scale: number; style: React.CSSProperties; accent: boolean; onAsk: (item: number) => void }) {
  const size = text.size * scale;
  const ask = text.role === "label" && text.item != null;
  return (
    <g className={`sk-text dg ${text.role}${accent ? " accent" : ""}${ask ? " ask" : ""}`} onClick={ask ? () => onAsk(text.item!) : undefined}>
      <text textAnchor={text.anchor} dominantBaseline="central" style={{ ...style, fontSize: size }}>
        {text.lines.map((line, k) => <tspan key={k} x={text.ax * scale} y={(text.y + (k + 0.5) * text.size * LINE) * scale}>{line}</tspan>)}
      </text>
    </g>
  );
}
