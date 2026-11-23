// The demo's Odoo logs: what an Odoo 18 server writes (werkzeug's requests, scheduled actions, the bus, SQL in debug,
// now and then a warning, an error with its traceback, rarely a critical line), the same for the same instance, over
// the last three hours, growing while a log is followed. Also install.log and update.log (Odoo's own output of the
// latest module installs and updates). Pages and filters as the server's (web/v1/logs.py, instances/logs.py), with
// the web app's own filter code (lib/logs.ts).

import type { Instance } from "@/api/types";
import { appendLines, entryText, LEVELS, makeMatcher, passes, type Level, type LogEntry, type LogFilters } from "@/lib/logs";
import { hash, Problem } from "./util";

interface Stored {
  id: string;
  lines: string[];
}

const HISTORY_MINUTES = 180;
const STORES = new Map<string, { entries: Stored[]; n: number }>();

const PATHS = [
  ["POST", "/web/dataset/call_kw/sale.order/web_search_read"],
  ["POST", "/web/dataset/call_kw/res.partner/web_read"],
  ["POST", "/web/action/load"],
  ["GET", "/web"],
  ["GET", "/odoo/action-sale.action_quotations_with_onboarding"],
  ["POST", "/web/dataset/call_kw/stock.picking/web_search_read"],
  ["GET", "/web/image/res.partner/7/avatar_128"],
  ["POST", "/mail/data"],
  ["GET", "/shop"],
  ["GET", "/my/orders"],
  ["POST", "/websocket/peek_notifications"],
  ["GET", "/web/assets/1/web.assets_web.min.js"],
  ["POST", "/web/dataset/call_kw/account.move/action_post"],
  ["GET", "/web/login"],
] as const;
const CRONS = [
  [1, "Mail: Email Queue Manager"],
  [4, "Discuss: channel member unmute"],
  [12, "Sales: Send pending quotation reminders"],
  [17, "Inventory: run scheduler"],
  [23, "Base: Auto-vacuum internal data"],
] as const;

/** A stamp of Odoo's ("2026-11-22 08:00:00,123") and the id the server gives the line (Docker's time stamp). */
function stamps(at: number, n: number): [string, string] {
  const iso = new Date(at).toISOString();
  const odoo = `${iso.slice(0, 10)} ${iso.slice(11, 19)},${iso.slice(20, 23)}`;
  const id = `${iso.slice(0, 19)}.${iso.slice(20, 23)}${String(n % 1_000_000).padStart(6, "0")}Z`;
  return [odoo, id];
}

