// Pages load when first opened (one small file each), so the first visit downloads only the frame and the page
// it shows.

import type { ComponentType } from "react";
import { createBrowserRouter } from "react-router";
import { AdminOnly, RequireSession, Shell } from "./components/Shell";
import { DashboardPage } from "./pages/Dashboard";
import { LoginPage } from "./pages/Login";

function page<M>(load: () => Promise<M>, pick: (module: M) => ComponentType, adminOnly = false) {
  return async () => {
    const Page = pick(await load());
    return { Component: adminOnly ? () => <AdminOnly><Page /></AdminOnly> : Page };
  };
}

export const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  {
    element: <RequireSession />,
    children: [
      { path: "/setup-second-factor", lazy: page(() => import("./pages/Account"), (m) => m.SetupSecondFactorPage) },
      {
        element: <Shell />,
        children: [
          { path: "/", element: <DashboardPage /> },
          { path: "/i/:name/:tab?", lazy: page(() => import("./pages/Instance"), (m) => m.InstancePage) },
          { path: "/new", lazy: page(() => import("./pages/NewInstance"), (m) => m.NewInstancePage, true) },
          { path: "/settings", lazy: page(() => import("./pages/Settings"), (m) => m.SettingsPage, true) },
          { path: "/accounts", lazy: page(() => import("./pages/Accounts"), (m) => m.AccountsPage, true) },
          { path: "/tokens", lazy: page(() => import("./pages/Tokens"), (m) => m.TokensPage, true) },
          { path: "/backups", lazy: page(() => import("./pages/Backups"), (m) => m.BackupsPage, true) },
          { path: "/activity", lazy: page(() => import("./pages/Audit"), (m) => m.AuditPage, true) },
          { path: "/github", lazy: page(() => import("./pages/GitHub"), (m) => m.GitHubPage, true) },
          { path: "/account", lazy: page(() => import("./pages/Account"), (m) => m.AccountPage) },
          { path: "*", lazy: page(() => import("./pages/NotFound"), (m) => m.NotFoundPage) },
        ],
      },
    ],
  },
]);
