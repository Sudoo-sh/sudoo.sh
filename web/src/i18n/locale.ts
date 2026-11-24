/**
 * The languages of the web app, which one is shown, and how it is chosen.
 *
 * Signed in, the account's own choice decides, kept on the server (GET /session `language`; the Account page and the
 * user menu change it), with the server's DEFAULT_LANGUAGE for accounts that have not chosen. Before signing in
 * (sign-in and setup pages), the language last used in this browser, then a `sudoo_lang` cookie (the other Sudoo.sh
 * sites set one on .sudoo.sh; a panel on its own domain normally has none), then the browser's languages, then the
 * server's default. The language in use is remembered in this browser (`sudoo.lang`), so public/boot.js can set
 * <html lang dir> before the first paint, without an inline script (the CSP allows none).
 */

import { useSyncExternalStore } from "react";

export const LOCALES = {
  en: { name: "English", dir: "ltr" },
  ar: { name: "العربية", dir: "rtl" },
} as const;

export type Locale = keyof typeof LOCALES;
export const LOCALE_CODES = Object.keys(LOCALES) as Locale[];
export const DEFAULT_LOCALE: Locale = "en";

const STORAGE_KEY = "sudoo.lang";
export const LANG_COOKIE = "sudoo_lang";

export function isLocale(value: unknown): value is Locale {
  return typeof value === "string" && Object.hasOwn(LOCALES, value);
}

export const dirOf = (locale: Locale) => LOCALES[locale].dir;

/** The Intl language tag of a locale: Arabic with Western digits, as on the other Sudoo.sh sites. */
export const intlTag = (locale: Locale) => (locale === "ar" ? "ar-u-nu-latn" : "en");

/** The language last used in this browser, if any. */
export function storedLocale(): Locale | null {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return isLocale(value) ? value : null;
  } catch {
    return null;
  }
}

function cookieLocale(): Locale | null {
  for (const part of document.cookie.split(";")) {
    const [name, ...value] = part.trim().split("=");
    if (name === LANG_COOKIE) {
      const found = value.join("=");
      return isLocale(found) ? found : null;
    }
  }
  return null;
}

/** The first of the browser's languages this app has, compared by their primary subtag ("ar-EG" is Arabic). */
export function browserLocale(): Locale | null {
  const list = navigator.languages?.length ? navigator.languages : [navigator.language];
  for (const tag of list) {
    const primary = String(tag ?? "")
      .toLowerCase()
      .split("-")[0];
    if (isLocale(primary)) return primary;
  }
  return null;
}

/** Before signing in: this browser's last language, the cookie, the browser's languages; null when none of them
 *  names a language of the app (the server's default then decides). */
export const signedOutChoice = (): Locale | null => storedLocale() ?? cookieLocale() ?? browserLocale();

let current: Locale = signedOutChoice() ?? DEFAULT_LOCALE;
const listeners = new Set<() => void>();

export const getLocale = () => current;

/** Show the app in a language: <html lang dir>, remembered in this browser, every page drawn again. */
export function setLocale(locale: Locale, remember = true) {
  if (remember) {
    try {
      localStorage.setItem(STORAGE_KEY, locale);
    } catch {
      /* private window: chosen again after a reload */
    }
  }
  applyToDocument(locale);
  if (locale === current) return;
  current = locale;
  listeners.forEach((listener) => listener());
}

function applyToDocument(locale: Locale) {
  const root = document.documentElement;
  if (root.lang !== locale) root.lang = locale;
  if (root.dir !== dirOf(locale)) root.dir = dirOf(locale);
}
applyToDocument(current);

const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => listeners.delete(listener);
};

/** The language shown; a component that uses it is drawn again when it changes. */
export const useLocale = () => useSyncExternalStore(subscribe, getLocale);