function entryAt(instance: Instance, n: number, at: number): Stored {
  const db = instance.odoo.database;
  const r = hash(`${instance.name}:${n}`);
  const [stamp, id] = stamps(at, n);
  const pid = 7 + (r % 3);
  const line = (level: Level, logger: string, text: string) => `${stamp} ${pid} ${level} ${db} ${logger}: ${text}`;
  const day = new Date(at).toUTCString().slice(5, 16).replace(/ /g, "/");
  const clock = new Date(at).toISOString().slice(11, 19);
  const pick = r % 1000;
  if (pick < 12) {
    const order = `Shop/${String(r % 900).padStart(4, "0")}`;
    return {
      id,
      lines: [
        line("ERROR", "odoo.http", "Exception during request handling."),
        "Traceback (most recent call last):",
        '  File "/usr/lib/python3/dist-packages/odoo/http.py", line 2375, in __call__',
        "    response = request._serve_db()",
        '  File "/usr/lib/python3/dist-packages/odoo/http.py", line 1953, in _serve_db',
        "    return service_model.retrying(self._serve_ir_http, self.env)",
        '  File "/usr/lib/python3/dist-packages/odoo/service/model.py", line 137, in retrying',
        "    result = func()",
        '  File "/usr/lib/python3/dist-packages/odoo/addons/point_of_sale/models/pos_order.py", line 912, in _process_payment_lines',
        '    raise ValidationError(_("The payment of order %s does not match its total.", order.name))',
        `odoo.exceptions.ValidationError: The payment of order ${order} does not match its total.`,
      ],
    };
  }
  if (pick < 14) return { id, lines: [line("CRITICAL", "odoo.service.server", "Worker (pid 41) reached its memory limit (2048 MB) and was stopped.")] };
  if (pick < 22) return { id, lines: [line("ERROR", "odoo.addons.mail.models.mail_mail", `Mail delivery failed via SMTP server 'smtp.acme.example': Connection unexpectedly closed (message ${r % 5000})`)] };
  if (pick < 60) {
    const warnings = [
      `sale.order.read() with unknown field 'x_studio_reference' (x_studio_reference)`,
      "Generating nondeterministic report for invoice INV/2026/0" + (r % 900),
      "The cron 'Inventory: run scheduler' took 42.1s, longer than its interval",
      "Field product.template.description_sale: unknown parameter 'tracking', if this is an actual parameter you may want to override the method _valid_field_parameter on the relevant model in order to allow it",
    ];
    const loggers = ["odoo.models", "odoo.addons.account.models.account_move", "odoo.addons.base.models.ir_cron", "odoo.fields"];
    return { id, lines: [line("WARNING", loggers[r % 4]!, warnings[r % 4]!)] };
  }
  if (pick < 110) return { id, lines: [line("DEBUG", "odoo.sql_db", `query: SELECT "res_partner"."id" FROM "res_partner" WHERE ("res_partner"."active" = true) LIMIT 80 (${(r % 90) / 10} ms)`)] };
  if (pick < 190) {
    const [cron, name] = CRONS[r % CRONS.length]!;
    return {
      id,
      lines: [r % 2
        ? line("INFO", "odoo.addons.base.models.ir_cron", `Job '${name}' (${cron}) starting`)
        : line("INFO", "odoo.addons.base.models.ir_cron", `Job '${name}' (${cron}) fully done (#loop 1; done ${r % 9}; remaining 0; duration ${(r % 300) / 100}s)`)],
    };
  }
  if (pick < 240) {
    const infos = [
      ["odoo.addons.bus.models.bus", "Bus.loop listen imbus on db postgres"],
      ["odoo.models.unlink", `User #2 deleted ir.attachment records with IDs: [${r % 4000}, ${(r % 4000) + 1}]`],
      ["odoo.addons.base.models.res_users", `Login successful for db:${db} login:lena@acme.example from 10.0.4.${r % 200}`],
      ["odoo.modules.registry", "Caches invalidated, signaling through the database: ['default']"],
    ] as const;
    const [logger, text] = infos[r % infos.length]!;
    return { id, lines: [line("INFO", logger, text)] };
  }
  const [method, path] = PATHS[r % PATHS.length]!;
  const status = r % 97 === 0 ? 404 : r % 53 === 0 ? 303 : r % 211 === 0 ? 500 : 200;
  const total = (r % 700) / 1000 + (r % 41 === 0 ? 1.4 : 0);
  return {
    id,
    lines: [line("INFO", "werkzeug", `10.0.4.${r % 200} - - [${day} ${clock}] "${method} ${path} HTTP/1.1" ${status} - ${r % 40} ${(total / 4).toFixed(3)} ${total.toFixed(3)}`)],
  };
}

// an entry may be written in every slot of SLOT ms (most slots have one): the same slot gives the same entry, so the
// ids stay the same when the demo is opened again and a link to a line keeps working
const SLOT = 1500;

/** The entries of the slots up to now that are not in the store yet. */
function catchUp(instance: Instance, store: { entries: Stored[]; n: number }): Stored[] {
  const fresh: Stored[] = [];
  const now = Math.floor(Date.now() / SLOT);
  for (; store.n <= now; store.n++) {
    const r = hash(`${instance.name}#${store.n}`);
    if (r % 10 < 3) continue; // a quiet slot
    fresh.push(entryAt(instance, store.n, store.n * SLOT + (r % SLOT)));
  }
  store.entries.push(...fresh);
  if (store.entries.length > 20_000) store.entries.splice(0, store.entries.length - 20_000);
  return fresh;
}

function storeOf(instance: Instance) {
  let store = STORES.get(instance.name);
  if (!store) {
    store = { entries: [], n: Math.floor((Date.now() - HISTORY_MINUTES * 60_000) / SLOT) };
    STORES.set(instance.name, store);
  }
  catchUp(instance, store);
  return store;
}

/** The last `count` entries of odoo.log, up to now (a followed log asks again for the new ones). */
export const recent = (instance: Instance, count: number): Stored[] => storeOf(instance).entries.slice(-count);

