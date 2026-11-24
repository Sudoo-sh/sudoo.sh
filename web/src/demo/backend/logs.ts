// The technical log a job writes for each of its steps: the commands the panel runs and what they answer, close to
// a real server's, with the demo's fictional names.

export interface LogContext {
  name: string;
  database: string;
  version: string;
  edition: "community" | "enterprise";
  repository?: string | null;
  branch?: string | null;
  modules?: string[];
}

const clock = () => new Date().toISOString().replace("T", " ").slice(0, 19);
const odooLine = (database: string, logger: string, text: string) =>
  `${clock()},${String(Math.floor(Math.random() * 900) + 100)} 1 INFO ${database} ${logger}: ${text}`;

export function stepLines(title: string, c: LogContext): string[] {
  const repo = c.repository?.replace(/^https:\/\/github\.com\//, "") ?? "";
  const t = title.toLowerCase();
  if (t.startsWith("downloading odoo") || t.startsWith("checking for a newer odoo"))
    return [
      `$ docker pull odoo:${c.version}`,
      `${c.version}: Pulling from library/odoo`,
      "Digest: sha256:4c1f0e9a7b2d…",
      `Status: Image is up to date for odoo:${c.version}`,
    ];
  if (t.startsWith("downloading the images"))
    return [
      `$ docker pull odoo:${c.version}`,
      `Status: Downloaded newer image for odoo:${c.version}`,
      "$ docker pull postgres:16",
      "Status: Image is up to date for postgres:16",
      "Pinned the new digests in compose.yaml",
    ];
  if (t.startsWith("preparing the instance"))
    return [`Writing /opt/odoo/instances/${c.name}/.env and compose.yaml`, `Network sudoo-${c.name} created`];
  if (t.includes("enterprise add-ons"))
    return [`Linking the Enterprise add-ons of ${c.version}`, "web_enterprise found"];
  if (
    t.startsWith("downloading the code") ||
    t.startsWith("checking github") ||
    t.startsWith("downloading the latest code")
  )
    return [
      `$ git fetch --depth 50 origin ${c.branch ?? "main"}`,
      `From github.com:${repo}`,
      ` * branch            ${c.branch ?? "main"}     -> FETCH_HEAD`,
      "$ git submodule update --init --recursive --depth 1",
      "Submodule path 'oca/web': checked out 7d1c2e4",
    ];
  if (t.startsWith("moving the submodules"))
    return ["$ git submodule update --remote --depth 1", "Submodule path 'oca/web': checked out 9a0b3f1"];
  if (t.startsWith("switching the code to branch"))
    return [
      `$ git fetch --depth 50 origin ${c.branch ?? "main"}`,
      `$ git checkout -B ${c.branch ?? "main"} FETCH_HEAD`,
    ];
  if (t.startsWith("starting the database"))
    return [
      "$ docker compose up -d db",
      ` Container ${c.name}-db-1  Started`,
      "LOG:  database system is ready to accept connections",
    ];
  if (t.startsWith("creating the odoo"))
    return [
      `$ odoo -d ${c.database} -i base --without-demo=all --stop-after-init`,
      odooLine(c.database, "odoo.modules.loading", "loading 1 modules..."),
      odooLine(c.database, "odoo.modules.loading", "1 modules loaded in 4.12s, 0 queries"),
      odooLine(c.database, "odoo.modules.loading", "loading 64 modules..."),
      odooLine(c.database, "odoo.modules.loading", "64 modules loaded in 21.87s, 8411 queries"),
    ];
  if (t.startsWith("installing the repository")) {
    const modules = c.modules?.length ? c.modules : ["custom_base"];
    return [
      `$ odoo -d ${c.database} -i ${modules.join(",")} --stop-after-init`,
      ...modules.map((m) =>
        odooLine(c.database, "odoo.modules.registry", `module ${m}: creating or updating database tables`),
      ),
      odooLine(c.database, "odoo.modules.loading", `${modules.length} modules installed`),
    ];
  }
  if (t.startsWith("upgrading modules")) {
    const modules = title.split(":")[1]?.trim() ?? "all";
    return [
      `$ odoo -d ${c.database} -u ${modules.replace(/\s/g, "")} --stop-after-init`,
      odooLine(c.database, "odoo.modules.loading", "updating modules list"),
      odooLine(c.database, "odoo.modules.loading", "Modules loaded."),
    ];
  }
  if (t.startsWith("upgrading the copy") || t.startsWith("upgrading the database to odoo")) {
    const target = title.match(/Odoo (\S+)/)?.[1] ?? c.version;
    if (t.includes("openupgrade"))
      return [
        "$ docker compose run --rm --no-deps -T --entrypoint sh odoo -c '…' sh https://github.com/OCA/OpenUpgrade/archive/<pinned>.tar.gz openupgradelib==3.13.7 …",
        "Downloading OpenUpgrade from https://github.com/OCA/OpenUpgrade/archive/<pinned>.tar.gz",
        "Installing openupgradelib==3.13.7",
        odooLine(c.database, "odoo.modules.loading", "loading 64 modules..."),
        odooLine(c.database, "OpenUpgrade", "base: migration script post-migration.py executed"),
        odooLine(c.database, "odoo.modules.loading", "64 modules loaded in 312.40s, 98411 queries"),
        `The database's base module is at version ${target}.1.3`,
      ];
    return [
      "Downloading Odoo's upgrade script from https://upgrade.odoo.com/upgrade",
      "(demo: Odoo's upgrade service is simulated, nothing leaves this page)",
      "INFO: Dump the database as 'origin.dump'",
      "INFO: Upload the database dump",
      "INFO: Upgrade request processing: pending, progress, done",
      "INFO: The upgraded database and filestore have been downloaded",
      `Loading the upgraded database into ${c.database}`,
      `The database's base module is at version ${target}.1.3`,
    ];
  }
  if (t.startsWith("switching to odoo"))
    return [
      `$ docker pull odoo:${title.match(/Odoo (\S+)/)?.[1] ?? c.version}`,
      "Pinned the new image in compose.yaml",
    ];
  if (t.startsWith("putting odoo into maintenance"))
    return ["$ docker compose stop odoo", "Visitors see the Upgrading page until the upgrade ends."];
  if (t.startsWith("putting the instance back"))
    return [
      "$ git checkout -B main <previous commit>",
      "Pinned the previous image in compose.yaml",
      `$ psql ${c.database} < dump.sql`,
      "Restored the filestore (1 284 files)",
    ];
  if (t.startsWith("loading the copy"))
    return [`$ dropdb --if-exists ${c.database} && createdb ${c.database}`, `$ psql ${c.database} < dump.sql`];
  if (t.startsWith("setting the admin password"))
    return ["The administrator (user 2) got this instance's admin password"];
  if (t.startsWith("creating the public link"))
    return [`Starting connector sudoo-connector-${c.name}`, `Public link: https://${c.name}.demo.sudoo.sh`];
  if (t.startsWith("starting odoo"))
    return [
      "$ docker compose up -d odoo",
      odooLine("?", "odoo", `Odoo version ${c.version}-20261110`),
      odooLine("?", "odoo.service.server", "HTTP service (werkzeug) running on 0.0.0.0:8069"),
      "GET /web/health 200",
    ];
  if (t.startsWith("restarting odoo"))
    return ["$ docker compose restart odoo", ` Container ${c.name}-odoo-1  Started`, "GET /web/health 200"];
  if (t.startsWith("waiting for odoo")) return ["GET /web/health 503", "GET /web/health 200"];
  if (t.startsWith("backing up") || t.startsWith("saving the current state"))
    return [
      `$ pg_dump --no-owner --format=plain ${c.database}`,
      "Packed the filestore (1 284 files)",
      "Wrote manifest.json",
      "Backup written (182.4 MB)",
    ];
  if (t.startsWith("stopping odoo")) return ["$ docker compose stop odoo", ` Container ${c.name}-odoo-1  Stopped`];
  if (t.startsWith("restoring the backup") || t.startsWith("rolling back"))
    return [
      `$ dropdb --if-exists ${c.database} && createdb ${c.database}`,
      `$ psql ${c.database} < dump.sql`,
      "Restored the filestore (1 284 files)",
    ];
  if (t.startsWith("pausing"))
    return ["$ docker compose stop", ` Container ${c.name}-odoo-1  Stopped`, ` Container ${c.name}-db-1  Stopped`];
  if (t.startsWith("switching off")) return ["$ docker compose stop", "It stays off until someone clicks Start"];
  if (t.startsWith("removing odoo")) return ["$ docker compose down --volumes", ` Network sudoo-${c.name}  Removed`];
  if (t.startsWith("removing the instance folder")) return [`Removed /opt/odoo/instances/${c.name}`];
  if (t.startsWith("comparing the instance"))
    return ["compose.yaml: unchanged", "odoo container: memory limit changed by hand"];
  if (t.startsWith("putting the panel's configuration back")) return ["$ docker compose up -d --force-recreate odoo"];
  if (t.startsWith("adding submodule") || t.startsWith("removing submodule"))
    return ["$ git submodule sync", "$ git commit -m 'Sudoo.sh: submodules'"];
  if (t.startsWith("saving the change to github")) return ["$ git push origin HEAD", "To github.com:" + repo];
  if (t.startsWith("putting the code into")) return ["Copied the repository into the add-ons folder"];
  return [];
}

export function logText(steps: string[], c: LogContext): string {
  return steps
    .map(
      (title) =>
        `\n== ${title}\n${stepLines(title, c)
          .map((l) => `${l}\n`)
          .join("")}`,
    )
    .join("");
}
