// The command toolbar of a project's branch, the Odoo.sh way: a tab per task (Clone, Fork, Merge, SSH, SQL,
// Submodule, Delete) and one command for this branch under it, ready to copy, with the panel's own action beside it
// where there is one (the web shell, Add submodule, Delete instance). The tab chosen is kept for the session.

import { Check, Copy, Plus, SquareTerminal, Trash2 } from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Link } from "react-router";
import type { Instance, ProjectBranch, ProjectDetail, Stage } from "@/api/types";
import type { BranchDialog } from "@/components/BranchDialogs";
import { Button } from "@/components/app/basics";
import { SelectBox } from "@/components/app/select";
import { Input } from "@/components/ui/input";
import { useT } from "@/i18n";
import words from "@/i18n/branch-commands";
import instanceWords from "@/i18n/instance";
import { SubmoduleDialog, useRun } from "@/pages/Instance";

const TABS = ["clone", "fork", "merge", "ssh", "sql", "submodule", "delete"] as const;
type Tab = (typeof TABS)[number];
const KEY = "sudoo.branch-command";
const BRANCH_NAME = /^(?!-)(?!.*\.\.)[A-Za-z0-9._/-]{1,200}$/;

function remembered(): Tab {
  try {
    const value = sessionStorage.getItem(KEY);
    return (TABS as readonly string[]).includes(value ?? "") ? (value as Tab) : "clone";
  } catch {
    return "clone";
  }
}

/** The branch merged by default: production into development and staging, staging into production. */
function mergeDefault(branches: ProjectBranch[], branch: ProjectBranch): string {
  const others = branches.filter((b) => b.name !== branch.name && b.on_github !== false);
  const wanted: Stage = branch.stage === "production" ? "staging" : "production";
  return others.find((b) => b.stage === wanted)?.name ?? others[0]?.name ?? "";
}

/** The command, read-only and whole (it scrolls sideways when long), and its copy button. */
function CommandField({ command, example }: { command: string; example?: boolean }) {
  const t = useT(words);
  const [copied, setCopied] = useState(false);
  const timer = useRef<number>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const copy = async () => {
    await navigator.clipboard.writeText(command);
    setCopied(true);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setCopied(false), 1400);
  };
  return (
    <div className="bc-field">
      {example && <span className="bc-example">{t.example}</span>}
      <code dir="ltr" tabIndex={0}>{command}</code>
      <Button size="sm" variant="ghost" icon={copied ? <Check /> : <Copy />} onClick={copy} aria-label={t.copy} title={copied ? t.copied : t.copy} />
    </div>
  );
}