/** The end of odoo.log as text (GET .../logs?lines=). */
export const odooLogText = (instance: Instance, lines: number) =>
  recent(instance, lines).flatMap((e) => e.lines).slice(-lines).join("\n") + "\n";

// ---------------------------------------------------------------- install.log and update.log

function jobLog(instance: Instance, log: string): Stored[] {
  const db = instance.odoo.database;
  const start = Date.now() - (log === "install.log" ? 26 : 3) * 3_600_000;
  const lines: [number, string][] = [];
  const add = (s: number, level: Level, logger: string, text: string) => lines.push([s, `${stamps(start + s * 1000, 0)[0]} 1 ${level} ${db} ${logger}: ${text}`]);
  if (log === "install.log") {
    add(0, "INFO", "odoo", `Odoo version ${instance.odoo.version}`);
    add(0, "INFO", "odoo", "Using configuration file at /etc/odoo/odoo.conf");
    add(1, "INFO", "odoo.modules.loading", "loading 1 modules...");
    add(4, "INFO", "odoo.modules.loading", "1 modules loaded in 2.94s, 0 queries (+0 extra)");
    add(5, "INFO", "odoo.modules.loading", "loading 58 modules...");
    for (const [i, module] of ["sale_management", "stock", "account", "point_of_sale", "website_sale", "acme_sale_extras"].entries())
      add(6 + i * 4, "INFO", "odoo.modules.registry", `module ${module}: creating or updating database tables`);
    add(30, "WARNING", "odoo.modules.loading", "The models ['acme.delivery.slot'] have no access rules in module acme_sale_extras, consider adding some, like:\nid,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink\nacme_sale_extras.access_acme_delivery_slot,access_acme_delivery_slot,acme_sale_extras.model_acme_delivery_slot,base.group_user,1,0,0,0");
    add(38, "INFO", "odoo.modules.loading", "58 modules loaded in 32.71s, 41873 queries (+41873 extra)");
    add(40, "INFO", "odoo.modules.loading", "Modules loaded.");
    add(40, "INFO", "odoo.service.server", "Initiating shutdown");
  } else {
    add(0, "INFO", "odoo", `Odoo version ${instance.odoo.version}`);
    add(1, "INFO", "odoo.modules.loading", "updating modules list");
    add(3, "INFO", "odoo.modules.loading", "loading 58 modules...");
    add(9, "INFO", "odoo.modules.registry", "module acme_sale_extras: creating or updating database tables");
    add(11, "ERROR", "odoo.modules.registry", "Failed to load registry");
    lines.push(
      [11, "Traceback (most recent call last):"],
      [11, '  File "/usr/lib/python3/dist-packages/odoo/modules/registry.py", line 110, in new'],
      [11, "    odoo.modules.load_modules(registry, force_demo, status, update_module)"],
      [11, '  File "/usr/lib/python3/dist-packages/odoo/tools/convert.py", line 698, in _tag_root'],
      [11, "    raise ParseError(msg) from None  # Restart with \"--log-handler odoo.tools.convert:DEBUG\" for complete traceback"],
      [11, "odoo.tools.convert.ParseError: while parsing /mnt/extra-addons/acme_sale_extras/views/sale_order_views.xml:14, somewhere inside"],
    );
    add(12, "INFO", "odoo.service.server", "Initiating shutdown");
    add(400, "INFO", "odoo", `Odoo version ${instance.odoo.version}`);
    add(402, "INFO", "odoo.modules.loading", "updating modules list");
    add(405, "INFO", "odoo.modules.registry", "module acme_sale_extras: creating or updating database tables");
    add(418, "INFO", "odoo.modules.loading", "58 modules loaded in 12.40s, 8114 queries (+8114 extra)");
    add(418, "INFO", "odoo.modules.loading", "Modules loaded.");
  }
  const text = lines.flatMap(([, l]) => l.split("\n"));
  const ids = lines.flatMap(([s, l], i) => l.split("\n").map((_, j) => `${String(start + s * 1000).padStart(13, "0")}.${String(i * 10 + j).padStart(7, "0")}`));
  return appendLines([], text, ids).map((e) => ({ id: e.id, lines: entryText(e).split("\n") }));
}

// ---------------------------------------------------------------- pages and filters, as the server's

