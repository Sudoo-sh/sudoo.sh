// The module picker of a new instance, as on Odoo.sh: which modules its new database gets. Three groups (the
// branch's own modules, read without creating anything by GET /repositories/modules; the standard Odoo apps of the
// version and edition, GET /odoo-apps; any other technical names typed in), the chosen ones as chips, and two
// shortcuts. Odoo installs the dependencies itself, so none are worked out here.
//
// useModuleChoice holds the choice; `request` is what the creation sends: {"modules": [...]} once the list is known or
// someone changed the selection, else `fallback` (the panel's own choice, as before the picker existed).

import { useQuery } from "@tanstack/react-query";
import { Package, RotateCw, Search, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, errorText } from "@/api/client";
import type { OdooApp, RepositoryModule, RepositoryModules } from "@/api/types";
import { Button, Callout, Field, Skeleton, Tag } from "@/components/app/basics";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { useT } from "@/i18n";
import words from "@/i18n/modules";

const NAME = /^[a-z0-9_]{1,64}$/;
const DEFAULT_MAX = 200;

export type ModuleRequest = { modules?: string[]; install_modules?: boolean };

export interface ModuleChoice {
  /** the repository's address, "" for plain Odoo */
  repo: string;
  repository: { data?: RepositoryModules; loading: boolean; error: string; retry: () => void };
  apps: OdooApp[];
  selected: string[];
  touched: boolean;
  max: number;
  tooMany: boolean;
  toggle: (name: string, on: boolean) => void;
  add: (names: string[]) => void;
  allRepository: () => void;
  none: () => void;
  request: ModuleRequest;
}

/** The choice of modules for a new instance of `repo` (a GitHub address, "" for plain Odoo) at `branch`. `fallback`:
 *  what is sent while the branch's modules are unknown and nobody changed the selection. */
export function useModuleChoice({ repo, branch, version, edition, fallback = {} }: { repo: string; branch: string; version: string; edition: string; fallback?: ModuleRequest }): ModuleChoice {
  const key = repo && branch ? `${repo}@${branch}` : "";
  const repository = useQuery({
    queryKey: ["repository-modules", repo, branch],
    queryFn: () => api.get<RepositoryModules>(`/repositories/modules?url=${encodeURIComponent(repo)}&branch=${encodeURIComponent(branch)}`),
    enabled: !!key,
    staleTime: 60_000,
    retry: false,
  });
  const apps = useQuery({
    queryKey: ["odoo-apps", version, edition],
    queryFn: () => api.page<OdooApp[]>(`/odoo-apps?version=${encodeURIComponent(version)}&edition=${edition}`),
    enabled: !!version,
    staleTime: Infinity,
  });
  const [selected, setSelected] = useState<string[]>([]);
  const [touched, setTouched] = useState(false);
  const seeded = useRef("");
  const seededModules = useRef<Set<string>>(new Set());

  // another repository or branch (or none): the previous branch's modules leave the selection, the Odoo apps and
  // other names stay; once the new branch's modules are known, those it selects first join them
  useEffect(() => {
    if (seeded.current && seeded.current !== key) {
      const gone = seededModules.current;
      setSelected((current) => current.filter((name) => !gone.has(name)));
      seeded.current = "";
      seededModules.current = new Set();
    }
    if (!key || !repository.data || seeded.current === key) return;
    const found = repository.data;
    seeded.current = key;
    seededModules.current = new Set([...found.modules.map((m) => m.name), ...found.default.modules]);
    setSelected((current) => [...new Set([...found.default.modules, ...current])]);
    setTouched(false);
  }, [key, repository.data]);

  // another edition or version: apps it does not offer leave the selection
  const offered = apps.data?.data;
  const lastOffered = useRef<OdooApp[] | undefined>(undefined);
  useEffect(() => {
    if (!offered) return;
    const before = lastOffered.current;
    lastOffered.current = offered;
    if (!before) return;
    const now = new Set(offered.map((a) => a.name));
    const gone = new Set(before.filter((a) => !now.has(a.name)).map((a) => a.name));
    if (gone.size) setSelected((current) => current.filter((name) => !gone.has(name)));
  }, [offered]);

  const max = Number(apps.data?.meta?.max_modules ?? DEFAULT_MAX);
  const change = (next: string[]) => {
    setSelected([...new Set(next)]);
    setTouched(true);
  };
  const known = !key || !!repository.data;
  let request: ModuleRequest;
  if (key && !known && !touched) request = fallback;
  else if (repository.data?.default.source === "project_all" && !touched) request = {}; // the project's "every module"
  else request = { modules: selected };

  return {
    repo,
    repository: {
      data: key ? repository.data : undefined,
      loading: !!key && repository.isLoading,
      error: key && repository.error ? errorText(repository.error) : "",
      retry: () => void repository.refetch(),
    },
    apps: offered ?? [],
    selected,
    touched,
    max,
    tooMany: selected.length > max,
    toggle: (name, on) => change(on ? [...selected, name] : selected.filter((n) => n !== name)),
    add: (names) => change([...selected, ...names]),
    allRepository: () => change([...selected, ...(repository.data?.modules ?? []).filter((m) => !m.submodule).map((m) => m.name)]),
    none: () => change([]),
    request,
  };
}

