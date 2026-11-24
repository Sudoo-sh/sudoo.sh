// The module picker of a new instance: which of Odoo's apps its new database gets besides the repository's modules
// (those follow the "Install the repository's modules" switch). The standard apps of the version and edition
// (GET /odoo-apps), any other technical names typed in (an OCA module of a submodule, a standard module not listed),
// the chosen ones as chips, and a shortcut to clear them. Odoo installs the dependencies itself, so none are worked
// out here.
//
// useModuleChoice holds the choice; `request` is what the creation sends: {"modules": [...]}.

import { useQuery } from "@tanstack/react-query";
import { Package, Search, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "@/api/client";
import type { OdooApp } from "@/api/types";
import { Button, Callout, Field, Skeleton, Tag } from "@/components/app/basics";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { useT } from "@/i18n";
import words from "@/i18n/modules";

const NAME = /^[a-z0-9_]{1,64}$/;
const DEFAULT_MAX = 200;

export type ModuleRequest = { modules: string[] };

export interface ModuleChoice {
  apps: OdooApp[];
  loading: boolean;
  selected: string[];
  max: number;
  tooMany: boolean;
  toggle: (name: string, on: boolean) => void;
  add: (names: string[]) => void;
  none: () => void;
  request: ModuleRequest;
}

/** The Odoo apps (and other modules) chosen for a new instance of Odoo `version` and `edition`. */
export function useModuleChoice({ version, edition }: { version: string; edition: string }): ModuleChoice {
  const apps = useQuery({
    queryKey: ["odoo-apps", version, edition],
    queryFn: () => api.page<OdooApp[]>(`/odoo-apps?version=${encodeURIComponent(version)}&edition=${edition}`),
    enabled: !!version,
    staleTime: Infinity,
  });
  const [selected, setSelected] = useState<string[]>([]);

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
  const change = (next: (current: string[]) => string[]) => setSelected((current) => [...new Set(next(current))]);
  return {
    apps: offered ?? [],
    loading: !!version && apps.isLoading,
    selected,
    max,
    tooMany: selected.length > max,
    toggle: (name, on) => change((current) => (on ? [...current, name] : current.filter((n) => n !== name))),
    add: (names) => change((current) => [...current, ...names]),
    none: () => change(() => []),
    request: { modules: selected },
  };
}

function matches(query: string, ...texts: string[]) {
  const q = query.trim().toLowerCase();
  return !q || texts.some((text) => text.toLowerCase().includes(q));
}

function Row({
  name,
  title,
  checked,
  onChange,
  tags,
}: {
  name: string;
  title: string;
  checked: boolean;
  onChange: (on: boolean) => void;
  tags?: ReactNode;
}) {
  return (
    <label className="mp-row">
      <Checkbox checked={checked} onCheckedChange={(v) => onChange(v === true)} />
      <bdi dir="ltr" className="mp-name mono">
        {name}
      </bdi>
      {title && title !== name && (
        <span className="mp-title truncate">
          <bdi>{title}</bdi>
        </span>
      )}
      {tags}
    </label>
  );
}

/** The picker itself, for a choice made by useModuleChoice. `note`: a line under the chips (a backup's modules). */
export function ModulePicker({ choice, note }: { choice: ModuleChoice; note?: string }) {
  const t = useT(words);
  const [query, setQuery] = useState("");
  const [other, setOther] = useState("");
  const [otherError, setOtherError] = useState("");
  const chosen = useMemo(() => new Set(choice.selected), [choice.selected]);
  const listed = new Set(choice.apps.map((a) => a.name));
  const others = choice.selected.filter((name) => !listed.has(name));

  const addOther = () => {
    const names = other
      .split(/[\s,]+/)
      .map((n) => n.trim())
      .filter(Boolean);
    const wrong = names.find((n) => !NAME.test(n));
    if (wrong) return setOtherError(t.wrongName(wrong));
    setOtherError("");
    if (names.length) choice.add(names);
    setOther("");
  };

  const shownApps = choice.apps.filter((a) => matches(query, a.name, a.title));
  const shownOthers = others.filter((name) => matches(query, name));

  return (
    <div className="module-picker stack">
      <div className="mp-head">
        <span className="small muted" aria-live="polite">
          {t.selected(choice.selected.length)}
        </span>
        <span className="spacer" />
        <Button size="sm" variant="ghost" disabled={!choice.selected.length} onClick={choice.none}>
          {t.none}
        </Button>
      </div>

      {choice.selected.length > 0 ? (
        <div className="row-wrap mp-chips">
          {choice.selected.map((name) => (
            <button
              key={name}
              type="button"
              className="chip"
              aria-label={t.remove(name)}
              title={t.remove(name)}
              onClick={() => choice.toggle(name, false)}
            >
              <bdi dir="ltr" className="mono">
                {name}
              </bdi>{" "}
              <X />
            </button>
          ))}
        </div>
      ) : (
        <p className="xsmall faint">{t.empty}</p>
      )}
      {choice.tooMany && <Callout tone="warning">{t.tooMany(choice.max)}</Callout>}
      {note && <p className="xsmall muted">{note}</p>}

      <div className="input-group">
        <Search />
        <Input
          type="search"
          placeholder={t.search}
          aria-label={t.search}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      <div className="mp-list" role="group" aria-label={t.title}>
        <section aria-label={t.odoo}>
          <h3 className="mp-group">
            <Package aria-hidden="true" /> {t.odoo}
          </h3>
          {choice.loading ? (
            <div className="stack-sm" aria-busy="true">
              <Skeleton height={14} width="60%" />
              <Skeleton height={14} width="45%" />
            </div>
          ) : (
            shownApps.map((a) => (
              <Row
                key={a.name}
                name={a.name}
                title={a.title}
                checked={chosen.has(a.name)}
                onChange={(on) => choice.toggle(a.name, on)}
                tags={a.enterprise ? <Tag>{t.enterprise}</Tag> : null}
              />
            ))
          )}
          {!choice.loading && choice.apps.length > 0 && !shownApps.length && (
            <p className="xsmall faint">{t.noMatch}</p>
          )}
        </section>
        {shownOthers.length > 0 && (
          <section aria-label={t.other}>
            <h3 className="mp-group">{t.other}</h3>
            {shownOthers.map((name) => (
              <Row key={name} name={name} title="" checked onChange={(on) => choice.toggle(name, on)} />
            ))}
          </section>
        )}
      </div>

      <Field label={t.otherLabel} hint={t.otherHint} error={otherError}>
        <div className="row" style={{ gap: 8 }}>
          <Input
            dir="ltr"
            placeholder={t.otherPlaceholder}
            aria-label={t.otherLabel}
            value={other}
            aria-invalid={!!otherError}
            onChange={(e) => setOther(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                addOther();
              }
            }}
          />
          <Button onClick={addOther} disabled={!other.trim()}>
            {t.add}
          </Button>
        </div>
      </Field>
      <p className="xsmall faint">{t.hint}</p>
    </div>
  );
}
