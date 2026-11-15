// After `vite build`: write a Brotli (.br) and a gzip (.gz) copy next to every text file of dist/, compressed once
// at the highest levels, so Caddy serves them as they are (file_server precompressed) instead of compressing every
// answer again. Fonts (woff2) and images are compressed already. Uses only Node's own zlib.

import { readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { brotliCompressSync, constants, gzipSync } from "node:zlib";

const DIST = new URL("../dist/", import.meta.url).pathname;
const TEXT = /\.(js|mjs|css|html|svg|json|txt|map)$/;
const MIN_BYTES = 1024; // smaller files gain nothing worth a second request path

function* files(dir) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) yield* files(path);
    else if (TEXT.test(name)) yield path;
  }
}

let raw = 0;
let brotli = 0;
for (const path of files(DIST)) {
  const data = readFileSync(path);
  if (data.length < MIN_BYTES) continue;
  const br = brotliCompressSync(data, {
    params: {
      [constants.BROTLI_PARAM_QUALITY]: constants.BROTLI_MAX_QUALITY,
      [constants.BROTLI_PARAM_SIZE_HINT]: data.length,
    },
  });
  writeFileSync(`${path}.br`, br);
  writeFileSync(`${path}.gz`, gzipSync(data, { level: 9 }));
  raw += data.length;
  brotli += br.length;
}
console.log(`precompressed: ${(raw / 1024).toFixed(0)} kB of text, ${(brotli / 1024).toFixed(0)} kB as Brotli`);