function matches(query: string, ...texts: string[]) {
  const q = query.trim().toLowerCase();
  return !q || texts.some((text) => text.toLowerCase().includes(q));
}

function Row({ name, title, checked, onChange, tags }: { name: string; title: string; checked: boolean; onChange: (on: boolean) => void; tags?: ReactNode }) {
  return (
    <label className="mp-row">
      <Checkbox checked={checked} onCheckedChange={(v) => onChange(v === true)} />
      <bdi dir="ltr" className="mp-name mono">{name}</bdi>
      {title && title !== name && <span className="mp-title truncate"><bdi>{title}</bdi></span>}
      {tags}
    </label>
  );
}

/** The picker itself, for a choice made by useModuleChoice. `note`: a line under the title (a backup's modules). */
export function ModulePicker({ choice, note }: { choice: ModuleChoice; note?: string }) {
  const t = useT(words);
  const [query, setQuery] = useState("");
  const [other, setOther] = useState("");
  const [otherError, setOtherError] = useState("");
  const chosen = useMemo(() => new Set(choice.selected), [choice.selected]);
  const data = choice.repository.data;
  const own = (data?.modules ?? []).filter((m) => !m.submodule);
  const bySubmodule = useMemo(() => {
    const groups = new Map<string, RepositoryModule[]>();
    for (const m of data?.modules ?? []) if (m.submodule) groups.set(m.submodule, [...(groups.get(m.submodule) ?? []), m]);
    return [...groups.entries()];
  }, [data]);
  const listed = new Set([...(data?.modules ?? []).map((m) => m.name), ...choice.apps.map((a) => a.name)]);
  const others = choice.selected.filter((name) => !listed.has(name));

  const addOther = () => {
    const names = other.split(/[\s,]+/).map((n) => n.trim()).filter(Boolean);
    const wrong = names.find((n) => !NAME.test(n));
    if (wrong) return setOtherError(t.wrongName(wrong));
    setOtherError("");
    if (names.length) choice.add(names);
    setOther("");
  };

  const shownOwn = own.filter((m) => matches(query, m.name, m.title));
  const shownApps = choice.apps.filter((a) => matches(query, a.name, a.title));
  const shownSubmodules = bySubmodule.map(([path, modules]) => [path, modules.filter((m) => matches(query, m.name, m.title))] as const).filter(([, m]) => m.length);
  const source = data?.default.source;

  return (
    <div className="module-picker stack">
      <div className="mp-head">
        <span className="small muted" aria-live="polite">{t.selected(choice.selected.length)}</span>
        <span className="spacer" />
        {choice.repo && <Button size="sm" variant="ghost" disabled={!own.length} onClick={choice.allRepository}>{t.allRepository}</Button>}
        <Button size="sm" variant="ghost" disabled={!choice.selected.length} onClick={choice.none}>{t.none}</Button>
      </div>

      {choice.selected.length > 0 ? (
        <div className="row-wrap mp-chips">
          {choice.selected.map((name) => (
            <button key={name} type="button" className="chip" aria-label={t.remove(name)} title={t.remove(name)} onClick={() => choice.toggle(name, false)}>
              <bdi dir="ltr" className="mono">{name}</bdi> <X />
            </button>
          ))}
        </div>
      ) : (
        <p className="xsmall faint">{t.empty}</p>
      )}
      {choice.tooMany && <Callout tone="warning">{t.tooMany(choice.max)}</Callout>}
      {source === "project_all" && !choice.touched && <p className="xsmall muted">{t.projectAll}</p>}
      {source === "project_list" && !choice.touched && <p className="xsmall muted">{t.projectList}</p>}
      {note && <p className="xsmall muted">{note}</p>}

      <div className="input-group">
        <Search />
        <Input type="search" placeholder={t.search} aria-label={t.search} value={query} onChange={(e) => setQuery(e.target.value)} />
      </div>

      <div className="mp-list" role="group" aria-label={t.title}>
        {choice.repo && (
          <section aria-label={t.repository}>
            <h3 className="mp-group">{t.repository}</h3>
            {choice.repository.loading ? (
              <div className="stack-sm" aria-busy="true">
                <span className="xsmall faint">{t.loading}</span>
                <Skeleton height={14} width="60%" />
                <Skeleton height={14} width="45%" />
              </div>
            ) : choice.repository.error ? (
              <Callout tone="warning">
                {t.notRead} {choice.repository.error} {!choice.touched && t.fallback}{" "}
                <Button size="sm" icon={<RotateCw />} onClick={choice.repository.retry}>{t.retry}</Button>
              </Callout>
            ) : !data ? (
              <p className="xsmall faint">{t.chooseBranch}</p>
            ) : !data.modules.length ? (
              <p className="xsmall faint">{t.noModules}</p>
            ) : (
              <>
                {shownOwn.map((m) => (
                  <Row key={m.name} name={m.name} title={m.title} checked={chosen.has(m.name)} onChange={(on) => choice.toggle(m.name, on)}
                    tags={m.application ? <Tag tone="accent">{t.app}</Tag> : null} />
                ))}
                {shownSubmodules.map(([path, modules]) => (
                  <div key={path} className="mp-sub">
                    <div className="xsmall faint">{t.submodule(path)}</div>
                    {modules.map((m) => (
                      <Row key={m.name} name={m.name} title={m.title} checked={chosen.has(m.name)} onChange={(on) => choice.toggle(m.name, on)}
                        tags={m.application ? <Tag tone="accent">{t.app}</Tag> : null} />
                    ))}
                  </div>
                ))}
                {!shownOwn.length && !shownSubmodules.length && <p className="xsmall faint">{t.noMatch}</p>}
                {data.problems.map((problem) => <p key={problem} className="xsmall faint">{problem}</p>)}
              </>
            )}
          </section>
        )}
        <section aria-label={t.odoo}>
          <h3 className="mp-group"><Package aria-hidden="true" /> {t.odoo}</h3>
          {shownApps.map((a) => (
            <Row key={a.name} name={a.name} title={a.title} checked={chosen.has(a.name)} onChange={(on) => choice.toggle(a.name, on)}
              tags={a.enterprise ? <Tag>{t.enterprise}</Tag> : null} />
          ))}
          {choice.apps.length > 0 && !shownApps.length && <p className="xsmall faint">{t.noMatch}</p>}
        </section>
        {others.length > 0 && (
          <section aria-label={t.other}>
            <h3 className="mp-group">{t.other}</h3>
            {others.map((name) => <Row key={name} name={name} title="" checked onChange={(on) => choice.toggle(name, on)} />)}
          </section>
        )}
      </div>

      <Field label={t.otherLabel} hint={t.otherHint} error={otherError}>
        <div className="row" style={{ gap: 8 }}>
          <Input dir="ltr" placeholder={t.otherPlaceholder} aria-label={t.otherLabel} value={other} aria-invalid={!!otherError}
            onChange={(e) => setOther(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addOther(); } }} />
          <Button onClick={addOther} disabled={!other.trim()}>{t.add}</Button>
        </div>
      </Field>
      <p className="xsmall faint">{t.hint}</p>
    </div>
  );
}
