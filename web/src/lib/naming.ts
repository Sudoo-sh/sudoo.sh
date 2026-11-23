// Names (and Odoo series) worked out the way the server works them out, so a field can show the name the server
// would give when it is left empty.
// Instance names are not here: they must be unique on the server, so GET /instance-plan gives them (usePlan).

const REPO_ID = /^[a-z0-9_.-]+\/[a-z0-9_.-]+$/;

/** "Owner/Repo" as written in a GitHub address or "owner/repo" text, else null (src/sudoo/repos/github.py
 *  repo_name). */
export function repositoryName(address: string): string | null {
  const text = address.trim();
  const match = /^(?:https:\/\/github\.com\/|git@github\.com:)(.+?)(?:\.git)?\/?$/.exec(text) ?? /^([A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+)$/.exec(text);
  const name = match?.[1] ?? "";
  return REPO_ID.test(name.toLowerCase()) && !name.includes("..") ? name : null;
}

/** The Odoo series of a version: "17.0" for "17.0", "17.0+e" or "17.0-20240101", "17.1" for "saas~17.1"; null when
 *  it is not a version (src/sudoo/instances/backups.py series). Data only moves between instances of one series. */
export function odooSeries(version: string): string | null {
  const match = /^(?:saas~)?(\d{1,3})\.(\d{1,2})/.exec(version.trim());
  return match ? `${Number(match[1])}.${Number(match[2])}` : null;
}

/** The repository's own name ("Repo" of "Owner/Repo"), "" when the address is not one: what a new project is called
 *  (projects.create) and the folder of a new submodule (web/v1/instances.py add-submodule) when left empty. */
export function repositoryShortName(address: string): string {
  return repositoryName(address)?.split("/").at(-1) ?? "";
}