/** The filters of a request (level, logger, q, regex, since, until, tracebacks), checked as the server checks them. */
export function filtersFrom(query: URLSearchParams): LogFilters {
  const levels = (query.get("level") ?? "").split(",").map((l) => l.trim().toUpperCase()).filter(Boolean);
  if (levels.some((l) => !LEVELS.includes(l as Level))) throw new Problem(400, `level takes one or more of ${LEVELS.join(", ")}, separated by commas.`);
  const logger = query.get("logger") ?? "";
  if (logger && !/^[\w.-]{1,120}$/.test(logger)) throw new Problem(400, "logger is the name of a Python logger, such as odoo.http (letters, digits, . _ -).");
  const q = query.get("q") ?? "";
  const regex = query.get("regex") === "1";
  const matcher = makeMatcher(q, regex);
  if (!matcher.ok)
    throw new Problem(400, matcher.reason === "slow" ? "This regular expression could take very long (nested repetitions such as (a+)+). Simplify it." : matcher.reason === "long" ? "The filter is too long (at most 200 characters)." : "This is not a valid regular expression.");
  const since = query.get("since") ?? "";
  const until = query.get("until") ?? "";
  for (const value of [since, until]) if (value && Number.isNaN(Date.parse(value))) throw new Problem(400, "since and until are times in ISO 8601 with their zone.");
  return {
    levels: levels as Level[],
    logger,
    query: q,
    regex,
    onlyMatches: Boolean(q),
    tracebacks: query.get("tracebacks") === "1",
    range: since || until ? { kind: "custom", since, until } : { kind: "all" },
  };
}

const parsed = new WeakMap<Stored, LogEntry>();
function entryOfStored(stored: Stored): LogEntry {
  let entry = parsed.get(stored);
  if (!entry) {
    entry = appendLines([], stored.lines, stored.lines.map(() => stored.id))[0]!;
    parsed.set(stored, entry);
  }
  return entry;
}

/** A filter on stored entries, for the stream. */
export function keeper(filters: LogFilters) {
  const matcher = makeMatcher(filters.onlyMatches ? filters.query : "", filters.regex);
  return (stored: Stored) => passes(entryOfStored(stored), filters, matcher);
}

/** GET /instances/<name>/logs/entries. */
export function page(instance: Instance, log: string, query: URLSearchParams) {
  if (!["odoo.log", "install.log", "update.log"].includes(log)) throw new Problem(400, "Choose one of the logs: odoo.log, install.log, update.log.");
  const limit = Number(query.get("limit") ?? 500);
  if (!Number.isInteger(limit) || limit < 1 || limit > 2000) throw new Problem(400, "limit is a whole number from 1 to 2000.");
  const keep = keeper(filtersFrom(query));
  const all = log === "odoo.log" ? storeOf(instance).entries : jobLog(instance, log);
  const before = query.get("before");
  const after = query.get("after");
  const around = query.get("around");
  let chosen: Stored[];
  let hasBefore = false;
  let hasAfter = false;
  if (around) {
    const at = all.findIndex((e) => e.id >= around);
    const target = at < 0 ? all.length - 1 : at;
    const earlier = all.slice(0, target).filter(keep);
    const later = all.slice(target + 1).filter(keep);
    const half = Math.floor(limit / 2);
    chosen = [...earlier.slice(-half), all[target]!, ...later.slice(0, limit - Math.min(half, earlier.length) - 1)];
    hasBefore = earlier.length > half;
    hasAfter = later.length > limit - half - 1;
  } else if (after) {
    const later = all.filter((e) => e.id > after).filter(keep);
    chosen = later.slice(0, limit);
    hasBefore = true;
    hasAfter = later.length > limit;
  } else {
    const earlier = (before ? all.filter((e) => e.id < before) : all).filter(keep);
    chosen = earlier.slice(-limit);
    hasBefore = earlier.length > limit;
    hasAfter = Boolean(before);
  }
  return {
    data: chosen.map((stored) => {
      const entry = entryOfStored(stored);
      return { id: stored.id, time: entry.time, level: entry.level, pid: entry.pid ? Number(entry.pid) : null, database: entry.db, logger: entry.logger, traceback: entry.traceback, text: stored.lines.join("\n") };
    }),
    meta: { log, has_before: hasBefore, has_after: hasAfter, first: chosen[0]?.id ?? null, last: chosen[chosen.length - 1]?.id ?? null },
  };
}
