// The shared scope premium features load against (window.__SUDOO__). A premium package's web files are ES modules
// built in Sudoo.sh's private repository with the libraries and the panel's own modules left out: each import of
// "react", "react-router", "@tanstack/react-query", ... or of a panel module ("@/components/app/basics", ...) reads
// window.__SUDOO__.modules[<name>] instead, so a premium page uses the very same React, router, query cache, design
// system and words as the panel. Only the modules listed here are shared; they are loaded once, before the first
// premium file (the frame's modules load with the first signed-in page anyway).

import * as React from "react";
import * as ReactDOM from "react-dom";
import * as JsxRuntime from "react/jsx-runtime";
import * as ReactRouter from "react-router";
import * as ReactQuery from "@tanstack/react-query";

/** The version of this shared scope: a premium package states the one it was built for (it only grows). */
export const SHARED_VERSION = 1;

type Loader = () => Promise<Record<string, unknown>>;

/** Panel modules premium code may import, by their path under src/ (what "@/<path>" names). */
const PANEL_MODULES: Record<string, Loader> = {
  "api/client": () => import("@/api/client"),
  "api/queries": () => import("@/api/queries"),
  "api/types": () => import("@/api/types"),
  "components/Session": () => import("@/components/Session"),
  "components/Shell": () => import("@/components/Shell"),
  "components/ui/basics": () => import("@/components/app/basics"),
  "components/ui/overlay": () => import("@/components/app/overlay"),
  "components/JobDrawer": () => import("@/components/JobDrawer"),
  "components/LockTag": () => import("@/components/LockTag"),
  "components/ProjectAccess": () => import("@/components/ProjectAccess"),
  "components/PageMotion": () => import("@/components/PageMotion"),
  i18n: () => import("@/i18n"),
  "i18n/common": () => import("@/i18n/common"),
  "i18n/teams": () => import("@/i18n/teams"),
  "i18n/settings": () => import("@/i18n/settings"),
  "i18n/operations": () => import("@/i18n/operations"),
  "i18n/plural": () => import("@/i18n/plural"),
  "lib/format": () => import("@/lib/format"),
  "premium/host": () => import("./host"),
};

export interface SharedScope {
  version: number;
  modules: Record<string, Record<string, unknown>>;
}

declare global {
  interface Window {
    __SUDOO__?: SharedScope;
  }
}

let ready: Promise<SharedScope> | null = null;

/** The shared scope, with every module in it loaded (once). */
export function sharedScope(): Promise<SharedScope> {
  ready ??= (async () => {
    const modules: SharedScope["modules"] = {
      react: React as unknown as Record<string, unknown>,
      "react/jsx-runtime": JsxRuntime as unknown as Record<string, unknown>,
      "react-dom": ReactDOM as unknown as Record<string, unknown>,
      "react-router": ReactRouter as unknown as Record<string, unknown>,
      "@tanstack/react-query": ReactQuery as unknown as Record<string, unknown>,
    };
    const loaded = await Promise.all(Object.entries(PANEL_MODULES).map(async ([name, load]) => [name, await load()] as const));
    for (const [name, module] of loaded) modules[`@/${name}`] = module;
    modules["@sudoo/host"] = modules["@/premium/host"]!;
    const scope = { version: SHARED_VERSION, modules };
    window.__SUDOO__ = scope;
    return scope;
  })();
  return ready;
}

/** The names a premium build may leave out (the private build's externals). */
export const SHARED_NAMES = ["react", "react/jsx-runtime", "react-dom", "react-router", "@tanstack/react-query", "@sudoo/host",
  ...Object.keys(PANEL_MODULES).map((name) => `@/${name}`)];
