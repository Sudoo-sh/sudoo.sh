// Teams: named groups of accounts that project and branch access is given to (src/sudoo/teams.py). Administrators
// create them here; the project's Access tab names them in its grants and the branch locks allow them.

import { Ellipsis, Pencil, Plus, Search, Trash2, UsersRound } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router";
import { api, errorText } from "@/api/client";
import { keys, useCall, useTeams, useUsers } from "@/api/queries";
import type { Team } from "@/api/types";
import { FeatureUpsell } from "@/components/PlanSettings";
import { Page, PageHead } from "@/components/Shell";
import { Button, Empty, Field, Skeleton, Tag } from "@/components/ui/basics";
import { Menu, Modal, useToast } from "@/components/ui/overlay";
import { countIn, useLocale, useT } from "@/i18n";
import common from "@/i18n/common";
import words from "@/i18n/teams";
import { ago } from "@/lib/format";

/** The accounts to choose from, with a search: a list of check boxes. */
export function AccountPicker({ chosen, onChange }: { chosen: string[]; onChange: (names: string[]) => void }) {
  const t = useT(words).teams;
  const roles = useT(common).roles;
  const { data: users, isPending } = useUsers();
  const [query, setQuery] = useState("");
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (users ?? []).filter((u) => !q || u.name.toLowerCase().includes(q) || u.display_name.toLowerCase().includes(q));
  }, [users, query]);
  if (isPending) return <Skeleton height={120} />;
  const toggle = (name: string, on: boolean) => onChange(on ? [...chosen, name].sort() : chosen.filter((n) => n !== name));
  return (
    <div className="pick-list">
      <label className="input-group pick-search">
        <Search />
        <input className="input" type="search" placeholder={t.search} value={query} onChange={(e) => setQuery(e.target.value)} />
      </label>
      <div className="pick-items" role="group" aria-label={t.members}>
        {shown.map((user) => (
          <label key={user.name} className="pick-item">
            <input type="checkbox" checked={chosen.includes(user.name)} onChange={(e) => toggle(user.name, e.target.checked)} />
            <span className="stack-xs spacer">
              <bdi className="truncate">{user.display_name}</bdi>
              <span className="xsmall faint"><bdi dir="ltr">{user.name}</bdi> · {roles[user.role as keyof typeof roles] ?? user.role_label}</span>
            </span>
          </label>
        ))}
      </div>
    </div>
  );
}

function TeamDialog({ team, onClose }: { team: Team | null; onClose: () => void }) {
  const t = useT(words).teams;
  const c = useT(common).common;
  const toast = useToast();
  const [name, setName] = useState(team?.name ?? "");
  const [description, setDescription] = useState(team?.description ?? "");
  const [members, setMembers] = useState<string[]>(team?.members.map((m) => m.name) ?? []);
  const save = useCall(
    () => {
      const body = { name: name.trim(), description: description.trim(), members };
      return team ? api.patch<Team>(`/teams/${team.id}`, body) : api.post<Team>("/teams", body);
    },
    [keys.teams, keys.projects],
  );
  return (
    <Modal
      title={team ? t.editTitle(team.name) : t.newTitle}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>{c.cancel}</Button>
          <Button variant="primary" loading={save.isPending} disabled={!name.trim()}
            onClick={() => save.mutate(undefined, {
              onSuccess: (made) => { toast("success", team ? t.saved(made.name) : t.created(made.name)); onClose(); },
              onError: (e) => toast("error", t.notSaved, errorText(e)),
            })}>
            {team ? c.save : c.create}
          </Button>
        </>
      }
    >
      <div className="stack">
        <Field label={t.name} hint={t.nameHint}>
          <input className="input" dir="auto" autoFocus maxLength={60} value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label={t.description} hint={t.descriptionHint}>
          <input className="input" dir="auto" maxLength={200} value={description} onChange={(e) => setDescription(e.target.value)} />
        </Field>
        <Field label={t.members} hint={t.membersHint}>
          <AccountPicker chosen={members} onChange={setMembers} />
        </Field>
      </div>
    </Modal>
  );
}

export function TeamsPage() {
  const t = useT(words).teams;
  const locale = useLocale();
  const toast = useToast();
  const { data: teams, isPending } = useTeams();
  const [editing, setEditing] = useState<Team | null | "new">(null);
  const remove = useCall((id: string) => api.del(`/teams/${id}`), [keys.teams, keys.projects]);
  return (
    <Page>
      <PageHead title={t.title} actions={<Button variant="primary" icon={<Plus />} onClick={() => setEditing("new")}>{t.new}</Button>}>
        {t.lead}
      </PageHead>
      <FeatureUpsell feature="teams" />
      <div className="card rise">
        {isPending ? (
          <div className="card-body"><Skeleton height={120} /></div>
        ) : !teams?.length ? (
          <Empty icon={<UsersRound />} title={t.none} action={<Button icon={<Plus />} onClick={() => setEditing("new")}>{t.createOne}</Button>}>
            {t.noneText}
          </Empty>
        ) : (
          <div className="table-wrap">
            <table className="table teams-table">
              <thead>
                <tr><th>{t.columns.team}</th><th>{t.columns.members}</th><th>{t.columns.projects}</th><th>{t.columns.changed}</th><th /></tr>
              </thead>
              <tbody>
                {teams.map((team) => (
                  <tr key={team.id}>
                    <td>
                      <div><bdi>{team.name}</bdi></div>
                      {team.description && <div className="xsmall faint"><bdi>{team.description}</bdi></div>}
                    </td>
                    <td>
                      <div className="small">{countIn(locale, team.members.length, t.memberCount)}</div>
                      <div className="xsmall faint truncate">{team.members.length ? team.members.map((m) => m.display_name).join(", ") : t.noMembers}</div>
                    </td>
                    <td>
                      {team.projects.length ? (
                        <div className="row-wrap">
                          {team.projects.map((p) => (
                            <Link key={p.id} to={`/projects/${p.id}/access`} viewTransition><Tag>{p.name}</Tag></Link>
                          ))}
                        </div>
                      ) : (
                        <span className="small faint">{t.notUsed}</span>
                      )}
                    </td>
                    <td className="small muted">{ago(team.changed_at)} · <bdi>{team.changed_by}</bdi></td>
                    <td className="actions">
                      <Menu trigger={(toggle) => <Button size="sm" variant="ghost" icon={<Ellipsis />} onClick={toggle} aria-label={t.actionsFor(team.name)} />}>
                        {(close) => (
                          <>
                            <button type="button" className="menu-item" onClick={() => { close(); setEditing(team); }}><Pencil /> {t.edit}</button>
                            <button type="button" className="menu-item danger"
                              onClick={() => {
                                close();
                                if (confirm(t.confirmDelete(team.name)))
                                  remove.mutate(team.id, {
                                    onSuccess: () => toast("success", t.deleted(team.name)),
                                    onError: (e) => toast("error", t.notDeleted, errorText(e)),
                                  });
                              }}>
                              <Trash2 /> {t.delete}
                            </button>
                          </>
                        )}
                      </Menu>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {editing && <TeamDialog team={editing === "new" ? null : editing} onClose={() => setEditing(null)} />}
    </Page>
  );
}
