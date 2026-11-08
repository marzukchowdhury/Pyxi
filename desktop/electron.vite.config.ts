import { resolve } from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "electron-vite";
import { devCsp } from "./csp";

export default defineConfig({
  main: { build: { outDir: "out/main" } },
  preload: { build: { outDir: "out/preload" } },
  renderer: {
    root: resolve(__dirname, "src/renderer"),
    plugins: [react(), devCsp()],
    build: {
      outDir: resolve(__dirname, "out/renderer"),
      rollupOptions: {
        input: {
          overlay: resolve(__dirname, "src/renderer/overlay.html"),
          panel: resolve(__dirname, "src/renderer/panel.html"),
          firefly: resolve(__dirname, "src/renderer/firefly.html"),
        },
      },
    },
  },
});
