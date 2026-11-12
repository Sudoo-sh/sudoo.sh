import {
  Activity,
  BookOpen,
  ChevronRight,
  Cpu,
  DatabaseBackup,
  FolderGit2,
  KeyRound,
  LayoutGrid,
  LogOut,
  Menu as MenuIcon,
  MemoryStick,
  Monitor,
  Moon,
  Plus,
  Settings2,
  Sun,
  UserRound,
  Users,
} from "lucide-react";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { Link, NavLink, Navigate, Outlet, useLocation, useNavigate } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { api, setCsrfToken } from "@/api/client";
import { useLiveEvents, type LiveState } from "@/api/live";
import { keys, useInstances, useMetrics, useSession, useSettings } from "@/api/queries";
import type { Session } from "@/api/types";
import { percent } from "@/lib/format";
import { applyBrandColor, applyTheme, storedTheme, type ThemeChoice } from "@/lib/theme";
import { Brand } from "./ui/Brand";
import { Button, Spinner } from "./ui/basics";
import { Menu, useToast } from "./ui/overlay";

const SessionContext = createContext<Session | null>(null);
export const useMe = () => useContext(SessionContext)!;
export const useIsAdmin = () => useMe().role === "admin";

/** Pages that need a signed-in session; sends everyone else to the sign-in page. */
export function RequireSession() {
  const { data: session, isPending } = useSession();
  const location = useLocation();
  if (isPending)
    return (
      <div style={{ display: "grid", placeItems: "center", height: "100vh" }}>
        <Spinner />
      </div>
    );
  if (!session) return <Navigate to={`/login?next=${encodeURIComponent(location.pathname + location.search)}`} replace />;
  setCsrfToken(session.csrf_token);
  if (session.must_set_up_second_factor && location.pathname !== "/setup-second-factor")
    return <Navigate to="/setup-second-factor" replace />;
  return (
    <SessionContext.Provider value={session}>
      <Outlet />
    </SessionContext.Provider>
  );
}

function NavItem({ to, icon, children, end, count }: { to: string; icon: ReactNode; children: ReactNode; end?: boolean; count?: number }) {
  return (
    <NavLink to={to} end={end} viewTransition className={({ isActive }) => `nav-link ${isActive ? "active" : ""}`}>
      {icon}
      <span>{children}</span>
      {count != null && <span className="badge count">{count}</span>}
    </NavLink>
  );
}

function ThemeButton() {
  const [theme, setTheme] = useState<ThemeChoice>(storedTheme);
  const next: Record<ThemeChoice, ThemeChoice> = { system: "light", light: "dark", dark: "system" };
  const icon = { system: <Monitor />, light: <Sun />, dark: <Moon /> }[theme];
  return (
    <Button
      variant="ghost"
      size="sm"
      icon={icon}
      title={`Theme: ${theme}`}
      onClick={() => {
        applyTheme(next[theme]);
        setTheme(next[theme]);
      }}
    >
      {theme === "system" ? "System" : theme === "light" ? "Light" : "Dark"}
    </Button>
  );
}

function Sidebar({ open, onNavigate }: { open: boolean; onNavigate: () => void }) {
  const me = useMe();
  const admin = me.role === "admin";
  const { data: instances } = useInstances();
  const client = useQueryClient();
  const navigate = useNavigate();
  const toast = useToast();
  const signOut = async () => {
    await api.del("/session").catch(() => undefined);
    client.setQueryData(keys.session, null);
    client.clear();
    navigate("/login");
    toast("info", "Signed out");
  };
  return (
    <aside className={`sidebar ${open ? "open" : ""}`} onClick={(e) => (e.target as HTMLElement).closest("a") && onNavigate()}>
      <Brand />
      <div className="nav-group">Workspace</div>
      <nav>
        <NavItem to="/" end icon={<LayoutGrid />} count={instances?.length}>
          Instances
        </NavItem>
        {admin && (
          <NavItem to="/new" icon={<Plus />}>
            New instance
          </NavItem>
        )}
      </nav>
      {admin && (
        <>
          <div className="nav-group">Operations</div>
          <nav>
            <NavItem to="/backups" icon={<DatabaseBackup />}>
              Backups
            </NavItem>
            <NavItem to="/activity" icon={<Activity />}>
              Audit log
            </NavItem>
            <NavItem to="/github" icon={<FolderGit2 />}>
              GitHub access
            </NavItem>
          </nav>
          <div className="nav-group">Administration</div>
          <nav>
            <NavItem to="/settings" icon={<Settings2 />}>
              Settings
            </NavItem>
            <NavItem to="/accounts" icon={<Users />}>
              Accounts
            </NavItem>
            <NavItem to="/tokens" icon={<KeyRound />}>
              API tokens
            </NavItem>
          </nav>
        </>
      )}
      <div className="sidebar-foot">
        <div className="row">
          <a className="btn ghost sm" href="/docs" target="_blank" rel="noreferrer">
            <BookOpen /> Docs
          </a>
          <span className="spacer" />
          <ThemeButton />
        </div>
        <Menu
          align="left"
          trigger={(toggle) => (
            <button className="user-card" onClick={toggle}>
              <span className="avatar">{me.user.slice(0, 2)}</span>
              <span className="who">
                <span className="name truncate">{me.user}</span>
                <br />
                <span className="role">{me.role_label}</span>
              </span>
              <ChevronRight size={16} className="faint" />
            </button>
          )}
        >
          {(close) => (
            <>
              <Link to="/account" viewTransition onClick={close}>
                <UserRound /> My account
              </Link>
              <hr />
              <button className="danger" onClick={signOut}>
                <LogOut /> Sign out
              </button>
            </>
          )}
        </Menu>
      </div>
    </aside>
  );
}

