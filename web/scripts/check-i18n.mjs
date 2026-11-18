// Checks the dictionaries of the web app (src/i18n/*.ts): English and Arabic have the same keys, the same kinds of
// value (text, list, sentence taking the same number of values) and lists of the same length; no text is empty;
// Arabic uses Western digits only (0-9, as everywhere on Sudoo.sh) and every Arabic text that has letters has Arabic
// letters, so a sentence left in English stands out. Counting words are checked against each language's plural forms.
// Run by `npm run check`. The dictionaries are TypeScript: they are loaded through the same compiler Vite uses
// (rolldown's), so any Node.js release runs this.

import { readdirSync, readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import { fileURLToPath } from "node:url";
import { transformSync } from "rolldown/utils";

registerHooks({
  load(url, context, nextLoad) {
    if (!url.startsWith("file:") || !url.endsWith(".ts")) return nextLoad(url, context);
    const path = fileURLToPath(url);
    const { code, errors } = transformSync(path, readFileSync(path, "utf8"));
    if (errors?.length) throw new Error(`${path}: ${errors.map((e) => e.message).join("; ")}`);
    return { format: "module", source: code, shortCircuit: true };
  },
});

const DIR = new URL("../src/i18n/", import.meta.url);
const NOT_DICTIONARIES = new Set(["index.ts", "locale.ts", "plural.ts"]);
const ARABIC = /[؀-ۿ]/;
const EASTERN_DIGITS = /[٠-٩۰-۹]/;
/** An English word written in lower case: a sentence left untranslated (names of products, formats and settings,
 *  the same in both languages on purpose, are capitalised or written as code: SMTP, Microsoft Teams, `odoo.conf`). */
const ENGLISH_WORD = /(?<![\w.@/:`-])[a-z]{3,}(?![\w.@/:`-])/;

function shape(value, path = "", out = new Map()) {
  if (Array.isArray(value)) {
    out.set(path, `list of ${value.length}`);
    value.forEach((item, index) => shape(item, `${path}[${index}]`, out));
  } else if (typeof value === "object" && value !== null) {
    for (const [key, item] of Object.entries(value)) shape(item, path ? `${path}.${key}` : key, out);
  } else {
    out.set(path, typeof value === "function" ? `sentence of ${value.length}` : typeof value);
  }
  return out;
}

function texts(value, path = "", out = []) {
  if (typeof value === "string") out.push([path, value]);
  else if (Array.isArray(value)) value.forEach((item, i) => texts(item, `${path}[${i}]`, out));
  else if (typeof value === "object" && value !== null)
    for (const [key, item] of Object.entries(value)) texts(item, path ? `${path}.${key}` : key, out);
  return out;
}

const PLURAL_KEYS = { en: ["one", "other"], ar: ["few", "many", "one", "other", "two", "zero"] };
const isPlural = (value) =>
  typeof value === "object" && value !== null && !Array.isArray(value) && "one" in value && "other" in value;

const problems = [];
let files = 0;
let keys = 0;
for (const name of readdirSync(DIR).sort()) {
  if (!name.endsWith(".ts") || NOT_DICTIONARIES.has(name)) continue;
  const module = await import(new URL(name, DIR).href);
  const dict = module.default;
  if (!dict?.en || !dict.ar) {
    problems.push(`${name}: the default export must be { en, ar }`);
    continue;
  }
  files++;
  const plurals = new Set();
  const walk = (value, path) => {
    if (isPlural(value)) plurals.add(path);
    else if (typeof value === "object" && value !== null)
      for (const [key, item] of Object.entries(value)) walk(item, path ? `${path}.${key}` : key);
  };
  walk(dict.en, "");
  const strip = (value, path = "") =>
    plurals.has(path)
      ? "plural"
      : typeof value === "object" && value !== null && !Array.isArray(value)
        ? Object.fromEntries(Object.entries(value).map(([k, v]) => [k, strip(v, path ? `${path}.${k}` : k)]))
        : value;
  const en = shape(strip(dict.en));
  const ar = shape(strip(dict.ar));
  keys += en.size;
  for (const [key, kind] of en) {
    if (!ar.has(key)) problems.push(`${name}: "${key}" is missing in Arabic`);
    else if (ar.get(key) !== kind) problems.push(`${name}: "${key}" is ${kind} in English but ${ar.get(key)} in Arabic`);
  }
  for (const key of ar.keys()) if (!en.has(key)) problems.push(`${name}: "${key}" is in Arabic only`);
  const at = (value, path) => path.split(".").reduce((o, k) => o?.[k], value);
  for (const path of plurals) {
    for (const [language, wanted] of Object.entries(PLURAL_KEYS)) {
      const forms = at(dict[language], path);
      const found = Object.keys(forms ?? {}).sort();
      if (found.join() !== wanted.join()) problems.push(`${name}: counting word "${path}" in ${language} has ${found.join(", ")}`);
    }
  }
  for (const [path, text] of texts(dict.en)) if (!text.trim()) problems.push(`${name}: "${path}" is empty in English`);
  for (const [path, text] of texts(dict.ar)) {
    if (!text.trim()) problems.push(`${name}: "${path}" is empty in Arabic`);
    if (EASTERN_DIGITS.test(text)) problems.push(`${name}: "${path}" has Eastern Arabic digits; use 0-9`);
    if (!ARABIC.test(text) && ENGLISH_WORD.test(text)) problems.push(`${name}: "${path}" looks untranslated: ${JSON.stringify(text)}`);
  }
}

if (problems.length) {
  console.error(`i18n: ${problems.length} problem(s)\n  ${problems.join("\n  ")}`);
  process.exit(1);
}
console.log(`i18n: ${files} dictionaries, ${keys} keys, English and Arabic complete`);
