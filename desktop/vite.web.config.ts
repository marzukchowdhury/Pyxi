// Serves the renderer pages in a normal browser (no Electron) with a mock bridge.
// Handy for iterating on the overlay, 3D layer and panel: http://localhost:5199/overlay.html?backdrop=1
import { resolve } from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  root: resolve(__dirname, "src/renderer"),
  plugins: [react()],
  server: { port: 5199, strictPort: true },
});