const LIVE_LABEL: Record<LiveState, string> = { open: "Live", connecting: "Connecting", closed: "Offline" };

const CRUMBS: Record<string, string> = {
  "": "Instances",
  new: "New instance",
  settings: "Settings",
  accounts: "Accounts",
  tokens: "API tokens",
  backups: "Backups",
  activity: "Audit log",
  github: "GitHub access",
  account: "My account",
  i: "Instances",
};

function Topbar({ live, onMenu }: { live: LiveState; onMenu: () => void }) {
  const location = useLocation();
  const { data: metrics } = useMetrics();
  const parts = location.pathname.split("/").filter(Boolean);
  const section = CRUMBS[parts[0] ?? ""] ?? "Sudoo.sh";
  const server = metrics?.server;
  return (
    <header className="topbar">
      <Button className="menu-button" variant="ghost" icon={<MenuIcon />} onClick={onMenu} aria-label="Menu" />
      <div className="crumbs">
        {parts[0] === "i" && parts[1] ? (
          <>
            <Link to="/" viewTransition>
              Instances
            </Link>
            <ChevronRight />
            <span className="current truncate">{parts[1]}</span>
          </>
        ) : (
          <span className="current">{section}</span>
        )}
      </div>
      <span className="spacer" />
      {server?.cpu_percent != null && (
        <>
          <span className="chip-metric hide-sm" title="Server processor">
            <Cpu /> CPU <b>{percent(server.cpu_percent)}</b>
          </span>
          <span className="chip-metric hide-sm" title="Server memory">
            <MemoryStick /> RAM <b>{percent(server.memory_percent)}</b>
          </span>
        </>
      )}
      <span className={`live ${live}`} title="Changes appear as they happen">
        <span className="dot" />
        {LIVE_LABEL[live]}
      </span>
    </header>
  );
}

/** The frame of every signed-in page: sidebar, top bar, live updates, brand colour. */
export function Shell() {
  const live = useLiveEvents(true);
  const [menuOpen, setMenuOpen] = useState(false);
  const { pathname } = useLocation();
  useEffect(() => setMenuOpen(false), [pathname]);
  const { data: settings } = useSettings();
  useEffect(() => {
    applyBrandColor(settings?.find((s) => s.key === "brand_color")?.value as string | undefined);
  }, [settings]);
  return (
    <div className="shell">
      <Sidebar open={menuOpen} onNavigate={() => setMenuOpen(false)} />
      {menuOpen && <div className="sidebar-scrim" onClick={() => setMenuOpen(false)} />}
      <div className="main">
        <Topbar live={live} onMenu={() => setMenuOpen(true)} />
        <Outlet />
      </div>
    </div>
  );
}

export function Page({ children }: { children: ReactNode }) {
  return <main className="page">{children}</main>;
}

export function PageHead({ eyebrow, title, children, actions }: { eyebrow?: string; title: ReactNode; children?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="page-head rise">
      <div className="titles">
        {eyebrow && <span className="eyebrow">{eyebrow}</span>}
        <h1>{title}</h1>
        {children && <p>{children}</p>}
      </div>
      {actions && <div className="actions">{actions}</div>}
    </div>
  );
}

export function AdminOnly({ children }: { children: ReactNode }) {
  return useIsAdmin() ? <>{children}</> : <Navigate to="/" replace />;
}
