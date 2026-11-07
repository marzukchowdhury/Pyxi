// A small, consistent icon set: 1.5px strokes on a 20px grid, no emoji anywhere in the UI.
import type { SVGProps } from "react";

type P = SVGProps<SVGSVGElement>;
const base = (props: P) => ({
  width: 18, height: 18, viewBox: "0 0 20 20", fill: "none", stroke: "currentColor",
  strokeWidth: 1.5, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, ...props,
});

export const Play = (p: P) => <svg {...base(p)}><path d="M7 5.2v9.6a.6.6 0 0 0 .9.5l7.4-4.8a.6.6 0 0 0 0-1L7.9 4.7a.6.6 0 0 0-.9.5Z" fill="currentColor" stroke="none" /></svg>;
export const Pause = (p: P) => <svg {...base(p)}><path d="M7 5v10M13 5v10" /></svg>;
export const Prev = (p: P) => <svg {...base(p)}><path d="M12 5l-5 5 5 5" /></svg>;
export const Next = (p: P) => <svg {...base(p)}><path d="M8 5l5 5-5 5" /></svg>;
export const Close = (p: P) => <svg {...base(p)}><path d="M5.5 5.5l9 9M14.5 5.5l-9 9" /></svg>;
export const Expand = (p: P) => <svg {...base(p)}><path d="M4 8V4h4M16 8V4h-4M4 12v4h4M16 12v4h-4" /></svg>;
export const Collapse = (p: P) => <svg {...base(p)}><path d="M8 4v4H4M12 4v4h4M8 16v-4H4M12 16v-4h4" /></svg>;
export const Mic = (p: P) => <svg {...base(p)}><rect x="7.5" y="3" width="5" height="9" rx="2.5" /><path d="M5 9.5a5 5 0 0 0 10 0M10 14.5V17" /></svg>;
export const Send = (p: P) => <svg {...base(p)}><path d="M4 10h11M11 5.5 15.5 10 11 14.5" /></svg>;
export const Replay = (p: P) => <svg {...base(p)}><path d="M4.5 10a5.5 5.5 0 1 0 1.6-3.9M4.5 4v3h3" /><path d="M9 8.2v3.6l3-1.8Z" fill="currentColor" stroke="none" /></svg>;
export const Thread = (p: P) => <svg {...base(p)}><circle cx="4.5" cy="14.5" r="1.8" /><circle cx="15.5" cy="5.5" r="1.8" /><path d="M6 13.2C9 12 8 7.5 13.9 6.4" strokeDasharray="2 2.2" /></svg>;
export const Clock = (p: P) => <svg {...base(p)}><circle cx="10" cy="10" r="6.5" /><path d="M10 6.5V10l2.4 1.6" /></svg>;
export const Plus = (p: P) => <svg {...base(p)}><path d="M10 4.5v11M4.5 10h11" /></svg>;
export const Stop = (p: P) => <svg {...base(p)}><rect x="6" y="6" width="8" height="8" rx="1.5" /></svg>;
export const Minus = (p: P) => <svg {...base(p)}><path d="M5 10h10" /></svg>;
export const FileStack = (p: P) => <svg {...base(p)}><path d="M7 3.5h5.5L16 7v8.5a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1v-11a1 1 0 0 1 1-1Z" /><path d="M12.5 3.5V7H16M4 6v10.5a1 1 0 0 0 1 1h8" /></svg>;
export const Grid = (p: P) => <svg {...base(p)}><rect x="3.5" y="4" width="13" height="12" rx="1.5" /><path d="M3.5 8h13M3.5 12h13M8 4v12" /></svg>;
export const Cube = (p: P) => <svg {...base(p)}><path d="M10 3 16 6.5v7L10 17l-6-3.5v-7Z" /><path d="M4 6.5 10 10l6-3.5M10 10v7" /></svg>;

/** Pyxi's mark: the same little firefly as the on-screen presence. */
export const Mark = ({ size = 18, ...p }: P & { size?: number }) => (
  <svg width={size} height={size} viewBox="-12 -12 24 24" {...p}>
    <defs>
      <radialGradient id="mark-g">
        <stop offset="0" stopColor="#ffe08a" stopOpacity="0.7" />
        <stop offset="0.5" stopColor="#ffc861" stopOpacity="0.25" />
        <stop offset="1" stopColor="#ffc861" stopOpacity="0" />
      </radialGradient>
    </defs>
    <circle r="11" fill="url(#mark-g)" />
    <ellipse cx="-3" cy="-4.2" rx="3.2" ry="1.8" transform="rotate(-32 -3 -4.2)" fill="rgba(255,250,235,.35)" />
    <ellipse cx="3" cy="-4.2" rx="3.2" ry="1.8" transform="rotate(32 3 -4.2)" fill="rgba(255,250,235,.35)" />
    <circle r="3.4" fill="#fff3cf" />
  </svg>
);
export const Compass = (p: P) => <svg {...base(p)}><circle cx="10" cy="10" r="6.5" /><path d="M12.6 7.4 11 11l-3.6 1.6L9 9Z" /></svg>;
export const Feather = (p: P) => <svg {...base(p)}><path d="M15.5 4.5c-4.5 0-8.5 3.6-8.5 8.6V16M7 13.1h4.6c2.6 0 3.9-4.5 3.9-8.6M7 16l-2 1.5M9.5 10.5h3.6" /></svg>;
export const Spark = (p: P) => <svg {...base(p)}><path d="M10 3.5v3M10 13.5v3M3.5 10h3M13.5 10h3M5.4 5.4l2 2M12.6 12.6l2 2M14.6 5.4l-2 2M7.4 12.6l-2 2" /></svg>;
