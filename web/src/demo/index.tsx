// The demo build (npm run build:demo): the panel's real web app, with an in-browser backend instead of a server.
// main.tsx loads this module only when import.meta.env.MODE is "demo", so the normal build contains none of it.

import "./demo.scss";

import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowUpRight } from "lucide-react";
import { useEffect, useState } from "react";
import type { DataRouter } from "react-router";
import { maintenanceKeys } from "@/api/maintenance";
import { keys } from "@/api/queries";
import type { Role } from "@/api/types";
import { LanguageSwitch } from "@/components/LanguageSwitch";
import { useToast } from "@/components/app/overlay";
import { SelectBox } from "@/components/app/select";
import { useT } from "@/i18n";
import words from "@/i18n/demo";
import { answer, me, remember, switchRole } from "./backend/api";
import { seedDomains } from "./backend/domains";
import { seedProjects } from "./backend/projects";
import { seedTeams } from "./backend/access";
import { seedClients } from "./backend/clients";
import { seedMaintenance } from "./backend/maintenance";
import { setBackendNotifier } from "./backend/notices";
import { seedServers } from "./backend/servers";
import { seedSupport } from "./backend/support";
import { startMetrics } from "./backend/metrics";
import { startScenario } from "./backend/scenario";
import { roleInfo, seedAll, session } from "./backend/state";
import { DemoPlanMenu, markPremium } from "./plan";
import { rememberReferrer, websiteLink } from "./referrer";
import { installTransport, setNotifier } from "./transport";

/** Seeds the sample data, puts the in-browser backend in place of the network and starts the live figures. */
export function start() {
  rememberReferrer();
  try {
    const saved = JSON.parse(sessionStorage.getItem("sudoo.demo") ?? "null") as {
      role?: Role;
      signedIn?: boolean;
    } | null;
    if (saved?.role && PERSPECTIVES.some((p) => p.role === saved.role)) switchRole(saved.role);
    if (saved?.signedIn === false) session.signedIn = false;
  } catch {
    /* no sessionStorage: start signed in as an administrator */
  }
  seedAll();
  seedTeams();
  seedProjects();
  seedClients();
  seedMaintenance();
  seedDomains();
  seedServers();
  seedSupport();
  markPremium();
  installTransport();
  startMetrics();
  startScenario();
  document.documentElement.dataset.demo = "";
}

/**
 * Puts the answers the frame draws above every page (the session, the plan's banner, the maintenance banner, plugin
 * notices) and those of the dashboard the demo opens on in the query cache before the first render. Over a network they arrive one by one after the page has
 * been drawn, and each banner that appears pushes the page down; the demo's backend is in the browser, so it can
 * answer them at once. The queries still ask again as usual; only the first draw is complete from the start.
 */
export async function prime(client: QueryClient): Promise<void> {
  const get = async (path: string, query = "") => {
    const result = await answer("GET", path, new URLSearchParams(query), undefined);
    if (result.status >= 400) throw new Error(`${path}: ${result.status}`);
    return result.body as { data: unknown; meta?: unknown };
  };
  await Promise.allSettled([
    get("/session").then((body) => client.setQueryData(keys.session, body.data)),
    get("/plan").then((body) => client.setQueryData(keys.plan, body.data)),
    get("/plugins/notices").then((body) => client.setQueryData(keys.pluginNotices, body.data)),
    // the dashboard the demo opens on: its tiles, the expiring-soon card and the charts are complete in the first draw
    get("/instances", "limit=500").then((body) => client.setQueryData(keys.instances, body.data)),
    get("/deleted-instances").then((body) => client.setQueryData(["deleted-instances"], body.data)),
    get("/metrics").then((body) => client.setQueryData(keys.metrics, body.data)),
    get("/metrics/history", "minutes=60").then((body) => client.setQueryData(keys.history, body.data)),
    get("/maintenance").then((body) =>
      client.setQueryData(maintenanceKeys.staff, { items: body.data, meta: body.meta }),
    ),
  ]);
}

// "member" is shown as Tester: a member's role in the demo's projects is Tester (demo/backend/access.ts)
const PERSPECTIVES: { role: Role }[] = [
  { role: "super_admin" },
  { role: "admin" },
  { role: "maintainer" },
  { role: "developer" },
  { role: "viewer" },
  { role: "member" },
  { role: "client" },
];

/** The demo's banner: always at the top, one line. What this is, where to install it, and whose eyes to look with. */
export function DemoBanner({ router }: { router: Pick<DataRouter, "state" | "subscribe"> }) {
  const client = useQueryClient();
  const toast = useToast();
  const [role, setRole] = useState<Role>(session.role);
  const t = useT(words);
  const [path, setPath] = useState(router.state.location.pathname);
  useEffect(() => router.subscribe((state) => setPath(state.location.pathname)), [router]);
  useEffect(() => setRole(session.role), [path]); // first-run setup signs in as a super admin
  useEffect(() => {
    setNotifier((title, text) => toast("info", title, text));
    setBackendNotifier((title, text) => toast("info", title, text));
  }, [toast]);

  const choose = (next: Role) => {
    switchRole(next);
    remember();
    setRole(next);
    if (session.signedIn) {
      client.setQueryData(keys.session, me());
      void client.invalidateQueries({ predicate: (query) => query.queryKey[0] !== "session" });
    }
    toast("info", t.viewingAsRole(t.roles[next] ?? next), roleInfo(next).description);
  };

  return (
    <div className="demo-bar" role="region" aria-label={t.about}>
      <span className="demo-dot" aria-hidden="true" />
      <p className="demo-text">
        {path === "/login" ? (
          <>
            <b>{t.demo}.</b>{" "}
            <span className="demo-long">
              {t.signInAny} <a href="/setup">{t.firstRun}</a>
            </span>
          </>
        ) : path === "/setup" ? (
          <>
            <b>{t.demo}.</b> <span className="demo-long">{t.setupAny}</span>
          </>
        ) : (
          <>
            <b>{t.demo}</b> <span className="demo-long">{t.sampleData}</span>
          </>
        )}
      </p>
      <span className="demo-spacer" />
      <DemoPlanMenu />
      <label className="demo-role">
        <span className="demo-long">{t.viewingAs}</span>
        <SelectBox
          className="demo-select"
          size="sm"
          value={role}
          onValueChange={(value) => choose(value as Role)}
          aria-label={t.viewingAs}
        >
          {PERSPECTIVES.map((p) => (
            <option key={p.role} value={p.role}>
              {t.roles[p.role] ?? p.role}
            </option>
          ))}
        </SelectBox>
      </label>
      <LanguageSwitch signedIn={session.signedIn && path !== "/login"} className="demo-language" />
      <a className="demo-back" href={websiteLink()} title={t.back} aria-label={t.back}>
        <ArrowLeft aria-hidden="true" className="flip-rtl" />
        <span className="demo-long">{t.back}</span>
      </a>
      <a className="demo-install" href="https://sudoo.sh">
        <span className="demo-long">{t.install}</span>
        <span className="demo-short">{t.installShort}</span>
        <ArrowUpRight aria-hidden="true" className="flip-rtl" />
      </a>
    </div>
  );
}
