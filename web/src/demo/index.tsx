// The demo build (npm run build:demo): the panel's real web app, with an in-browser backend instead of a server.
// main.tsx loads this module only when import.meta.env.MODE is "demo", so the normal build contains none of it.

import "./demo.scss";

import { useQueryClient } from "@tanstack/react-query";
import { ArrowUpRight, ChevronDown } from "lucide-react";
import { useEffect, useState } from "react";
import type { DataRouter } from "react-router";
import { keys } from "@/api/queries";
import type { Role } from "@/api/types";
import { useToast } from "@/components/ui/overlay";
import { me, remember, switchRole } from "./backend/api";
import { seedDomains } from "./backend/domains";
import { startMetrics } from "./backend/metrics";
import { startScenario } from "./backend/scenario";
import { roleInfo, seedAll, session } from "./backend/state";
import { installTransport, setNotifier } from "./transport";

/** Seeds the sample data, puts the in-browser backend in place of the network and starts the live figures. */
export function start() {
  try {
    const saved = JSON.parse(sessionStorage.getItem("sudoo.demo") ?? "null") as { role?: Role; signedIn?: boolean } | null;
    if (saved?.role && PERSPECTIVES.some((p) => p.role === saved.role)) switchRole(saved.role);
    if (saved?.signedIn === false) session.signedIn = false;
  } catch {
    /* no sessionStorage: start signed in as an administrator */
  }
  seedAll();
  seedDomains();
  installTransport();
  startMetrics();
  startScenario();
  document.documentElement.dataset.demo = "";
}

const PERSPECTIVES: { role: Role; label: string }[] = [
  { role: "super_admin", label: "Super admin" },
  { role: "admin", label: "Admin" },
  { role: "developer", label: "Developer" },
  { role: "viewer", label: "Viewer" },
];

/** The demo's banner: always at the top, one line. What this is, where to install it, and whose eyes to look with. */
export function DemoBanner({ router }: { router: Pick<DataRouter, "state" | "subscribe"> }) {
  const client = useQueryClient();
  const toast = useToast();
  const [role, setRole] = useState<Role>(session.role);
  const [path, setPath] = useState(router.state.location.pathname);
  useEffect(() => router.subscribe((state) => setPath(state.location.pathname)), [router]);
  useEffect(() => setNotifier((title, text) => toast("info", title, text)), [toast]);

  const choose = (next: Role) => {
    switchRole(next);
    remember();
    setRole(next);
    if (session.signedIn) {
      client.setQueryData(keys.session, me());
      void client.invalidateQueries({ predicate: (query) => query.queryKey[0] !== "session" });
    }
    const label = PERSPECTIVES.find((p) => p.role === next)?.label ?? next;
    toast("info", `Viewing as ${label}`, roleInfo(next).description);
  };

  return (
    <div className="demo-bar" role="region" aria-label="About this demo">
      <span className="demo-dot" aria-hidden="true" />
      <p className="demo-text">
        {path === "/login" ? (
          <>
            <b>Demo.</b> <span className="demo-long">Sign in with any e-mail address and password.</span>
          </>
        ) : (
          <>
            <b>Demo</b> <span className="demo-long">with sample data. Nothing here is real.</span>
          </>
        )}
      </p>
      <span className="demo-spacer" />
      <label className="demo-role">
        <span className="demo-long">Viewing as</span>
        <span className="demo-select">
          <select value={role} onChange={(event) => choose(event.target.value as Role)} aria-label="Viewing as">
            {PERSPECTIVES.map((p) => (
              <option key={p.role} value={p.role}>
                {p.label}
              </option>
            ))}
          </select>
          <ChevronDown aria-hidden="true" />
        </span>
      </label>
      <a className="demo-install" href="https://sudoo.sh">
        <span className="demo-long">Install on your server</span>
        <span className="demo-short">Install</span>
        <ArrowUpRight aria-hidden="true" />
      </a>
    </div>
  );
}
