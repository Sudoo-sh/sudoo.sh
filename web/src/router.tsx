// Pages load when first opened (one small file each), so the first visit downloads only what the page it shows
// needs: the sign-in page neither the panel's frame (Shell) nor the charts (Chart.js, with the dashboard). Each
// page names the permission it needs; the backend checks it again on every request.

import type { ComponentType } from "react";
import { createBrowserRouter } from "react-router";
import type { Permission } from "./api/types";
import { RequirePermission, RequireSession } from "./components/Session";
import { Spinner } from "./components/ui/basics";
import { LoginPage } from "./pages/Login";

function page<M>(load: () => Promise<M>, pick: (module: M) => ComponentType, permission?: Permission) {
  return async () => {
    const Page = pick(await load());
    return {
      Component: permission
        ? () => (
            <RequirePermission permission={permission}>
              <Page />
            </RequirePermission>
          )
        : Page,
    };
  };
}

// shown while the first page's code loads, only when that takes longer than 150 ms (the spinner's own delay)
const loading = (
  <div style={{ display: "grid", placeItems: "center", height: "100vh" }}>
    <Spinner />
  </div>
);

export const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  { path: "/invite", lazy: page(() => import("./pages/Invite"), (m) => m.InvitePage), hydrateFallbackElement: loading },
  { path: "/setup", lazy: page(() => import("./pages/Setup"), (m) => m.SetupPage), hydrateFallbackElement: loading },
  {
    element: <RequireSession />,
    hydrateFallbackElement: loading,
    children: [
      { path: "/change-password", lazy: page(() => import("./pages/Setup"), (m) => m.ChangePasswordPage) },
      { path: "/setup-second-factor", lazy: page(() => import("./pages/Account"), (m) => m.SetupSecondFactorPage) },
      { path: "/licence", lazy: page(() => import("./pages/Licence"), (m) => m.LicencePage) },
      {
        lazy: async () => ({ Component: (await import("./components/Shell")).Shell }),
        children: [
          { path: "/", lazy: page(() => import("./pages/Dashboard"), (m) => m.DashboardPage) },
          { path: "/i/:name/:tab?", lazy: page(() => import("./pages/Instance"), (m) => m.InstancePage) },
          { path: "/new", lazy: page(() => import("./pages/NewInstance"), (m) => m.NewInstancePage, "instances.create") },
          { path: "/settings", lazy: page(() => import("./pages/Settings"), (m) => m.SettingsPage, "settings.manage") },
          { path: "/email-templates", lazy: page(() => import("./pages/EmailTemplates"), (m) => m.EmailTemplatesPage, "mail.manage") },
          { path: "/plugins/:tab?", lazy: page(() => import("./pages/Plugins"), (m) => m.PluginsPage, "plugins.view") },
          { path: "/accounts", lazy: page(() => import("./pages/Accounts"), (m) => m.AccountsPage, "accounts.manage") },
          { path: "/tokens", lazy: page(() => import("./pages/Tokens"), (m) => m.TokensPage, "tokens.manage") },
          { path: "/backups", lazy: page(() => import("./pages/Backups"), (m) => m.BackupsPage, "backups.manage") },
          { path: "/activity", lazy: page(() => import("./pages/Audit"), (m) => m.AuditPage, "audit.view") },
          { path: "/github/app/:step", lazy: page(() => import("./pages/GitHub"), (m) => m.GitHubReturnPage, "settings.manage") },
          { path: "/github", lazy: page(() => import("./pages/GitHub"), (m) => m.GitHubPage, "github.manage") },
          { path: "/account", lazy: page(() => import("./pages/Account"), (m) => m.AccountPage) },
          { path: "/help/*", lazy: page(() => import("./pages/Help"), (m) => m.HelpPage) },
          { path: "*", lazy: page(() => import("./pages/NotFound"), (m) => m.NotFoundPage) },
        ],
      },
    ],
  },
]);
