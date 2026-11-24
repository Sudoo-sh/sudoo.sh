// The signed-in session for every page that needs one: loaded before any of them, and small, so the sign-in page
// and the first paint do not wait for the panel's frame (Shell.tsx), which loads with the first signed-in page.

import { createContext, useContext, useEffect, type ReactNode } from "react";
import { Navigate, Outlet, useLocation } from "react-router";
import { setCsrfToken } from "@/api/client";
import { useSession } from "@/api/queries";
import type { Permission, Session } from "@/api/types";
import { applyBrandColor } from "@/lib/theme";
import { Spinner } from "./app/basics";

const SessionContext = createContext<Session | null>(null);
export const useMe = () => useContext(SessionContext)!;
/** Whether the signed-in role includes a permission (the backend checks it again on every request). */
export const useCan = () => {
  const me = useMe();
  return (permission: Permission) => me.permissions.includes(permission);
};

const RESTRICTED_PAGE = { password: "/change-password", second_factor: "/setup-second-factor" } as const;

/** Pages that need a signed-in session; sends everyone else to the sign-in page. */
export function RequireSession() {
  const { data: session, isPending } = useSession();
  const location = useLocation();
  useEffect(() => applyBrandColor(session?.brand_color), [session?.brand_color]);
  if (isPending)
    return (
      <div style={{ display: "grid", placeItems: "center", height: "100vh" }}>
        <Spinner />
      </div>
    );
  if (!session)
    return <Navigate to={`/login?next=${encodeURIComponent(location.pathname + location.search)}`} replace />;
  setCsrfToken(session.csrf_token);
  const task = session.restricted ? RESTRICTED_PAGE[session.restricted] : null;
  if (task && location.pathname !== task) return <Navigate to={task} replace />;
  // a super admin accepts the licence agreement before anything else (the backend refuses the rest meanwhile)
  if (!task && session.licence_required && location.pathname !== "/licence") return <Navigate to="/licence" replace />;
  return (
    <SessionContext.Provider value={session}>
      <Outlet />
    </SessionContext.Provider>
  );
}

export function RequirePermission({ permission, children }: { permission: Permission; children: ReactNode }) {
  return useCan()(permission) ? <>{children}</> : <Navigate to="/" replace />;
}
