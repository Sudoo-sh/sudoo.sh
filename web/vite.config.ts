import { fileURLToPath, URL } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// `npm run dev`: the app with live reload; the API comes from a backend running locally (or through an SSH tunnel
// to a server: ssh -L 9002:127.0.0.1:9002 -L 9001:127.0.0.1:9001 <server>).
const api = process.env.SUDOO_API ?? "http://127.0.0.1:9002";
const events = process.env.SUDOO_EVENTS ?? "http://127.0.0.1:9001";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  server: {
    proxy: {
      "/api/v1/events": { target: events, changeOrigin: false },
      "/api": { target: api, changeOrigin: false },
      "/docs": { target: api, changeOrigin: false },
      "/static": { target: api, changeOrigin: false },
    },
  },
  build: { target: "es2022", sourcemap: false, chunkSizeWarningLimit: 600 },
});
