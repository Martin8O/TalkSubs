import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The built page is served by the app's own server (parakeet_stt/server), which sets the real CSP.
// There is no dev server against the API: run `vite build --watch` next to the running app instead.
export default defineConfig({
  plugins: [react()],
  base: "/",
  cacheDir: fileURLToPath(new URL("../.cache/vite", import.meta.url)),
  server: { host: "127.0.0.1", strictPort: true, cors: false },
  preview: { host: "127.0.0.1", strictPort: true, cors: false },
  build: {
    outDir: fileURLToPath(new URL("../parakeet_stt/server/static", import.meta.url)),
    emptyOutDir: true,
    assetsInlineLimit: 0, // the CSP allows no data: scripts or styles
    modulePreload: { polyfill: false }, // the polyfill would be an inline script
    sourcemap: false,
    reportCompressedSize: false,
  },
  test: {
    include: ["src/**/*.test.ts"],
    environment: "node",
  },
});
