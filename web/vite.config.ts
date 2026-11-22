import { createHash } from "node:crypto";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath, URL } from "node:url";
import tailwindcss from "@tailwindcss/vite";
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

// The premium features in the demo: their compiled web files (never their source) are added at build time from a
// folder outside the repository's tracked files: SUDOO_DEMO_PREMIUM, else web/demo-premium/ (git-ignored), one
// folder per package as the premium build makes it (<id>/ui-manifest.json and <id>/ui/*.js|css|json). They are
// served at /premium/ui/<id>/<file> as a panel serves them, and the demo's backend lists them with their SHA-256
// (virtual:sudoo-demo-premium), so the panel's own loader checks and loads them. Without the folder the demo has no
// premium files and shows each premium feature's plan card. Other builds always get an empty list.
const PREMIUM_IDS = ["teams", "domains", "offsite", "support", "servers"];
const PREMIUM_FILE = /^ui\/[\w.-]+\.(js|css|json)$/;
const VIRTUAL = "virtual:sudoo-demo-premium";

interface DemoPremiumPackage {
  version: string | null;
  routes: unknown[];
  sections: unknown[];
  styles: string[];
  strings: string | null;
  files: Record<string, string>;
}

function readPremium(folder: string) {
  const packages: Record<string, DemoPremiumPackage> = {};
  const data = new Map<string, Buffer>(); // "<id>/<file>" -> content
  if (!existsSync(folder)) return { packages, data };
  for (const id of readdirSync(folder)) {
    const manifestFile = join(folder, id, "ui-manifest.json");
    if (!PREMIUM_IDS.includes(id) || !existsSync(manifestFile)) continue;
    const manifest = JSON.parse(readFileSync(manifestFile, "utf8")) as Partial<DemoPremiumPackage>;
    const files: Record<string, string> = {};
    for (const name of readdirSync(join(folder, id, "ui"))) {
      const file = `ui/${name}`;
      if (!PREMIUM_FILE.test(file) || !statSync(join(folder, id, file)).isFile()) continue; // never source maps or sources
      const content = readFileSync(join(folder, id, file));
      files[file] = createHash("sha256").update(content).digest("hex");
      data.set(`${id}/${file}`, content);
    }
    packages[id] = {
      version: typeof manifest.version === "string" ? manifest.version : null,
      routes: manifest.routes ?? [],
      sections: manifest.sections ?? [],
      styles: manifest.styles ?? [],
      strings: manifest.strings ?? null,
      files,
    };
  }
  return { packages, data };
}

function demoPremium(mode: string): Plugin {
  const folder = resolve(process.env.SUDOO_DEMO_PREMIUM || fileURLToPath(new URL("./demo-premium", import.meta.url)));
  const found = () => (mode === "demo" ? readPremium(folder) : { packages: {}, data: new Map<string, Buffer>() });
  const types: Record<string, string> = { js: "text/javascript", css: "text/css", json: "application/json" };
  return {
    name: "sudoo-demo-premium",
    resolveId: (id) => (id === VIRTUAL ? `\0${VIRTUAL}` : undefined),
    load: (id) => (id === `\0${VIRTUAL}` ? `export default ${JSON.stringify(found().packages)};` : undefined),
    configureServer(server) {
      if (mode !== "demo") return;
      server.middlewares.use("/premium/ui/", (request, response, next) => {
        const path = decodeURIComponent((request.url ?? "").split("?")[0]!.replace(/^\//, ""));
        const content = found().data.get(path);
        if (!content) return next();
        response.setHeader("Content-Type", types[path.split(".").pop()!]!);
        response.end(content);
      });
    },
    generateBundle() {
      if (mode !== "demo") return;
      const { packages, data } = found();
      for (const [path, source] of data) this.emitFile({ type: "asset", fileName: `premium/ui/${path}`, source });
      const ids = Object.keys(packages);
      this.info(ids.length ? `premium features: ${ids.join(", ")}` : "premium features: none (each shows its plan card)");
    },
  };
}

export default defineConfig(({ mode }) => ({
  plugins: [react(), tailwindcss(), demoPremium(mode), ...(mode === "demo" ? [demoSite()] : [])],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  server: {
    proxy: {
      "/api/v1/events": { target: events, changeOrigin: false },
      "^/api/v1/instances/[a-z0-9-]+/logs/stream": { target: events, changeOrigin: false },
      "/api": { target: api, changeOrigin: false },
      "/docs": { target: api, changeOrigin: false },
      "/static": { target: api, changeOrigin: false },
      // the demo serves its premium files itself (demoPremium)
      ...(mode === "demo" ? {} : { "/premium": { target: api, changeOrigin: false } }),
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
