import type { SceneSpec } from "../shared/protocol";

/**
 * Restrained palettes: bodies are always dark clear-coated glass; colour lives only in light
 * (rims, edges, emissive focus, particles). Accents are desaturated so nothing shouts.
 */
export interface Theme {
  palette: string[];
  body: string;
  edge: string;
  text: string;
  panel: string;
  grid: string | null;
  rim: string;
}

export const THEMES: Record<SceneSpec["theme"], Theme> = {
  aurora: { palette: ["#9ecbff", "#8fe3d0", "#c6b8ff", "#ffd2a1", "#f5a8c8", "#a6e4ff"], body: "#131722", edge: "#b9d8ff", text: "#f4f7fb", panel: "#10131b", grid: null, rim: "#9ecbff" },
  blueprint: { palette: ["#8fd1ff", "#7fb8ff", "#86e7f0", "#aac4ff", "#9fe6d6"], body: "#0f1622", edge: "#9fd4ff", text: "#eef6ff", panel: "#0d1420", grid: "#1a2a40", rim: "#8fd1ff" },
  candy: { palette: ["#f7b4d0", "#b9c3ff", "#a8ecc8", "#ffe1a6", "#ffc4a3", "#d5c4ff"], body: "#1a1520", edge: "#f7cde0", text: "#fff8fb", panel: "#171219", grid: null, rim: "#f7b4d0" },
  mono: { palette: ["#e8edf5", "#cfd6e2", "#b4bdcc"], body: "#15171c", edge: "#e8edf5", text: "#ffffff", panel: "#111318", grid: "#262a33", rim: "#e8edf5" },
};

export function nodeColor(theme: Theme, index: number, explicit?: string | null): string {
  return explicit ?? theme.palette[index % theme.palette.length];
}
