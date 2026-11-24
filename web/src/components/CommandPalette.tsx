// The command palette: Ctrl+K (⌘K on a Mac) anywhere in the panel opens a search over the projects, their branches
// (to the branch's history), the instances and their tabs ("Logs of acme-prod"), the panel's pages, and two safe
// actions per instance (Get latest code, Restart), each asked again before it runs. Only what the signed-in role may
// open is listed; the backend checks every request again. Recent choices come first while nothing is typed.
// Keyboard only: arrows move, Enter opens or runs, Escape goes back or closes. "/" outside a field focuses the branch
// filter of a project's workspace (an input marked data-branch-filter).

import {
  DownloadCloud,
  FolderKanban,
  GitBranch,
  History,
  LayoutGrid,
  RotateCw,
  Search,
  SquareArrowOutUpRight,
  type LucideIcon,
} from "lucide-react";
import { useCallback, useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useNavigate } from "react-router";
import { api, errorText } from "@/api/client";
import { useInstances, useProjects } from "@/api/queries";
import type { Instance, Job, Permission } from "@/api/types";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { useDirection } from "@/components/ui/direction";
import { useT } from "@/i18n";
import instanceWords from "@/i18n/instance";
import buildWords from "@/i18n/builds";
import projectWords from "@/i18n/projects";
import words from "@/i18n/status";
import { rank } from "@/lib/fuzzy";
import { useToast } from "./app/overlay";
import { useCan } from "./Session";

const OPEN_EVENT = "sudoo:command-palette";
const RECENT_KEY = "sudoo.palette.recent";
const RECENT_KEPT = 6;
const ASLEEP = ["paused", "switched_off", "stopped"];

type Group = "projects" | "branches" | "instances" | "tabs" | "pages" | "actions";

interface Item {
  id: string;
  group: Group;
  label: string;
  /** shown after the label, quieter */
  detail?: string;
  /** other words that find it */
  keywords: string[];
  icon: LucideIcon;
  /** where it goes */
  to?: string;
  /** an action, asked again before it runs */
  action?: { instance: Instance; kind: "update" | "restart" };
}

/** Opens the palette from anywhere (the top bar's search button). */
export const openCommandPalette = () => window.dispatchEvent(new Event(OPEN_EVENT));

const isMac = () => /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

function typing(target: EventTarget | null): boolean {
  const element = target as HTMLElement | null;
  return (
    !!element &&
    (element.isContentEditable ||
      ["INPUT", "TEXTAREA", "SELECT"].includes(element.tagName) ||
      !!element.closest("[role=textbox], .xterm"))
  );
}

function readRecent(): string[] {
  try {
    const found = JSON.parse(localStorage.getItem(RECENT_KEY) ?? "[]");
    return Array.isArray(found) ? found.filter((id): id is string => typeof id === "string").slice(0, RECENT_KEPT) : [];
  } catch {
    return [];
  }
}

function remember(id: string) {
  try {
    localStorage.setItem(
      RECENT_KEY,
      JSON.stringify([id, ...readRecent().filter((other) => other !== id)].slice(0, RECENT_KEPT)),
    );
  } catch {
    // private window or storage blocked: recent items are only a convenience
  }
}

/** The palette's search button, for the top bar. */
export function CommandButton() {
  const t = useT(words).palette;
  const [mac, setMac] = useState(false);
  useEffect(() => setMac(isMac()), []);
  return (
    <button
      type="button"
      className="command-button"
      onClick={openCommandPalette}
      aria-label={t.open}
      aria-keyshortcuts={mac ? "Meta+K" : "Control+K"}
      title={t.open}
    >
      <Search aria-hidden="true" />
      <kbd className="hide-sm">{mac ? t.shortcutMac : t.shortcut}</kbd>
    </button>
  );
}