export function BranchCommands({
  project,
  branch,
  instance,
  can,
  shellLink,
  onDialog,
  onJob,
}: {
  project: ProjectDetail;
  branch: ProjectBranch;
  instance: Instance | undefined;
  can: (permission: "instances.shell" | "instances.update" | "instances.delete") => boolean;
  /** the instance's Shell tab in this branch */
  shellLink: string | null;
  onDialog: (dialog: BranchDialog) => void;
  onJob: (id: string) => void;
}) {
  const t = useT(words);
  const j = useT(instanceWords).jobs;
  const [tab, setTab] = useState<Tab>(remembered);
  const [forkName, setForkName] = useState("");
  const others = project.branches_list.filter((b) => b.name !== branch.name && b.on_github !== false).map((b) => b.name);
  const [mergeFrom, setMergeFrom] = useState(() => mergeDefault(project.branches_list, branch));
  const [adding, setAdding] = useState(false);
  const run = useRun(instance?.name ?? "", onJob);
  const tabs = useRef<HTMLDivElement>(null);

  const choose = (next: Tab) => {
    setTab(next);
    try {
      sessionStorage.setItem(KEY, next);
    } catch {
      // private windows: the choice lasts for the page only
    }
  };
  // the arrows move between the tabs (in the reading direction), as in any tab list
  const onKey = (event: KeyboardEvent<HTMLDivElement>) => {
    const rtl = getComputedStyle(event.currentTarget).direction === "rtl";
    const step = { ArrowRight: rtl ? -1 : 1, ArrowLeft: rtl ? 1 : -1, Home: -99, End: 99 }[event.key];
    if (!step) return;
    event.preventDefault();
    const at = TABS.indexOf(tab);
    const next = TABS[Math.min(TABS.length - 1, Math.max(0, Math.abs(step) === 99 ? (step < 0 ? 0 : TABS.length - 1) : at + step))]!;
    choose(next);
    tabs.current?.querySelector<HTMLElement>(`[data-tab="${next}"]`)?.focus();
  };

  const repository = `${project.repository.url}.git`;
  const typed = forkName.trim();
  const newBranch = BRANCH_NAME.test(typed) ? typed : "new-branch";
  const names = instance?.containers;
  const shell = shellLink && can("instances.shell") && (
    <Button asChild size="sm"><Link to={shellLink} viewTransition><SquareTerminal /> {t.webShell}</Link></Button>
  );

  // SSH and SQL need an instance
  const visible = TABS.filter((name) => instance || (name !== "ssh" && name !== "sql"));
  const shown = visible.includes(tab) ? tab : "clone";
  let body;
  switch (shown) {
    case "clone":
      body = (
        <>
          <p className="bc-text">{t.clone}</p>
          <CommandField command={`git clone --recurse-submodules --branch ${branch.name} ${repository}`} />
        </>
      );
      break;
    case "fork":
      body = (
        <>
          <p className="bc-text">{t.fork}</p>
          <div className="bc-inputs">
            <Input dir="ltr" aria-label={t.forkName} placeholder="new-branch" value={forkName} maxLength={200} onChange={(e) => setForkName(e.target.value)}
              aria-invalid={typed && !BRANCH_NAME.test(typed) ? true : undefined} />
          </div>
          <CommandField example command={`git checkout -b ${newBranch} origin/${branch.name} && git push -u origin ${newBranch}`} />
        </>
      );
      break;
    case "merge":
      body = (
        <>
          <p className="bc-text">{t.merge}</p>
          {others.length > 0 && (
            <div className="bc-inputs">
              <SelectBox dir="ltr" size="sm" aria-label={t.mergeFrom} value={mergeFrom} onValueChange={setMergeFrom}>
                {others.map((name) => <option key={name} value={name}>{name}</option>)}
              </SelectBox>
            </div>
          )}
          <CommandField example command={`git merge ${mergeFrom || "<branch>"} && git push -u origin ${branch.name}`} />
        </>
      );
      break;
    case "ssh":
      body = names ? (
        <>
          <p className="bc-text">{t.ssh} {!names.host && t.sshNoHost}</p>
          <CommandField command={`ssh ${names.host ?? "<server>"} docker exec -it ${names.odoo} bash`} />
          {shell && <div className="bc-actions">{shell}</div>}
        </>
      ) : (
        <p className="bc-text">{t.noNames}</p>
      );
      break;
    case "sql":
      body = names ? (
        <>
          <p className="bc-text">{t.sql}</p>
          <CommandField command={`docker exec -it ${names.db} psql -U ${names.db_user} ${instance!.odoo.database}`} />
          {shell && <div className="bc-actions">{shell}</div>}
        </>
      ) : (
        <p className="bc-text">{t.noNames}</p>
      );
      break;
    case "submodule":
      body = (
        <>
          <p className="bc-text">{t.submodule}</p>
          <CommandField example command={`git submodule add -b ${instance?.odoo.version ?? branch.odoo_version ?? project.odoo.version} https://github.com/OCA/web.git oca/web && git commit -m "Add OCA web" && git push`} />
          {instance && can("instances.update") && (
            <div className="bc-actions">
              <Button size="sm" icon={<Plus />} disabled={!!instance.current_job} onClick={() => setAdding(true)}>{t.addSubmodule}</Button>
            </div>
          )}
        </>
      );
      break;
    case "delete":
      body = (
        <>
          {instance && <p className="bc-text">{t.deleteText(instance.name)}</p>}
          <CommandField command={`git push origin --delete ${branch.name}`} />
          {instance && can("instances.delete") && (
            <div className="bc-actions">
              <Button size="sm" variant="danger" icon={<Trash2 />} onClick={() => onDialog({ kind: "delete", instance })}>{t.deleteInstance}</Button>
            </div>
          )}
        </>
      );
      break;
  }

  return (
    <div className="bc" role="group" aria-label={t.label}>
      <div className="bc-tabs" role="tablist" aria-label={t.label} ref={tabs} onKeyDown={onKey}>
        {visible.map((name) => (
          <button key={name} type="button" role="tab" data-tab={name} id={`bc-tab-${name}`} aria-selected={shown === name} aria-controls="bc-panel"
            tabIndex={shown === name ? 0 : -1} className={name === "delete" ? "danger" : undefined} onClick={() => choose(name)}>
            {t.tabs[name]}
          </button>
        ))}
      </div>
      <div className="bc-panel" id="bc-panel" role="tabpanel" aria-labelledby={`bc-tab-${shown}`} key={shown}>
        {body}
      </div>
      {adding && instance && (
        <SubmoduleDialog version={instance.odoo.version} onClose={() => setAdding(false)}
          onRun={(payload) => run.mutate({ action: "add-submodule", title: j.addSubmodule, body: payload })} />
      )}
    </div>
  );
}
