// The frame of every signed-in page: the panel's (Shell: sidebar, top bar, live updates) for the panel owner's people,
// the client portal's own small frame for client accounts (pages/ClientPortal.tsx); the portal's code loads only
// for clients. What a client may call is decided by the server (security.CLIENT_ENDPOINTS), never by this choice.

import { lazy, Suspense } from "react";
import { frameFor } from "@/lib/frame";
import { Spinner } from "./app/basics";
import { useMe } from "./Session";

const Shell = lazy(() => import("./Shell").then((m) => ({ default: m.Shell })));
const ClientPortal = lazy(() => import("@/pages/ClientPortal").then((m) => ({ default: m.ClientPortal })));

export function Frame() {
  const me = useMe();
  return (
    <Suspense fallback={<div style={{ display: "grid", placeItems: "center", height: "100vh" }}><Spinner /></div>}>
      {frameFor(me.role) === "portal" ? <ClientPortal /> : <Shell />}
    </Suspense>
  );
}
