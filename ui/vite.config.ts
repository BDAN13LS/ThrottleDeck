import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The production build is served by the Governor service out of
// `governor/static`, so the bundle lands in the repository, not in `ui/dist`.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../governor/static",
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8778",
    },
  },
});
