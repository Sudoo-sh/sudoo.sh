// Where the demo's "Back to website" link goes: the sudoo.sh page the visitor came from (document.referrer when the
// demo first opened in this tab, kept in sessionStorage so it survives moving around and reloading), else the
// website's home page.

const KEY = "sudoo.demo.from";
const HOME = "https://sudoo.sh";
let remembered: string | null = null;

/** A page of the Sudoo.sh websites (sudoo.sh and its sub-domains), never the demo itself or its sample instances. */
export function isWebsitePage(address: string): boolean {
  try {
    const url = new URL(address);
    const host = url.hostname;
    const ours = host === "sudoo.sh" || host.endsWith(".sudoo.sh");
    const demo = host === "demo.sudoo.sh" || host.endsWith(".demo.sudoo.sh") || url.origin === location.origin;
    return url.protocol === "https:" && ours && !demo;
  } catch {
    return false;
  }
}

/** Reads document.referrer once per tab (call when the demo starts). */
export function rememberReferrer() {
  const from = isWebsitePage(document.referrer) ? document.referrer : "";
  try {
    const saved = sessionStorage.getItem(KEY);
    if (saved !== null) {
      remembered = saved;
      return;
    }
    sessionStorage.setItem(KEY, from);
  } catch {
    /* no sessionStorage: remember it for this page only */
  }
  remembered = from;
}

/** The address of "Back to website". */
export const websiteLink = () => (remembered && isWebsitePage(remembered) ? remembered : HOME);
