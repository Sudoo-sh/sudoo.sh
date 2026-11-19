// Loading a premium package's web files: each file is fetched, its SHA-256 compared with the one its signed
// manifest states (as GET /api/v1/premium lists it; the server checks the same before serving it), then imported
// as an ES module from the same address (the Content-Security-Policy allows scripts from this origin only). The
// address carries the hash, so the browser keeps the file until the package changes.

import { useQuery } from "@tanstack/react-query";
import type { ComponentType } from "react";
import { api } from "@/api/client";
import { getLocale } from "@/i18n";
import { sharedScope } from "./shared";
import type { PremiumOverview, PremiumPackage } from "./types";

export const premiumKey = ["premium"] as const;

/** The premium packages, as the server sees them; read once per visit and after a check. */
export const usePremium = () =>
  useQuery({ queryKey: premiumKey, queryFn: () => api.get<PremiumOverview>("/premium"), staleTime: 5 * 60_000 });

const url = (pkg: PremiumPackage, file: string) => `/premium/ui/${pkg.id}/${file}?v=${pkg.ui?.files[file] ?? ""}`;

async function sha256(data: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

async function checked(pkg: PremiumPackage, file: string): Promise<ArrayBuffer> {
  const expected = pkg.ui?.files[file];
  if (!expected) throw new Error(`${pkg.id}: ${file} is not listed in its manifest`);
  const response = await fetch(url(pkg, file), { credentials: "same-origin" });
  if (!response.ok) throw new Error(`${pkg.id}: ${file} answered ${response.status}`);
  const data = await response.arrayBuffer();
  if ((await sha256(data)) !== expected) throw new Error(`${pkg.id}: ${file} does not match its signed hash`);
  return data;
}

const modules = new Map<string, Promise<Record<string, unknown>>>();

/** A module of a package: checked, then imported (once). */
export function premiumModule(pkg: PremiumPackage, file: string): Promise<Record<string, unknown>> {
  const address = url(pkg, file);
  let found = modules.get(address);
  if (!found) {
    found = (async () => {
      await sharedScope();
      ensureStyles(pkg);
      await loadStrings(pkg);
      await checked(pkg, file);
      return (await import(/* @vite-ignore */ address)) as Record<string, unknown>;
    })();
    found.catch(() => modules.delete(address));
    modules.set(address, found);
  }
  return found;
}

/** A component a package exports. */
export async function premiumComponent<P>(pkg: PremiumPackage, file: string, name: string): Promise<ComponentType<P>> {
  const module = await premiumModule(pkg, file);
  const found = module[name];
  if (typeof found !== "function") throw new Error(`${pkg.id}: ${file} has no component ${name}`);
  return found as ComponentType<P>;
}

function ensureStyles(pkg: PremiumPackage) {
  for (const file of pkg.ui?.styles ?? []) {
    const href = url(pkg, file);
    if (document.querySelector(`link[data-premium="${CSS.escape(href)}"]`)) continue;
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = href;
    link.dataset.premium = href;
    document.head.append(link);
  }
}

type Strings = Record<string, Record<string, unknown>>;
const strings = new Map<string, Strings>();

async function loadStrings(pkg: PremiumPackage) {
  const file = pkg.ui?.strings;
  if (!file || strings.has(pkg.id)) return;
  try {
    const parsed = JSON.parse(new TextDecoder().decode(await checked(pkg, file))) as Strings;
    strings.set(pkg.id, parsed);
  } catch {
    strings.set(pkg.id, {});
  }
}

/** A package's words from its strings.json ({en, ar}), in the language shown (empty until the package loaded). */
export function premiumStrings(id: string): Record<string, unknown> {
  const found = strings.get(id) ?? {};
  return found[getLocale()] ?? found.en ?? {};
}

/** Load the words of every running package (for menu labels before any page of it opened). */
export async function loadAllStrings(overview: PremiumOverview) {
  await Promise.all(overview.packages.filter((p) => p.status === "active").map((p) => loadStrings(p)));
}
