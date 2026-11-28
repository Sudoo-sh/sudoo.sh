// Pages load when first opened (one small file each), so the first visit downloads only what the page it shows
// needs: the sign-in page neither the panel's frame (Shell) nor the charts (Chart.js, with the dashboard). Each
// page names the permission it needs; the backend checks it again on every request.

import type { ComponentType } from "react";
import { createBrowserRouter, type RouteObject } from "react-router";
import type { Permission } from "./api/types";
import { RequirePermission, RequireSession } from "./components/Session";
import { Spinner } from "./components/app/basics";
import { RouteError } from "./components/Problems";
import { GitHubReturnPage, LoginPage } from "./pages/Login";

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

// every page that fails to load or draw shows the error screen (components/Problems.tsx), never a blank page
const routes: RouteObject[] = [
  { path: "/login", element: <LoginPage /> },
  // where GitHub sends the browser back after "Continue with GitHub" or linking a GitHub account (auth/github.py)
  { path: "/login/github", element: <GitHubReturnPage /> },
  {
    path: "/request-access",
    lazy: page(
      () => import("./pages/RequestAccess"),
      (m) => m.RequestAccessPage,
    ),
    hydrateFallbackElement: loading,
  },
  {
    path: "/invite",
    lazy: page(
      () => import("./pages/Invite"),
      (m) => m.InvitePage,
    ),
    hydrateFallbackElement: loading,
  },
  // a project's public status page: read without signing in (status/pages.py)
  {
    path: "/status/:token",
    lazy: page(
      () => import("./pages/PublicStatus"),
      (m) => m.PublicStatusPage,
    ),
    hydrateFallbackElement: loading,
  },
  {
    path: "/setup",
    lazy: page(
      () => import("./pages/Setup"),
      (m) => m.SetupPage,
    ),
    hydrateFallbackElement: loading,
  },
  {
    element: <RequireSession />,
    hydrateFallbackElement: loading,
    children: [
      {
        path: "/change-password",
        lazy: page(
          () => import("./pages/Setup"),
          (m) => m.ChangePasswordPage,
        ),
      },
      {
        path: "/setup-second-factor",
        lazy: page(
          () => import("./pages/Account"),
          (m) => m.SetupSecondFactorPage,
        ),
      },
      {
        path: "/licence",
        lazy: page(
          () => import("./pages/Licence"),
          (m) => m.LicencePage,
        ),
      },
      {
        lazy: async () => ({ Component: (await import("./components/Frame")).Frame }),
        children: [
          {
            path: "/",
            lazy: page(
              () => import("./pages/Dashboard"),
              (m) => m.DashboardPage,
            ),
          },
          {
            path: "/projects",
            lazy: page(
              () => import("./pages/Projects"),
              (m) => m.ProjectsPage,
            ),
          },
          {
            path: "/projects/new",
            lazy: page(
              () => import("./pages/Projects"),
              (m) => m.NewProjectPage,
              "projects.manage",
            ),
          },
          // a branch's name may hold "/": it is one segment, written with encodeURIComponent (branchPath in components/ProjectWorkspace.tsx)
          {
            path: "/projects/:id/branches/:branch/:view?",
            lazy: page(
              () => import("./pages/Projects"),
              (m) => m.ProjectPage,
            ),
          },
          {
            path: "/projects/:id/:tab?",
            lazy: page(
              () => import("./pages/Projects"),
              (m) => m.ProjectPage,
            ),
          },
          {
            path: "/i/:name/:tab?",
            lazy: page(
              () => import("./pages/Instance"),
              (m) => m.InstancePage,
            ),
          },
          {
            path: "/new",
            lazy: page(
              () => import("./pages/NewInstance"),
              (m) => m.NewInstancePage,
              "instances.create",
            ),
          },
          {
            path: "/settings",
            lazy: page(
              () => import("./pages/Settings"),
              (m) => m.SettingsPage,
              "settings.manage",
            ),
          },
          {
            path: "/email-templates",
            lazy: page(
              () => import("./pages/EmailTemplates"),
              (m) => m.EmailTemplatesPage,
              "mail.manage",
            ),
          },
          {
            path: "/plugins/:tab?",
            lazy: page(
              () => import("./pages/Plugins"),
              (m) => m.PluginsPage,
              "plugins.view",
            ),
          },
          {
            path: "/accounts",
            lazy: page(
              () => import("./pages/Accounts"),
              (m) => m.AccountsPage,
              "accounts.manage",
            ),
          },
          {
            path: "/teams",
            lazy: page(
              () => import("./premium/pages"),
              (m) => m.TeamsRoute,
              "teams.manage",
            ),
          },
          {
            path: "/tokens",
            lazy: page(
              () => import("./pages/Tokens"),
              (m) => m.TokensPage,
              "tokens.manage",
            ),
          },
          {
            path: "/maintenance",
            lazy: page(
              () => import("./pages/Maintenance"),
              (m) => m.MaintenancePage,
              "instances.view",
            ),
          },
          {
            path: "/backups",
            lazy: page(
              () => import("./pages/Backups"),
              (m) => m.BackupsPage,
              "backups.manage",
            ),
          },
          {
            path: "/support-access",
            lazy: page(
              () => import("./premium/pages"),
              (m) => m.SupportAccessRoute,
              "support_access.manage",
            ),
          },
          {
            path: "/servers/*",
            lazy: page(
              () => import("./premium/pages"),
              (m) => m.ServersRoute,
              "settings.manage",
            ),
          },
          {
            path: "/activity",
            lazy: page(
              () => import("./pages/Audit"),
              (m) => m.AuditPage,
              "audit.view",
            ),
          },
          {
            path: "/github/app/:step",
            lazy: page(
              () => import("./pages/GitHub"),
              (m) => m.GitHubReturnPage,
              "settings.manage",
            ),
          },
          {
            path: "/github",
            lazy: page(
              () => import("./pages/GitHub"),
              (m) => m.GitHubPage,
              "github.manage",
            ),
          },
          {
            path: "/account",
            lazy: page(
              () => import("./pages/Account"),
              (m) => m.AccountPage,
            ),
          },
          {
            path: "/help/*",
            lazy: page(
              () => import("./pages/Help"),
              (m) => m.HelpPage,
            ),
          },
          // a page a premium feature adds (premium/), else "not found"
          {
            path: "*",
            lazy: page(
              () => import("./premium/pages"),
              (m) => m.PremiumOrNotFound,
            ),
          },
        ],
      },
    ],
  },
];

export const router = createBrowserRouter(routes.map((route) => ({ errorElement: <RouteError />, ...route })));
