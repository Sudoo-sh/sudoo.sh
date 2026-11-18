import { readFileSync } from "node:fs";
import { fileURLToPath, URL } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

// `npm run dev`: the app with live reload; the API comes from a backend running locally (or through an SSH tunnel
// to a server: ssh -L 9002:127.0.0.1:9002 -L 9001:127.0.0.1:9001 <server>).
const api = process.env.SUDOO_API ?? "http://127.0.0.1:9002";
const events = process.env.SUDOO_EVENTS ?? "http://127.0.0.1:9001";

// `npm run build:demo` (mode "demo"): the same app with an in-browser backend and sample data (src/demo/), a static
// site for Cloudflare Pages in dist-demo/. Its page names sudoo.sh as the canonical address, and the host's headers
// and SPA fallback (demo/_headers, demo/_redirects) go next to it.
function demoSite(): Plugin {
  const file = (name: string) => readFileSync(new URL(`./demo/${name}`, import.meta.url), "utf8");
  return {
    name: "sudoo-demo-site",
    transformIndexHtml: (html) =>
      html
        .replace("<title>Sudoo.sh</title>", "<title>Sudoo.sh demo: the panel with sample data</title>")
        .replace(
          /<meta name="description" content="[^"]*" \/>/,
          '<meta name="description" content="Click through the Sudoo.sh panel with sample data: Odoo instances per branch, live metrics, backups and roles. Nothing to install." />\n    <link rel="canonical" href="https://sudoo.sh/" />',
        ),
    generateBundle() {
      for (const name of ["_headers", "_redirects"]) this.emitFile({ type: "asset", fileName: name, source: file(name) });
    },
  };
}

export default defineConfig(({ mode }) => ({
  plugins: [react(), ...(mode === "demo" ? [demoSite()] : [])],
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
    outDir: mode === "demo" ? "dist-demo" : "dist",
    target: "es2022",
    sourcemap: false,
    // fonts stay files: the Content-Security-Policy (font-src 'self') refuses fonts inlined as data: addresses
    assetsInlineLimit: (file: string) => (/\.(woff2?|ttf|otf)$/.test(file) ? false : undefined),
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
}));
