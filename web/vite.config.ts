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
  build: {
    target: "es2022",
    sourcemap: false,
    chunkSizeWarningLimit: 600,
    rolldownOptions: {
      output: {
        // libraries in chunks of their own: they change far less often than the panel's code, so browsers keep
        // them across releases (assets are immutable, by their content hash); the charts load only with a chart
        codeSplitting: {
          groups: [
            { name: "react", test: /node_modules[\\/](react|react-dom|scheduler|react-router|@tanstack)[\\/]/ },
            { name: "charts", test: /node_modules[\\/](chart\.js|@kurkle)[\\/]/ },
            { name: "icons", test: /node_modules[\\/]lucide-react[\\/]/ }, // one file, not one per icon
          ],
        },
      },
    },
  },
});
