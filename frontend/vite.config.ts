import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The operator token is injected by the dev-server proxy, server side, so it never ships in
// the browser bundle (and EventSource, which cannot set headers, is authenticated too).
const token = process.env.RELAY_API_TOKEN ?? "dev-operator-token";
const target = `http://127.0.0.1:${process.env.RELAY_API_PORT ?? "8077"}`;

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target, headers: { Authorization: `Bearer ${token}` } },
      "/health": { target },
    },
  },
});