/** Builds the list of everything the palette can open or run, for the signed-in role. */
function useItems(): Item[] {
  const can = useCan();
  const { data: projects } = useProjects();
  const { data: instances } = useInstances();
  const t = useT(words);
  const tabs = useT(instanceWords).tabs;
  const projectTabs = useT(projectWords).page;
  const buildTab = useT(buildWords).builds.tab;
  return useMemo(() => {
    const items: Item[] = [];
    const pages: [string, string, Permission | null][] = [
      ["/projects", t.palette.pages.projects, null],
      ["/", t.palette.pages.instances, null],
      ["/new", t.palette.pages.newInstance, "instances.create"],
      ["/backups", t.palette.pages.backups, "backups.manage"],
      ["/activity", t.palette.pages.audit, "audit.view"],
      ["/settings", t.palette.pages.settings, "settings.manage"],
      ["/github", t.palette.pages.github, "github.manage"],
      ["/accounts", t.palette.pages.accounts, "accounts.manage"],
      ["/tokens", t.palette.pages.tokens, "tokens.manage"],
      ["/plugins", t.palette.pages.plugins, "plugins.view"],
      ["/help", t.palette.pages.help, null],
      ["/account", t.palette.pages.account, null],
    ];
    for (const [to, label, permission] of pages)
      if (!permission || can(permission))
        items.push({ id: `page:${to}`, group: "pages", label, keywords: [to], icon: LayoutGrid, to });

    for (const project of projects?.projects ?? []) {
      items.push({
        id: `project:${project.id}`,
        group: "projects",
        label: project.name,
        detail: project.repository.full_name,
        keywords: [project.id, project.repository.full_name],
        icon: FolderKanban,
        to: `/projects/${project.id}`,
      });
      const projectTabList: [string, string, Permission | null][] = [
        ["status", t.tabs.status, null],
        ["builds", buildTab, null],
        ["settings", projectTabs.settingsTab, null],
        ["backups", projectTabs.backupsTab, "backups.manage"],
        ["notifications", t.tabs.notifications, "projects.manage"],
      ];
      for (const [tab, label, permission] of projectTabList)
        if (label && (!permission || can(permission)))
          items.push({
            id: `project-tab:${project.id}:${tab}`,
            group: "tabs",
            label: t.palette.tabOf(label, project.name),
            keywords: [project.id, tab],
            icon: SquareArrowOutUpRight,
            to: `/projects/${project.id}/${tab}`,
          });
    }

    const branches = new Map<string, { project: string; projectName: string; branch: string; instance: string }>();
    for (const instance of instances ?? []) {
      if (instance.project && instance.git.branch) {
        const key = `${instance.project.id}:${instance.git.branch}`;
        if (!branches.has(key))
          branches.set(key, {
            project: instance.project.id,
            projectName: instance.project.name,
            branch: instance.git.branch,
            instance: instance.name,
          });
      }
      items.push({
        id: `instance:${instance.name}`,
        group: "instances",
        label: instance.name,
        detail: instance.project?.name ?? instance.git.branch ?? undefined,
        keywords: [instance.git.branch ?? "", instance.stage],
        icon: LayoutGrid,
        to: `/i/${instance.name}`,
      });
      const instanceTabs: [string, string, Permission | null][] = [
        ["logs", tabs.logs, "instances.logs"],
        ["activity", tabs.activity, null],
        ["backups", tabs.backups, null],
        ["code", tabs.code, null],
        ["settings", tabs.settings, "instances.operate"],
      ];
      for (const [tab, label, permission] of instanceTabs)
        if (!permission || can(permission))
          items.push({
            id: `instance-tab:${instance.name}:${tab}`,
            group: "tabs",
            label: t.palette.tabOf(label, instance.name),
            keywords: [tab, instance.git.branch ?? ""],
            icon: SquareArrowOutUpRight,
            to: `/i/${instance.name}/${tab}`,
          });
      // the safe actions, on an instance its branch's lock does not hold
      if (can("instances.update") && !instance.lock && !["creating", "busy"].includes(instance.status)) {
        if (instance.source !== "plain" && instance.git.repository)
          items.push({
            id: `action:update:${instance.name}`,
            group: "actions",
            label: t.palette.getLatest(instance.name),
            keywords: ["update", "pull", "git"],
            icon: DownloadCloud,
            action: { instance, kind: "update" },
          });
        if (!ASLEEP.includes(instance.status))
          items.push({
            id: `action:restart:${instance.name}`,
            group: "actions",
            label: t.palette.restart(instance.name),
            keywords: ["restart", "reboot"],
            icon: RotateCw,
            action: { instance, kind: "restart" },
          });
      }
    }
    for (const found of branches.values())
      // a branch opens on its history (as components/ProjectWorkspace.tsx branchPath writes it)
      items.push({
        id: `branch:${found.project}:${found.branch}`,
        group: "branches",
        label: t.palette.branchHistory(found.branch),
        detail: found.projectName,
        keywords: [found.branch, found.project, "history"],
        icon: GitBranch,
        to: `/projects/${found.project}/branches/${encodeURIComponent(found.branch)}`,
      });
    return items;
  }, [can, projects, instances, t, tabs, projectTabs, buildTab]);
}

