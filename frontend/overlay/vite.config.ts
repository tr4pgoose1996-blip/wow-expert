import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The overlay renderer is a static SPA loaded by Electron via file://. The
// dev server is only used for live iteration; production uses the built files.
export default defineConfig({
  root: ".",
  plugins: [react()],
  base: "./",
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    port: 5174,
    strictPort: true,
  },
});
