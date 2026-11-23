// The demo build (npm run build:demo): the panel's real web app, with an in-browser backend instead of a server.
// main.tsx loads this module only when import.meta.env.MODE is "demo", so the normal build contains none of it.

import "./demo.scss";

import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowUpRight } from "lucide-react";
import { useEffect, useState } from "react";
import type { DataRouter } from "react-router";
import { keys } from "@/api/queries";
import type { Role } from "@/api/types";
import { LanguageSwitch } from "@/components/LanguageSwitch";
import { useToast } from "@/components/app/overlay";
import { SelectBox } from "@/components/app/select";
import { useT } from "@/i18n";
import words from "@/i18n/demo";
import { me, remember, switchRole } from "./backend/api";
import { seedDomains } from "./backend/domains";
import { seedProjects } from "./backend/projects";
import { seedTeams } from "./backend/access";
import { seedClients } from "./backend/clients";
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
    const saved = JSON.parse(sessionStorage.getItem("sudoo.demo") ?? "null") as { role?: Role; signedIn?: boolean } | null;
    if (saved?.role && PERSPECTIVES.some((p) => p.role === saved.role)) switchRole(saved.role);
    if (saved?.signedIn === false) session.signedIn = false;
  } catch {
    /* no sessionStorage: start signed in as an administrator */
  }
  seedAll();
  seedTeams();
  seedProjects();
  seedClients();
  seedDomains();
  seedServers();
  seedSupport();
  markPremium();
  installTransport();
  startMetrics();
  startScenario();
  document.documentElement.dataset.demo = "";
}

const PERSPECTIVES: { role: Role }[] = [{ role: "super_admin" }, { role: "admin" }, { role: "developer" }, { role: "viewer" }, { role: "client" }];

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
            <b>{t.demo}.</b> <span className="demo-long">{t.signInAny} <a href="/setup">{t.firstRun}</a></span>
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
        <SelectBox className="demo-select" size="sm" value={role} onValueChange={(value) => choose(value as Role)} aria-label={t.viewingAs}>
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
