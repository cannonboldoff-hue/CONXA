import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

// Renderer is loaded from file:// in the packaged app, so use relative asset paths.
export default defineConfig({
  root: path.join(__dirname),
  base: "./",
  plugins: [react(), tailwindcss()],
  server: { port: Number(process.env.CONXA_RENDERER_PORT) || 5174, strictPort: true },
  build: {
    outDir: path.join(__dirname, "dist"),
    emptyOutDir: true,
  },
  resolve: {
    alias: { "@": path.join(__dirname, "src") },
  },
});
