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
  Monitor,
  Moon,
  Plus,
  Puzzle,
  ScrollText,
  Settings2,
  Sun,
  UserRound,
  Users,
  UsersRound,
} from "lucide-react";
import { useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import { flushSync } from "react-dom";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router";
import { api } from "@/api/client";
import { useLiveEvents, type Sync } from "@/api/live";
import { keys, useAlerts, useInstances, useMetrics, usePluginNotices, useProjects } from "@/api/queries";
import type { Alert } from "@/api/types";
import { useT } from "@/i18n";
import common from "@/i18n/common";
import { ago, percent } from "@/lib/format";
import { applyTheme, storedTheme, type ThemeChoice } from "@/lib/theme";
import { revealTheme } from "@/lib/theme-reveal";
import { NARROW, useMedia } from "@/lib/media";
import { useDirection } from "@/components/ui/direction";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { LanguageSwitch } from "./LanguageSwitch";
import { Brand } from "./app/Brand";
import { Button, Callout } from "./app/basics";
import { BackToTop } from "./BackToTop";
import { HelpButton } from "./HelpPanel";
import { PageMotion } from "./PageMotion";
import { PlanBanner } from "./PlanSettings";
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

function NavItem({ to, icon, children, end, count }: { to: string; icon: ReactNode; children: ReactNode; end?: boolean; count?: number }) {
  return (
    <NavLink to={to} end={end} viewTransition className={({ isActive }) => `nav-link ${isActive ? "active" : ""}`}>
      {icon}
      <span>{children}</span>
      {count != null && <span className="count">{count}</span>}
    </NavLink>
  );
}

function ThemeButton() {
  const [theme, setTheme] = useState<ThemeChoice>(storedTheme);
  const next: Record<ThemeChoice, ThemeChoice> = { system: "light", light: "dark", dark: "system" };
  const icon = { system: <Monitor />, light: <Sun />, dark: <Moon /> }[theme];
  const label = useT(common).theme[theme];
  return (
    <Button
      variant="ghost"
      size="sm"
      icon={icon}
      title={label}
      aria-label={label}
      onClick={(event) => {
        const choice = next[theme];
        // the new theme grows in a circle from this button; with reduced motion or an older browser it fades
        void revealTheme(event.currentTarget, (revealed) => {
          flushSync(() => setTheme(choice));
          applyTheme(choice, !revealed);
        });
      }}
    />
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
  const operations = can("backups.manage") || can("audit.view") || can("github.manage");
  // a page a premium feature adds to the menu (only Servers so far; Teams and Support access are always listed)
  const servers = usePremiumRoutes().some(({ route }) => route.path === "/servers");
  const administration =
    can("settings.manage") || can("mail.manage") || can("accounts.manage") || can("teams.manage") || can("tokens.manage") || can("plugins.view") || can("support_access.manage");
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
              <MenuItem asChild><Link to="/account" viewTransition onClick={close}>
                <UserRound /> {t.myAccount}
              </Link></MenuItem>
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
  const unseen = list.filter((a) => a.last_at > seen && a.severity !== "info").length;
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
          <Button variant="ghost" size="sm" icon={<Bell />} title={label} aria-label={label}
            onClick={() => { toggle(); markSeen(); }} />
          {unseen > 0 && <span className="bell-dot" aria-hidden="true" />}
        </span>
      )}
    >
      {(close) => (
        <div className="bell-menu">
          <div className="bell-head">
            <b>{t.alerts}</b>
            <span className="spacer" />
            {can("settings.manage") && <Link to="/settings?tab=alerts" viewTransition onClick={close}>{nav.settings}</Link>}
          </div>
          {list.length === 0 ? (
            <p className="bell-empty">{t.alertsEmpty}</p>
          ) : (
            <div className="bell-list">
              {list.map((alert) => (
                <MenuItem key={alert.id} asChild><Link to={alert.link ?? "/"} viewTransition onClick={close} className={alert.resolved_at ? "resolved" : ""}>
                  <AlertIcon alert={alert} />
                  <span className="bell-item">
                    <span className="bell-title">{alert.title}</span>
                    <span className="bell-text">{alert.text}</span>
                    <span className="bell-when">
                      {ago(alert.last_at)}
                      {alert.count > 1 && t.times(alert.count)}
                      {alert.severity === "problem" && alert.resolved_at && t.resolved}
                    </span>
                  </span>
                </Link></MenuItem>
              ))}
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
      <Button className="menu-button" variant="ghost" icon={<MenuIcon />} onClick={onMenu} aria-label={all.common.menu} />
      <nav className="crumbs" aria-label={t.breadcrumb}>
        {page && sub ? (
          <>
            <Link to={`/${parts[0]}`} viewTransition>
              {all.nav[page]}
            </Link>
            <ChevronRight className="flip-rtl" aria-hidden="true" />
            <span className="current truncate" aria-current="page">{sub}</span>
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
              <Link to="/plugins/installed" viewTransition>{t.openPlugins}</Link>
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
        <Link to="/account" viewTransition>{t.makeRecoveryCodes}</Link>
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
          <SheetContent side={rtl ? "right" : "left"} showCloseButton={false} className="sidebar-sheet" aria-describedby={undefined}>
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
        <Outlet />
      </div>
      <BackToTop />
    </div>
  );
}

export function Page({ children, wide }: { children: ReactNode; wide?: boolean }) {
  return (
    <main className="page" tabIndex={-1} style={wide ? { maxWidth: 1600 } : undefined}>
      {children}
    </main>
  );
}

export function PageHead({ title, children, actions }: { title: ReactNode; children?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="page-head">
      <div className="titles">
        <h1>{title}</h1>
        {children && <p>{children}</p>}
      </div>
      {actions && <div className="actions">{actions}</div>}
    </div>
  );
}
