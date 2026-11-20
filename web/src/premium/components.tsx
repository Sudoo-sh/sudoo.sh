// Where premium features appear in the panel: their pages (routes), their sections in the panel's own pages
// (slots), and a plan card while a feature is absent. Settings › Plan shows every premium feature with its state.

import { ArrowUpRight, RefreshCcw, Sparkles } from "lucide-react";
import { Component, useEffect, useState, type ComponentType, type ErrorInfo, type ReactNode } from "react";
import { Link, useLocation } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { api, errorText } from "@/api/client";
import { useSudooPlan } from "@/api/queries";
import type { Job, Permission } from "@/api/types";
import { JobDrawer } from "@/components/JobDrawer";
import { useCan } from "@/components/Session";
import { Page } from "@/components/Shell";
import { Button, Callout, Skeleton, Spinner, Tag } from "@/components/app/basics";
import { useToast } from "@/components/app/overlay";
import { useT } from "@/i18n";
import words from "@/i18n/premium";
import { ago, dateTime } from "@/lib/format";
import { NotFoundPage } from "@/pages/NotFound";
import { premiumComponent, premiumKey, usePremium } from "./loader";
import type { PremiumId, PremiumPackage, PremiumRoute, PremiumSection, PremiumStatus } from "./types";

/** Where a premium component is drawn: a page (its path) or a section of a panel page (its slot). */
export interface PremiumPlace {
  pkg: PremiumPackage;
  path?: string;
  slot?: string;
}

/** What draws around each premium component: nothing in the panel; a build may mark them (setPremiumFrame). */
let frame = (node: ReactNode, _place: PremiumPlace): ReactNode => node;

/** Draw something around every premium page and section from now on (the demo marks them with their plans). */
export function setPremiumFrame(next: (node: ReactNode, place: PremiumPlace) => ReactNode) {
  frame = next;
}

/** Draw a premium component once its file is loaded and checked. */
function Loaded<P extends object>({ pkg, module, name, props, onError, place }: {
  pkg: PremiumPackage; module: string; name: string; props: P; onError?: ReactNode; place: Omit<PremiumPlace, "pkg">;
}) {
  const t = useT(words);
  const [found, setFound] = useState<{ C: ComponentType<P> } | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    premiumComponent<P>(pkg, module, name).then(
      (C) => live && setFound({ C }),
      () => live && setFailed(true),
    );
    return () => {
      live = false;
    };
  }, [pkg, module, name]);
  if (failed) return <>{onError ?? <Callout tone="warning">{t.loadFailed}</Callout>}</>;
  if (!found) return <Skeleton height={48} />;
  return (
    <Guard fallback={onError ?? <Callout tone="warning">{t.loadFailed}</Callout>}>
      {frame(<found.C {...props} />, { pkg, ...place })}
    </Guard>
  );
}

