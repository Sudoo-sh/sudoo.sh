// "@sudoo/host": what a premium page imports from the panel in one place. Everything here is the panel's own code
// (the same objects the panel uses); premium builds may also import a listed panel module by its "@/<path>" name
// (shared.ts).

import { useLocale } from "@/i18n";
import { premiumStrings } from "./loader";

export { api, ApiError, errorText } from "@/api/client";
export { keys, useCall } from "@/api/queries";
export { useCan, useMe } from "@/components/Session";
export { Page, PageHead, useSubCrumb } from "@/components/Shell";
export {
  AnimatedNumber,
  Button,
  Callout,
  CopyField,
  Empty,
  Field,
  Progress,
  Segmented,
  Skeleton,
  Spinner,
  Status,
  Switch,
  Tag,
} from "@/components/ui/basics";
export { Drawer, Menu, Modal, useToast } from "@/components/ui/overlay";
export { JobDrawer } from "@/components/JobDrawer";
export { countIn, getLocale, iso, ltr, useLocale, useT, words } from "@/i18n";
export { ago, bytes, count, dateTime } from "@/lib/format";
export { premiumKey, usePremium } from "./loader";
export { PlanCard } from "./components";

/** The version of this module's contract: it only grows. */
export const HOST_VERSION = 1;

/** A premium package's words from its strings.json, in the language shown (drawn again when it changes). */
export function usePremiumWords<T = Record<string, unknown>>(id: string): T {
  useLocale();
  return premiumStrings(id) as T;
}