const GROUP_ORDER: Group[] = ["projects", "branches", "instances", "actions", "tabs", "pages"];

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const show = useCallback(() => setOpen(true), []);
  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && !event.altKey && !event.shiftKey && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen((current) => !current);
        return;
      }
      if (event.key === "/" && !event.ctrlKey && !event.metaKey && !event.altKey && !typing(event.target)) {
        const filter = document.querySelector<HTMLInputElement>("[data-branch-filter]");
        if (filter) {
          event.preventDefault();
          filter.focus();
          filter.select();
        }
      }
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener(OPEN_EVENT, show);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener(OPEN_EVENT, show);
    };
  }, [show]);
  return open ? <Palette onClose={() => setOpen(false)} /> : null;
}

function Palette({ onClose }: { onClose: () => void }) {
  const t = useT(words).palette;
  const items = useItems();
  const navigate = useNavigate();
  const toast = useToast();
  const rtl = useDirection() === "rtl";
  const [open, setOpen] = useState(true);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [confirming, setConfirming] = useState<Item | null>(null);
  const [running, setRunning] = useState(false);
  const listId = useId();
  const list = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);

  const close = () => {
    setOpen(false);
    setTimeout(onClose, matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 160);
  };

  // nothing typed: the recent choices, then a few of each kind; typed: the best matches of every kind
  const shown = useMemo(() => {
    if (!query.trim()) {
      const byId = new Map(items.map((item) => [item.id, item]));
      const recent = readRecent()
        .map((id) => byId.get(id))
        .filter((item): item is Item => !!item);
      const seen = new Set(recent.map((item) => item.id));
      const rest = GROUP_ORDER.flatMap((group) =>
        items
          .filter((item) => item.group === group && !seen.has(item.id) && group !== "tabs" && group !== "actions")
          .slice(0, 5),
      );
      return [...recent.map((item) => ({ item, recent: true })), ...rest.map((item) => ({ item, recent: false }))];
    }
    return rank(items, query, (item) => [item.label, item.detail ?? "", ...item.keywords], 60).map((item) => ({
      item,
      recent: false,
    }));
  }, [items, query]);

  useEffect(() => setActive(0), [query]);
  useEffect(() => {
    list.current?.querySelector<HTMLElement>(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  const choose = (item: Item) => {
    remember(item.id);
    if (item.action) {
      setConfirming(item);
      return;
    }
    if (item.to) {
      close();
      navigate(item.to, { viewTransition: true });
    }
  };

  const run = async (item: Item) => {
    if (!item.action || running) return;
    setRunning(true);
    try {
      await api.post<Job>(`/instances/${item.action.instance.name}/actions/${item.action.kind}`, {});
      toast("success", t.started(item.label));
      close();
      navigate(`/i/${item.action.instance.name}/activity`, { viewTransition: true });
    } catch (error) {
      toast("error", item.label, errorText(error));
      setRunning(false);
    }
  };

  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (confirming) {
      if (event.key === "Enter") {
        event.preventDefault();
        void run(confirming);
      }
      return;
    }
    const last = shown.length - 1;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((current) => (current >= last ? 0 : current + 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((current) => (current <= 0 ? last : current - 1));
    } else if (event.key === "Home" && event.ctrlKey) {
      event.preventDefault();
      setActive(0);
    } else if (event.key === "End" && event.ctrlKey) {
      event.preventDefault();
      setActive(last);
    } else if (event.key === "Enter") {
      event.preventDefault();
      const found = shown[active];
      if (found) choose(found.item);
    }
  };

  // the groups' headings, in the order the items are listed
  let previous: string | null = null;
  const activeId = shown[active] ? `${listId}-${active}` : undefined;
  return (
    <Dialog open={open} onOpenChange={(next) => !next && close()}>
      <DialogContent
        showCloseButton={false}
        className="command-palette"
        dir={rtl ? "rtl" : "ltr"}
        onKeyDown={onKeyDown}
        onOpenAutoFocus={(event) => {
          event.preventDefault();
          input.current?.focus();
        }}
        onEscapeKeyDown={(event) => {
          if (confirming) {
            event.preventDefault();
            setConfirming(null);
            setRunning(false);
            input.current?.focus();
          }
        }}
      >
        <DialogTitle className="sr-only">{t.label}</DialogTitle>
        <DialogDescription className="sr-only">{t.placeholder}</DialogDescription>
        {confirming?.action ? (
          <div className="palette-confirm" role="alertdialog" aria-labelledby={`${listId}-confirm`}>
            <h2 id={`${listId}-confirm`}>{t.confirmTitle}</h2>
            <p className="palette-confirm-what">
              <confirming.icon aria-hidden="true" /> {confirming.label}
            </p>
            {confirming.action.instance.stage === "production" && (
              <p className="small palette-warning">{t.confirmProduction}</p>
            )}
            <p className="xsmall muted">{t.confirmText}</p>
            <div className="row" style={{ gap: 8, justifyContent: "flex-end" }}>
              <button type="button" className="palette-button" onClick={() => setConfirming(null)}>
                {t.back}
              </button>
              <button
                type="button"
                className="palette-button primary"
                autoFocus
                disabled={running}
                onClick={() => void run(confirming)}
              >
                {running ? <span className="spinner" /> : null}
                {t.run}
              </button>
            </div>
          </div>
        ) : (
          <>
            <div className="palette-search">
              <Search aria-hidden="true" />
              <input
                ref={input}
                role="combobox"
                aria-expanded="true"
                aria-controls={listId}
                aria-activedescendant={activeId}
                aria-autocomplete="list"
                aria-label={t.label}
                placeholder={t.placeholder}
                value={query}
                spellCheck={false}
                autoComplete="off"
                onChange={(e) => setQuery(e.target.value)}
              />
              <kbd>Esc</kbd>
            </div>
            <div className="palette-list" id={listId} role="listbox" aria-label={t.label} ref={list}>
              {!shown.length && <p className="palette-empty">{t.empty}</p>}
              {shown.map(({ item, recent }, index) => {
                const heading = recent ? t.recent : t.groups[item.group];
                const head = heading !== previous ? heading : null;
                previous = heading;
                const Icon = recent ? History : item.icon;
                return (
                  <div key={`${recent ? "recent:" : ""}${item.id}`} role="presentation">
                    {head && (
                      <div className="palette-group" role="presentation">
                        {head}
                      </div>
                    )}
                    <div
                      id={`${listId}-${index}`}
                      role="option"
                      aria-selected={index === active}
                      data-index={index}
                      className="palette-item"
                      onPointerMove={() => index !== active && setActive(index)}
                      onClick={() => choose(item)}
                    >
                      <Icon aria-hidden="true" />
                      <span className="palette-label truncate">
                        <bdi>{item.label}</bdi>
                      </span>
                      {item.detail && (
                        <span className="palette-detail truncate">
                          <bdi>{item.detail}</bdi>
                        </span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
            <div className="palette-foot xsmall muted" aria-hidden="true">
              <span>
                <kbd>↑</kbd>
                <kbd>↓</kbd> {t.hints.move}
              </span>
              <span>
                <kbd>↵</kbd> {t.hints.open}
              </span>
              <span>
                <kbd>Esc</kbd> {t.hints.close}
              </span>
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
