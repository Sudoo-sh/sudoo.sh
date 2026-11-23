// The terminal of the web shell: xterm.js (MIT), loaded only when a shell or a recording is opened (a chunk of its
// own, about 90 KB compressed), from the panel itself (no CDN).
//
// Colours and font come from the design tokens (--shell-*, --mono in styles/_tokens.scss): a dark terminal in both
// themes, with a plum cursor and selection. The page's Content-Security-Policy allows no inline styles, and xterm.js writes a few <style> elements
// (its sizes and colours); the browser ignores them, so their text is copied into constructed stylesheets
// (document.adoptedStyleSheets), which the policy allows, and kept in step with them.

import "@xterm/xterm/css/xterm.css";
import { FitAddon } from "@xterm/addon-fit";
import { Terminal, type ITheme } from "@xterm/xterm";

const COLOURS = [
  "black", "red", "green", "yellow", "blue", "magenta", "cyan", "white",
  "bright-black", "bright-red", "bright-green", "bright-yellow", "bright-blue", "bright-magenta", "bright-cyan", "bright-white",
] as const;
const camel = (name: string) => name.replace(/-(\w)/g, (_, c: string) => c.toUpperCase());

function theme(): ITheme {
  const css = getComputedStyle(document.documentElement);
  const value = (name: string) => css.getPropertyValue(`--shell-${name}`).trim();
  const found: ITheme = {
    background: value("bg"),
    foreground: value("text"),
    cursor: value("cursor"),
    cursorAccent: value("bg"),
    selectionBackground: value("selection"),
  };
  for (const name of COLOURS) (found as Record<string, string>)[camel(name)] = value(name);
  return found;
}

const mono = () => getComputedStyle(document.documentElement).getPropertyValue("--mono").trim() || "monospace";

/** Copies the text of xterm.js's own <style> elements under `host` into constructed stylesheets (see above). */
function adoptStyles(host: HTMLElement): () => void {
  if (typeof CSSStyleSheet === "undefined" || !("adoptedStyleSheets" in document)) return () => undefined;
  const sheets = new Map<HTMLStyleElement, CSSStyleSheet>();
  const sync = () => {
    for (const element of host.querySelectorAll("style")) {
      let sheet = sheets.get(element);
      if (!sheet) {
        sheet = new CSSStyleSheet();
        sheets.set(element, sheet);
        document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
      }
      sheet.replaceSync(element.textContent ?? "");
    }
    for (const [element, sheet] of sheets) {
      if (host.contains(element)) continue;
      sheets.delete(element);
      document.adoptedStyleSheets = document.adoptedStyleSheets.filter((s) => s !== sheet);
    }
  };
  const observer = new MutationObserver(sync);
  observer.observe(host, { subtree: true, childList: true, characterData: true });
  sync();
  return () => {
    observer.disconnect();
    const ours = new Set(sheets.values());
    document.adoptedStyleSheets = document.adoptedStyleSheets.filter((s) => !ours.has(s));
  };
}

export interface ShellScreen {
  term: Terminal;
  /** fit the terminal to its box (rows and columns) */
  fit: () => void;
  dispose: () => void;
}

/** A terminal drawn in `host`, sized to it, in the panel's colours and font. */
export function openTerminal(host: HTMLElement, { readOnly = false }: { readOnly?: boolean } = {}): ShellScreen {
  const term = new Terminal({
    fontFamily: mono(),
    fontSize: 13,
    lineHeight: 1.2,
    cursorBlink: !readOnly,
    cursorStyle: readOnly ? "underline" : "block",
    disableStdin: readOnly,
    scrollback: 5000,
    allowProposedApi: false,
    theme: theme(),
    macOptionIsMeta: true,
    // box and block characters (the welcome's banner, tables) drawn by xterm.js itself, whole whatever the font
    customGlyphs: true,
    rightClickSelectsWord: true,
  });
  const fitter = new FitAddon();
  term.loadAddon(fitter);
  const unadopt = adoptStyles(host);
  term.open(host);
  const fit = () => {
    try {
      if (host.clientWidth && host.clientHeight) fitter.fit();
    } catch {
      /* not laid out yet */
    }
  };
  fit();

  // the theme and the font follow the page: light or dark, and the monospace font once it has loaded
  const retheme = () => {
    term.options.theme = theme();
  };
  const themeObserver = new MutationObserver(retheme);
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "class"] });
  const scheme = matchMedia("(prefers-color-scheme: dark)");
  scheme.addEventListener("change", retheme);
  void document.fonts?.ready.then(() => {
    term.options.fontFamily = mono();
    fit();
  });
  const resize = new ResizeObserver(() => fit());
  resize.observe(host);

  return {
    term,
    fit,
    dispose: () => {
      resize.disconnect();
      themeObserver.disconnect();
      scheme.removeEventListener("change", retheme);
      term.dispose();
      unadopt();
    },
  };
}