/** A premium component that throws never takes the panel's page down with it. */
class Guard extends Component<{ fallback: ReactNode; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(error: unknown, info: ErrorInfo) {
    console.error("premium feature failed", error, info.componentStack);
  }
  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

function allowed(can: (p: Permission) => boolean, permission: string | null) {
  return !permission || can(permission as Permission);
}

/** The sections running packages add to `name`, in package order. */
export function usePremiumSections(name: string): { pkg: PremiumPackage; section: PremiumSection }[] {
  const { data } = usePremium();
  const can = useCan();
  return (data?.packages ?? []).flatMap((pkg) =>
    (pkg.ui?.sections ?? []).filter((s) => s.slot === name && allowed(can, s.permission)).map((section) => ({ pkg, section })),
  );
}

/** Where premium packages add a section to a panel page; `fallback` shows while none does. */
export function PremiumSlot<P extends object>({ name, props, fallback = null }: { name: string; props?: P; fallback?: ReactNode }) {
  const { isPending } = usePremium();
  const found = usePremiumSections(name);
  if (isPending) return null;
  if (found.length === 0) return <>{fallback}</>;
  return (
    <>
      {found.map(({ pkg, section }) => (
        <Loaded key={`${pkg.id}:${section.module}:${section.export}`} pkg={pkg} module={section.module} name={section.export}
          props={(props ?? {}) as P} onError={fallback} place={{ slot: name }} />
      ))}
    </>
  );
}

/** The routes of the running packages the signed-in role may open. */
export function usePremiumRoutes(): { pkg: PremiumPackage; route: PremiumRoute }[] {
  const { data } = usePremium();
  const can = useCan();
  return (data?.packages ?? []).flatMap((pkg) =>
    (pkg.ui?.routes ?? []).filter((r) => allowed(can, r.permission)).map((route) => ({ pkg, route })),
  );
}

function matches(path: string, pathname: string) {
  return pathname === path || pathname.startsWith(`${path}/`);
}

/** A page a premium package provides at `path`, or its plan card while the package is absent. */
export function PremiumPage({ id, path }: { id: PremiumId; path: string }) {
  const { isPending } = usePremium();
  const routes = usePremiumRoutes();
  if (isPending) return <Page><Skeleton height={160} /></Page>;
  const found = routes.find(({ route }) => route.path === path);
  if (found) return <Loaded pkg={found.pkg} module={found.route.module} name={found.route.export} props={{}} place={{ path }} />;
  return <Page><PlanCard id={id} /></Page>;
}

/** Any other page a running package lists; the "not found" page otherwise. */
export function PremiumOrNotFound() {
  const { pathname } = useLocation();
  const { isPending } = usePremium();
  const routes = usePremiumRoutes();
  if (isPending) return <Page><Skeleton height={160} /></Page>;
  const found = routes.filter(({ route }) => matches(route.path, pathname)).sort((a, b) => b.route.path.length - a.route.path.length)[0];
  if (!found) return <NotFoundPage />;
  return <Loaded pkg={found.pkg} module={found.route.module} name={found.route.export} props={{}} place={{ path: found.route.path }} />;
}

/** What a premium feature is and how it comes: shown where it would be while it is absent. */
export function PlanCard({ id, compact }: { id: PremiumId; compact?: boolean }) {
  const t = useT(words);
  const { data } = usePremium();
  const { data: plan } = useSudooPlan();
  const pkg = data?.packages.find((p) => p.id === id);
  const name = t.names[id];
  const state = pkg?.status;
  return (
    <section className={compact ? "callout" : "card"} aria-label={name}>
      <div className={compact ? undefined : "card-body"}>
        <div className="premium-card">
          <Sparkles aria-hidden />
          <div className="text">
            <b>{t.planCardTitle(name)}</b>
            <span className="small muted">{t.about[id]}</span>
            <span className="small">
              {state === "failed" ? <>{t.planCardFailed} {pkg?.problem}</>
                : state === "switched_off" ? t.planCardOff
                : state === "pending" || state === "restart_pending" ? t.planCardPending
                : <>{pkg ? `${t.plans(pkg.plans)}. ` : ""}{t.planCardText}</>}
            </span>
            <span className="links small">
              {plan?.links.pricing && (
                <a href={plan.links.pricing} target="_blank" rel="noreferrer">{t.seePlans} <ArrowUpRight size={12} className="flip-rtl" /></a>
              )}
              <Link to="/settings?tab=plan" viewTransition>{t.openSettings}</Link>
            </span>
          </div>
        </div>
      </div>
    </section>
  );
}

const TONE: Record<PremiumStatus, "positive" | "caution" | "negative" | "accent" | undefined> = {
  active: "positive",
  pending: "accent",
  restart_pending: "accent",
  failed: "negative",
  switched_off: "caution",
  not_included: undefined,
};

/** Settings › Plan: every premium feature, its state, and Check now. */
export function PremiumFeaturesCard() {
  const t = useT(words);
  const { data, isPending } = usePremium();
  const can = useCan();
  const toast = useToast();
  const client = useQueryClient();
  const [job, setJob] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const running = Boolean(data?.job);
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => client.invalidateQueries({ queryKey: premiumKey }), 3000);
    return () => clearInterval(timer);
  }, [running, client]);
  const check = async () => {
    setBusy(true);
    try {
      const started = await api.post<Job>("/premium/sync");
      setJob(started.id);
      toast("success", t.checkStarted);
      await client.invalidateQueries({ queryKey: premiumKey });
    } catch (error) {
      toast("error", t.checkNow, errorText(error));
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="card">
      <div className="card-head">
        <h2>{t.title}</h2>
        {can("settings.manage") && (
          <Button size="sm" icon={running ? <Spinner /> : <RefreshCcw />} loading={busy} disabled={running} onClick={check}>
            {running ? t.checking : t.checkNow}
          </Button>
        )}
      </div>
      <div className="card-body stack">
        <p className="small muted">{t.text}</p>
        {data && !data.marketplace && <Callout tone="info">{t.marketplaceOff}</Callout>}
        {data?.last_error && <Callout tone="warning">{t.lastError} {data.last_error}</Callout>}
        {isPending ? <Skeleton height={120} /> : (
          <ul className="premium-list" role="list">
            {data?.packages.map((pkg) => (
              <li key={pkg.id}>
                <div className="about">
                  <b>{t.names[pkg.id]}</b>
                  <span className="xsmall muted">{t.about[pkg.id]}</span>
                  {pkg.problem && pkg.status !== "not_included" && <span className="xsmall problem">{pkg.problem}</span>}
                  {pkg.binding_until && pkg.status === "active" && <span className="xsmall faint">{t.boundUntil(dateTime(pkg.binding_until))}</span>}
                </div>
                <div className="tags">
                  {pkg.version && pkg.status !== "not_included" && <Tag mono>{t.version(pkg.version)}</Tag>}
                  <Tag tone={TONE[pkg.status]}>{t.status[pkg.status]}</Tag>
                </div>
              </li>
            ))}
          </ul>
        )}
        <p className="xsmall faint">
          {data?.auto_install ? t.autoOn : t.autoOff} {data?.last_checked_at ? t.lastChecked(ago(data.last_checked_at)) : t.notChecked}
        </p>
      </div>
      {job && <JobDrawer id={job} onClose={() => setJob(null)} />}
    </section>
  );
}
