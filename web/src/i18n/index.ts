/**
 * The words of the web app, in English and Arabic.
 *
 * Each area has its dictionary file in this folder (common.ts for the frame and the words every page uses, one file
 * per page or group of pages), whose default export is `{ en, ar }`, the Arabic typed as the English: a key missing
 * in either language fails the type check, and `npm run check` also runs scripts/check-i18n.ts (same keys, kinds and
 * list lengths, no empty text, Western digits only). A page's dictionary loads with the page.
 *
 * Sentences that take a value are functions; a sentence with a link or a bold word in it is a function that takes
 * that part and returns a list of parts. Messages the server writes itself (problem details, job logs, alerts) are
 * shown as the server sends them.
 */

import { getLocale, useLocale, type Locale } from "./locale";

export type Dict<T> = Record<Locale, T>;

/** The words of a dictionary in the language shown; the component is drawn again when the language changes. */
export function useT<T>(dict: Dict<T>): T {
  return dict[useLocale()];
}

/** The same outside a component (formatting helpers, toasts made in plain functions). */
export function words<T>(dict: Dict<T>): T {
  return dict[getLocale()];
}

export * from "./locale";
export { countIn, iso, ltr, type Plural } from "./plural";
