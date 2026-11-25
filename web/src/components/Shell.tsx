import { useQueryClient } from "@tanstack/react-query";
import {
  Bell,
  BookOpen,
  CircleAlert,
  CircleCheck,
  Info,
  ChevronRight,
  ChevronsUpDown,
  DatabaseBackup,
  FolderGit2,
  FolderKanban,
  KeyRound,
  LayoutGrid,
  LifeBuoy,
  ServerCog,
  LogOut,
  Mail,
  Menu as MenuIcon,
  Plus,
  Puzzle,
  ScrollText,
  Settings2,
  UserRound,
  Users,
  UsersRound,
  Wrench,
} from "lucide-react";
import { createContext, useContext, useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router";
import { api } from "@/api/client";
import { useLiveEvents, type Sync } from "@/api/live";
import { keys, useAlerts, useInstances, useMetrics, usePluginNotices, useProjects } from "@/api/queries";
import type { Alert } from "@/api/types";
import { useT } from "@/i18n";
import common from "@/i18n/common";
import { ago, percent } from "@/lib/format";
import { NARROW, useMedia } from "@/lib/media";
import { useDirection } from "@/components/ui/direction";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { LanguageSwitch } from "./LanguageSwitch";
import { ThemeButton } from "./ThemeButton";
import { Brand } from "./app/Brand";
import { Button, Callout } from "./app/basics";
import { BackToTop } from "./BackToTop";
import { CommandButton, CommandPalette } from "./CommandPalette";
import { HelpButton } from "./HelpPanel";
import { PageMotion } from "./PageMotion";
import { PlanBanner } from "./PlanSettings";
import { MaintenanceBanner } from "./Maintenance";
import maintenanceWords from "@/i18n/maintenance";
import { PremiumSlot, usePremiumRoutes } from "@/premium/components";
import { Menu, MenuItem, MenuSeparator, useToast } from "./app/overlay";

import { useCan, useMe } from "./Session";

export { RequirePermission, RequireSession, useCan, useMe } from "./Session";

export function useSignOut() {
  const client = useQueryClient();
  const navigate = useNavigate();
  const toast = useToast();
  const t = useT(common).nav;
  return async () => {
    await api.del("/session").catch(() => undefined);
    client.setQueryData(keys.session, null);
    client.clear();
    navigate("/login");
    toast("info", t.signedOut);
  };
}

function NavItem({
  to,
  icon,
  children,
  end,
  count,
}: {
  to: string;
  icon: ReactNode;
  children: ReactNode;
  end?: boolean;
  count?: number;
}) {
  return (
    <NavLink to={to} end={end} viewTransition className={({ isActive }) => `nav-link ${isActive ? "active" : ""}`}>
      {icon}
      <span>{children}</span>
      {count != null && <span className="count">{count}</span>}
    </NavLink>
  );
}

function Sidebar() {
  const me = useMe();
  const can = useCan();
  const { data: instances } = useInstances();
  const { data: projects } = useProjects();
  const signOut = useSignOut();
  const all = useT(common);
  const t = all.nav;
  const maintenance = useT(maintenanceWords).nav;
  const operations = can("backups.manage") || can("audit.view") || can("github.manage");
  // a page a premium feature adds to the menu (only Servers so far; Teams and Support access are always listed)
  const servers = usePremiumRoutes().some(({ route }) => route.path === "/servers");
  const administration =
    can("settings.manage") ||
    can("mail.manage") ||
    can("accounts.manage") ||
    can("teams.manage") ||
    can("tokens.manage") ||
    can("plugins.view") ||
    can("support_access.manage");
  return (
    <aside className="sidebar">
      <Brand />
      <div className="nav-group">{t.workspace}</div>
      <nav>
        <NavItem to="/projects" icon={<FolderKanban />} count={projects?.projects.length}>
          {t.projects}
        </NavItem>
        <NavItem to="/" end icon={<LayoutGrid />} count={instances?.length}>
          {t.instances}
        </NavItem>
        {can("instances.create") && (
          <NavItem to="/new" icon={<Plus />}>
            {t.newInstance}
          </NavItem>
        )}
        <NavItem to="/maintenance" icon={<Wrench />}>
          {maintenance}
        </NavItem>
        <NavItem to="/help" icon={<BookOpen />}>
          {t.help}
        </NavItem>
      </nav>
      {operations && (
        <>
          <div className="nav-group">{t.operations}</div>
          <nav>
            {can("backups.manage") && (
              <NavItem to="/backups" icon={<DatabaseBackup />}>
                {t.backups}
              </NavItem>
            )}
            {can("github.manage") && (
              <NavItem to="/github" icon={<FolderGit2 />}>
                {t.github}
              </NavItem>
            )}
            {can("audit.view") && (
              <NavItem to="/activity" icon={<ScrollText />}>
                {t.audit}
              </NavItem>
            )}
          </nav>
        </>
      )}
      {administration && (
        <>
          <div className="nav-group">{t.administration}</div>
          <nav>
            {can("settings.manage") && (
              <NavItem to="/settings" icon={<Settings2 />}>
                {t.settings}
              </NavItem>
            )}
            {can("mail.manage") && (
              <NavItem to="/email-templates" icon={<Mail />}>
                {t.emailTemplates}
              </NavItem>
            )}
            {can("plugins.view") && (
              <NavItem to="/plugins" icon={<Puzzle />}>
                {t.plugins}
              </NavItem>
            )}
            {can("accounts.manage") && (
              <NavItem to="/accounts" icon={<Users />}>
                {t.accounts}
              </NavItem>
            )}
            {can("teams.manage") && (
              <NavItem to="/teams" icon={<UsersRound />}>
                {t.teams}
              </NavItem>
            )}
            {can("tokens.manage") && (
              <NavItem to="/tokens" icon={<KeyRound />}>
                {t.tokens}
              </NavItem>
            )}
            {can("support_access.manage") && (
              <NavItem to="/support-access" icon={<LifeBuoy />}>
                {t.supportAccess}
              </NavItem>
            )}
            {servers && (
              <NavItem to="/servers" icon={<ServerCog />}>
                {t.servers}
              </NavItem>
            )}
          </nav>
        </>
      )}
      <div className="sidebar-foot">
        <Menu
          align="left"
          trigger={(toggle) => (
            <button className="user-card" onClick={toggle}>
              <span className="avatar">{(me.first_name?.[0] ?? me.user[0]) + (me.last_name?.[0] ?? "")}</span>
              <span className="who">
                <span className="name truncate">{me.display_name}</span>
                <br />
                <span className="role">{all.roles[me.role] ?? me.role_label}</span>
              </span>
              <ChevronsUpDown />
            </button>
          )}
        >
          {(close) => (
            <>
              <MenuItem asChild>
                <Link to="/account" viewTransition onClick={close}>
                  <UserRound /> {t.myAccount}
                </Link>
              </MenuItem>
              <MenuSeparator />
              <MenuItem danger onSelect={signOut}>
                <LogOut /> {t.signOut}
              </MenuItem>
            </>
          )}
        </Menu>
        <div className="row" style={{ padding: "0 8px" }}>
          <span className="xsmall faint">Sudoo.sh</span>
          <span className="spacer" />
          <LanguageSwitch signedIn />
          <ThemeButton />
        </div>
      </div>
    </aside>
  );
}

/** Nothing while connected; a small notice only while the connection to the server is lost. */
function Reconnecting({ sync }: { sync: Sync }) {
  const [lost, setLost] = useState(false);
  const t = useT(common).shell;
  useEffect(() => {
    if (sync.state === "open") {
      setLost(false);
      return;
    }
    const timer = setTimeout(() => setLost(true), 3000); // short drops pass unnoticed
    return () => clearTimeout(timer);
  }, [sync.state]);
  if (!lost) return null;
  return (
    <div className="reconnecting" role="status">
      <span className="spinner" />
      {sync.state === "closed" ? t.signedOutReload : t.reconnecting}
    </div>
  );
}

const CRUMBS: Record<string, keyof (typeof common)["en"]["nav"]> = {
  "": "instances",
  projects: "projects",
  new: "newInstance",
  settings: "settings",
  "email-templates": "emailTemplates",
  plugins: "plugins",
  accounts: "accounts",
  teams: "teams",
  tokens: "tokens",
  "support-access": "supportAccess",
  servers: "servers",
  backups: "backups",
  activity: "audit",
  github: "github",
  account: "myAccount",
  help: "help",
};

// A page's own last crumb, after the page's name (Settings › E-mail): the page sets it, the top bar shows it. The top
// bar stays still while the page changes it (moving between the tabs of Settings).
let subCrumb: string | null = null;
const crumbListeners = new Set<() => void>();
const setSubCrumb = (label: string | null) => {
  subCrumb = label;
  crumbListeners.forEach((listener) => listener());
};
const followSubCrumb = (listener: () => void) => {
  crumbListeners.add(listener);
  return () => crumbListeners.delete(listener);
};

/** Shows `label` after the page's name in the top bar's breadcrumb while the page is open. */
export function useSubCrumb(label: string | null) {
  useEffect(() => {
    setSubCrumb(label);
    return () => setSubCrumb(null);
  }, [label]);
}

const SEEN_KEY = "sudoo.alerts.seen";

function readSeen(): number {
  try {
    return Number(localStorage.getItem(SEEN_KEY)) || 0;
  } catch {
    return 0;
  }
}

function AlertIcon({ alert }: { alert: Alert }) {
  if (alert.severity === "recovered") return <CircleCheck className="positive" />;
  if (alert.severity === "info") return <Info />;
  return <CircleAlert className={alert.resolved_at ? "" : "negative"} />;
}

/** The bell: the latest alerts, for those who may read the audit log. A dot marks alerts newer than the last time the
 *  list was opened (remembered in this browser only). */
function Alerts() {
  const can = useCan();
  const allowed = can("audit.view");
  const { data: alerts } = useAlerts(allowed);
  const [seen, setSeen] = useState(readSeen);
  const t = useT(common).shell;
  const nav = useT(common).nav;
  if (!allowed) return null;
  const list = alerts ?? [];
  // a standing warning (the default admin password, a missing country database) is not news: it never counts for the dot
  const standing = (a: Alert) => a.type === "default_admin_password" || a.type === "country_database";
  const unseen = list.filter((a) => a.last_at > seen && a.severity !== "info" && !standing(a)).length;
  const markSeen = () => {
    const latest = Math.max(seen, ...list.map((a) => a.last_at));
    setSeen(latest);
    try {
      localStorage.setItem(SEEN_KEY, String(latest));
    } catch {
      /* private window: the dot comes back after a reload */
    }
  };
  const label = unseen ? t.alertsNew(unseen) : t.alerts;
  return (
    <Menu
      trigger={(toggle) => (
        <span className="bell">
          <Button
            variant="ghost"
            size="sm"
            icon={<Bell />}
            title={label}
            aria-label={label}
            onClick={() => {
              toggle();
              markSeen();
            }}
          />
          {unseen > 0 && <span className="bell-dot" aria-hidden="true" />}
        </span>
      )}
    >
      {(close) => (
        <div className="bell-menu">
          <div className="bell-head">
            <b>{t.alerts}</b>
            <span className="spacer" />
            {can("settings.manage") && (
              <Link to="/settings?tab=alerts" viewTransition onClick={close}>
                {nav.settings}
              </Link>
            )}
          </div>
          {list.length === 0 ? (
            <p className="bell-empty">{t.alertsEmpty}</p>
          ) : (
            <div className="bell-list">
              {list.map((alert) => {
                const body = (
                  <>
                    <AlertIcon alert={alert} />
                    <span className="bell-item">
                      <span className="bell-title">{standing(alert) ? t.defaultPasswordTitle : alert.title}</span>
                      <span className="bell-text">
                        {standing(alert)
                          ? can("settings.manage")
                            ? t.defaultPasswordText
                            : t.defaultPasswordTextAdmin
                          : alert.text}
                      </span>
                      {!standing(alert) && (
                        <span className="bell-when">
                          {ago(alert.last_at)}
                          {alert.count > 1 && t.times(alert.count)}
                          {alert.severity === "problem" && alert.resolved_at && t.resolved}
                        </span>
                      )}
                    </span>
                  </>
                );
                // the standing warning links to the setting only for those who may change it; else it is plain text
                if (!alert.link)
                  return (
                    <MenuItem key={alert.id} className="bell-static">
                      {body}
                    </MenuItem>
                  );
                return (
                  <MenuItem key={alert.id} asChild>
                    <Link
                      to={alert.link}
                      viewTransition
                      onClick={close}
                      className={alert.resolved_at ? "resolved" : ""}
                    >
                      {body}
                    </Link>
                  </MenuItem>
                );
              })}
            </div>
          )}
        </div>
      )}
    </Menu>
  );
}

function Topbar({ onMenu }: { onMenu: () => void }) {
  const location = useLocation();
  const { data: metrics } = useMetrics();
  const parts = location.pathname.split("/").filter(Boolean);
  const server = metrics?.server;
  const all = useT(common);
  const t = all.shell;
  const sub = useSyncExternalStore(followSubCrumb, () => subCrumb);
  const page = CRUMBS[parts[0] ?? ""];
  return (
    <header className="topbar">
      <Button
        className="menu-button"
        variant="ghost"
        icon={<MenuIcon />}
        onClick={onMenu}
        aria-label={all.common.menu}
      />
      <nav className="crumbs" aria-label={t.breadcrumb}>
        {page && sub ? (
          <>
            <Link to={`/${parts[0]}`} viewTransition>
              {all.nav[page]}
            </Link>
            <ChevronRight className="flip-rtl" aria-hidden="true" />
            <span className="current truncate" aria-current="page">
              {sub}
            </span>
          </>
        ) : parts[0] === "i" && parts[1] ? (
          <>
            <Link to="/" viewTransition>
              {all.nav.instances}
            </Link>
            <ChevronRight className="flip-rtl" />
            <bdi className="current truncate">{parts[1]}</bdi>
          </>
        ) : (
          <span className="current">{page ? all.nav[page] : "Sudoo.sh"}</span>
        )}
      </nav>
      <span className="spacer" />
      {server?.cpu_percent != null && (
        <span className="meter hide-sm" title={t.meterTitle}>
          {t.cpu} <b>{percent(server.cpu_percent)}</b> &nbsp; {t.memory} <b>{percent(server.memory_percent)}</b>
        </span>
      )}
      <CommandButton />
      <HelpButton />
      <Alerts />
    </header>
  );
}

/** Banners about plugins that need attention: a licence ending, in its grace period or ended, changed files. */
function PluginNotices() {
  const can = useCan();
  const { pathname } = useLocation();
  const { data: notices } = usePluginNotices(can("plugins.view"));
  const t = useT(common).shell;
  if (!notices?.length) return null;
  const tone = (level: string) => (level === "danger" ? "danger" : level === "warning" ? "warning" : "info");
  return (
    <div className="notices" role="status">
      {notices.slice(0, 3).map((notice, i) => (
        <Callout key={`${notice.plugin}-${notice.kind}-${i}`} tone={tone(notice.level)}>
          {notice.text}
          {!pathname.startsWith("/plugins") && (
            <>
              {" "}
              <Link to="/plugins/installed" viewTransition>
                {t.openPlugins}
              </Link>
            </>
          )}
        </Callout>
      ))}
    </div>
  );
}

/** Accounts that use the authenticator app without recovery codes (set up before they existed, or all used) are
 *  asked to make them, on every page but the account page where that is done. */
function RecoveryCodesNotice() {
  const me = useMe();
  const { pathname } = useLocation();
  const t = useT(common).shell;
  if (!me.recovery_codes_needed || pathname === "/account") return null;
  return (
    <div className="notices" role="status">
      <Callout tone="warning">
        {t.noRecoveryCodes}{" "}
        <Link to="/account" viewTransition>
          {t.makeRecoveryCodes}
        </Link>
      </Callout>
    </div>
  );
}

/** The frame of every signed-in page: sidebar, top bar, live updates. */
export function Shell() {
  const sync = useLiveEvents(true);
  const [menuOpen, setMenuOpen] = useState(false);
  const narrow = useMedia(NARROW);
  const rtl = useDirection() === "rtl";
  const menuLabel = useT(common).common.menu;
  const { pathname } = useLocation();
  useEffect(() => setMenuOpen(false), [pathname, narrow]);
  return (
    <div className="shell">
      <PageMotion />
      {narrow ? (
        // up to 960px the sidebar is a sheet from the inline-start edge, opened from the top bar
        <Sheet open={menuOpen} onOpenChange={setMenuOpen}>
          <SheetContent
            side={rtl ? "right" : "left"}
            showCloseButton={false}
            className="sidebar-sheet"
            aria-describedby={undefined}
          >
            <SheetTitle className="sr-only">{menuLabel}</SheetTitle>
            <Sidebar />
          </SheetContent>
        </Sheet>
      ) : (
        <Sidebar />
      )}
      <div className="main">
        <Topbar onMenu={() => setMenuOpen(true)} />
        <Reconnecting sync={sync} />
        <PluginNotices />
        <RecoveryCodesNotice />
        <PremiumSlot name="shell.banner" />
        <PlanBanner />
        {pathname !== "/maintenance" && <MaintenanceBanner />}
        <Outlet />
      </div>
      <BackToTop />
      <CommandPalette />
    </div>
  );
}

/** True inside another page: a project's branch shows its instance's page in its own pane, where Page draws a plain
 *  block instead of a second <main>. */
export const InsidePage = createContext(false);

export function Page({ children, wide }: { children: ReactNode; wide?: boolean }) {
  const inside = useContext(InsidePage);
  if (inside) return <div className="page-inside">{children}</div>;
  return (
    <main className="page" tabIndex={-1} style={wide ? { maxWidth: 1600 } : undefined}>
      {children}
    </main>
  );
}

/** Something shown next to the titles of the pages inside it (a premium page's plans, in the demo). */
export const PageMark = createContext<ReactNode>(null);

/** A page's title, its line under it and its actions. `below` goes under the titles and `side` under the actions,
 *  which then sit at the top (a project's chosen branch and its commands). */
export function PageHead({
  title,
  children,
  actions,
  below,
  side,
}: {
  title: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
  below?: ReactNode;
  side?: ReactNode;
}) {
  const mark = useContext(PageMark);
  return (
    <div className={side ? "page-head page-head-split" : "page-head"}>
      <div className="titles">
        {mark ? (
          <div className="row-wrap" style={{ gap: 12 }}>
            <h1>{title}</h1>
            {mark}
          </div>
        ) : (
          <h1>{title}</h1>
        )}
        {children && <p>{children}</p>}
        {below}
      </div>
      {side ? (
        <div className="page-head-side">
          {actions && <div className="actions">{actions}</div>}
          {side}
        </div>
      ) : (
        actions && <div className="actions">{actions}</div>
      )}
    </div>
  );
}
