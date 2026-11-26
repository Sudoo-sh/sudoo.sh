// The panel asks its questions with its own dialogs (components/app/confirm.tsx, the Modal), never the browser's
// confirm(), alert() or prompt(): those ignore the theme and the language, cannot be styled or tested, and a page
// can be blocked from showing them. This fails when a file in src calls one, as window.confirm(...), globalThis.alert(...)
// or a bare prompt(...) that the file does not declare itself (a local function named confirm is fine). Run with `npm test`.

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, test } from "vitest";

const NAMES = ["confirm", "alert", "prompt"];

/** The code of a file without its comments and the text of its strings, so a word in a message is not a call. */
function code(source: string): string {
  return source.replace(
    /\/\*[\s\S]*?\*\/|\/\/[^\n]*|"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`/g,
    (match) => match.replace(/[^\n]/g, " "),
  );
}

/** The native dialog calls in one source file, as "line: name". A bare confirm(...) counts unless the file declares
 *  that name itself (const [confirm, dialog] = useConfirm(), function confirm(...)). */
export function nativeDialogCalls(source: string): string[] {
  const text = code(source);
  const found: string[] = [];
  for (const name of NAMES) {
    const declared = new RegExp(`\\b(?:const|let|var|function)\\b[^=;(]*\\b${name}\\b`).test(text);
    const pattern = new RegExp(`(?:\\b(?:window|globalThis|self)\\s*\\.\\s*|(?<![.\\w$]))${name}\\s*\\(`, "g");
    for (const match of text.matchAll(pattern)) {
      const qualified = /\./.test(match[0]);
      if (!qualified && declared) continue;
      found.push(`${text.slice(0, match.index).split("\n").length}: ${name}`);
    }
  }
  return found;
}

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.tsx?$/.test(entry) && !/\.d\.ts$/.test(entry) ? [path] : [];
  });
}

describe("native dialogs", () => {
  test("the check sees window.confirm, globalThis.alert and a bare prompt", () => {
    expect(nativeDialogCalls("if (window.confirm('x')) go();")).toHaveLength(1);
    expect(nativeDialogCalls("globalThis.alert('x');")).toHaveLength(1);
    expect(nativeDialogCalls("const n = prompt('name');")).toHaveLength(1);
    expect(nativeDialogCalls("export const f = () => confirm('x') && go();")).toHaveLength(1);
  });

  test("a name the file declares itself is not the browser's", () => {
    expect(nativeDialogCalls("function confirm(x: number) { return x; }\nconfirm(1);")).toEqual([]);
    expect(nativeDialogCalls("const [confirm, dialog] = useConfirm();\nawait confirm('x');")).toEqual([]);
    expect(nativeDialogCalls("api.confirm(1); clientCalls.alert(2);")).toEqual([]);
    expect(nativeDialogCalls("// confirm(1)\nconst t = 'prompt(x)';")).toEqual([]);
  });

  test("no file in src uses the browser's confirm, alert or prompt", () => {
    const root = join(__dirname);
    const bad = sources(root).flatMap((path) =>
      nativeDialogCalls(readFileSync(path, "utf8")).map((hit) => `${path.slice(root.length + 1)}:${hit}`),
    );
    expect(bad).toEqual([]);
  });
});
