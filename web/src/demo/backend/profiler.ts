// The demo's code profiler (instances/profiler.py): one recording per instance at a time (preparing, recording
// with its countdown, saving), stopping early, the last PROFILER_KEEP recordings, and flame graphs made up from a
// typical Odoo call tree (HTTP workers rendering a list, the ORM, SQL, a cron). Nothing is sampled anywhere.

import type { Instance, ProfileNode, ProfileRecording, ProfilerRun, ProfilerState, ProfileTree } from "@/api/types";
import { ASLEEP, ME, record, setting } from "./state";
import { hash, hex, iso, MINUTE, OPENED, Problem, SECOND, seeded } from "./util";

interface Stored {
  meta: ProfileRecording;
  tree: ProfileTree;
}
interface Running {
  run: ProfilerRun;
  startedAt: number;
  recordingSince: number | null;
  timer: number;
}

const stored = new Map<string, Stored[]>();
const running = new Map<string, Running>();

type Shape = [name: string, where: string, weight: number, children?: Shape[]];

// a typical Odoo 18 request and cron, by where time goes (weights are relative)
const REQUEST: Shape = ["Application.__call__", "odoo/http.py:2361", 1, [
  ["Request._serve_db", "odoo/http.py:1934", 1, [
    ["Dispatcher.dispatch", "odoo/http.py:2187", 1, [
      ["DataSet.call_kw", "odoo/addons/web/controllers/dataset.py:42", 0.62, [
        ["call_kw_model", "odoo/service/model.py:62", 1, [
          ["Model.web_search_read", "odoo/addons/web/models/models.py:96", 0.7, [
            ["BaseModel.search_fetch", "odoo/models.py:1714", 0.45, [
              ["BaseModel._search", "odoo/models.py:5612", 0.3, [["Query.__str__", "odoo/tools/query.py:212", 0.4], ["BaseModel._flush_search", "odoo/models.py:5590", 0.6]]],
              ["Cursor.execute", "odoo/sql_db.py:332", 0.7, [["cursor.execute", "psycopg2/extensions.py:0", 1]]],
            ]],
            ["Model.web_read", "odoo/addons/web/models/models.py:142", 0.55, [
              ["BaseModel._read_format", "odoo/models.py:3480", 0.4, [["Field.convert_to_read", "odoo/fields.py:1162", 1]]],
              ["Field.__get__", "odoo/fields.py:1213", 0.6, [
                ["BaseModel._fetch_field", "odoo/models.py:3612", 0.4, [["Cursor.execute", "odoo/sql_db.py:332", 1]]],
                ["SaleOrder._compute_amounts", "odoo/addons/sale/models/sale_order.py:612", 0.6, [
                  ["AccountTax._compute_taxes", "odoo/addons/account/models/account_tax.py:1490", 0.7, [["AccountTax._eval_tax_amount", "odoo/addons/account/models/account_tax.py:822", 1]]],
                  ["Field.__get__", "odoo/fields.py:1213", 0.3, [["Cursor.execute", "odoo/sql_db.py:332", 1]]],
                ]],
              ]],
            ]],
          ]],
          ["SaleOrder.action_confirm", "odoo/addons/sale/models/sale_order.py:1003", 0.3, [
            ["StockPicking._action_assign", "odoo/addons/stock/models/stock_picking.py:1095", 0.6, [["StockQuant._gather", "odoo/addons/stock/models/stock_quant.py:540", 1, [["Cursor.execute", "odoo/sql_db.py:332", 1]]]]],
            ["MailThread.message_post", "odoo/addons/mail/models/mail_thread.py:2145", 0.4, [["IrQweb._render", "odoo/addons/base/models/ir_qweb.py:378", 1]]],
          ]],
        ]],
      ]],
      ["Home.web_client", "odoo/addons/web/controllers/home.py:44", 0.25, [
        ["IrQweb._render", "odoo/addons/base/models/ir_qweb.py:378", 0.7, [["IrQweb._compile", "odoo/addons/base/models/ir_qweb.py:612", 1]]],
        ["IrHttp.get_frontend_session_info", "odoo/addons/web/models/ir_http.py:71", 0.3],
      ]],
      ["Binary.content_image", "odoo/addons/web/controllers/binary.py:120", 0.13, [["IrBinary._image_process", "odoo/tools/image.py:88", 1, [["Image.resize", "PIL/Image.py:2200", 1]]]]],
    ]],
    ["Registry.check_signaling", "odoo/modules/registry.py:885", 0.06, [["Cursor.execute", "odoo/sql_db.py:332", 1]]],
  ]],
]];
const CRON: Shape = ["WorkerCron.process_work", "odoo/service/server.py:1295", 1, [
  ["IrCron._process_jobs", "odoo/addons/base/models/ir_cron.py:282", 1, [
    ["MailMail.process_email_queue", "odoo/addons/mail/models/mail_mail.py:420", 0.7, [["IrMailServer.send_email", "odoo/addons/base/models/ir_mail_server.py:690", 1, [["SMTP.sendmail", "smtplib.py:887", 1]]]]],
    ["IrCron._acquire_one_job", "odoo/addons/base/models/ir_cron.py:420", 0.3, [["Cursor.execute", "odoo/sql_db.py:332", 1]]],
  ]],
]];

function grow(shape: Shape, samples: number, random: () => number): ProfileNode {
  const [name, where, , children] = shape;
  const node: ProfileNode = { n: name, w: where, v: Math.max(1, Math.round(samples)) };
  if (children?.length) {
    const own = 0.04 + random() * 0.06; // time in the function's own code
    const weights = children.map((c) => c[2] * (0.75 + random() * 0.5));
    const sum = weights.reduce((a, b) => a + b, 0);
    node.c = children
      .map((child, i) => grow(child, (samples * (1 - own) * weights[i]!) / sum, random))
      .filter((c) => c.v >= 2)
      .sort((a, b) => b.v - a.v);
  }
  return node;
}

function makeTree(seed: number, seconds: number): ProfileTree {
  const random = seeded(seed);
  const busy = 0.25 + random() * 0.25; // share of the time the workers were not waiting
  const http = Math.round(seconds * 100 * busy * 2); // two HTTP workers
  const cron = Math.round(seconds * 100 * 0.04);
  const threads = [
    { name: "Worker WorkerHTTP (24) workthread", samples: Math.round(http * 0.55) },
    { name: "Worker WorkerHTTP (25) workthread", samples: http - Math.round(http * 0.55) },
    { name: "Worker WorkerCron (27) workthread", samples: cron },
  ];
  const root: ProfileNode = {
    n: "all",
    v: threads.reduce((a, t) => a + t.samples, 0),
    c: threads.map((t) => {
      const shape: Shape = t.name.includes("Cron") ? CRON : REQUEST;
      return { n: t.name, v: t.samples, c: [{ n: "Thread._bootstrap", w: "threading.py:1032", v: t.samples, c: [grow(shape, t.samples, random)] }] };
    }),
  };
  const self = new Map<string, { name: string; where: string; self: number; total: number }>();
  const visit = (node: ProfileNode) => {
    const own = node.v - (node.c ?? []).reduce((a, c) => a + c.v, 0);
    if (node.w && own > 0) {
      const key = `${node.n}@${node.w}`;
      const found = self.get(key) ?? { name: node.n, where: node.w, self: 0, total: 0 };
      found.self += own;
      found.total += node.v;
      self.set(key, found);
    }
    node.c?.forEach(visit);
  };
  visit(root);
  return { total: root.v, seconds, threads, root, top: [...self.values()].sort((a, b) => b.self - a.self).slice(0, 40) };
}

function keep(): number {
  return Number(setting("profiler_keep") ?? 5);
}

function save(instance: string, user: string, startedAt: number, seconds: number, minutes: number, stoppedEarly: boolean, production: boolean, seed: number) {
  const id = `${new Date(startedAt).toISOString().slice(0, 19).replace(/[-:]/g, "").replace("T", "-")}-${hex(6)}`;
  const tree = makeTree(seed, seconds);
  const meta: ProfileRecording = {
    id,
    user,
    minutes,
    seconds,
    samples: tree.total,
    threads: tree.threads.length,
    rate: 100,
    size: tree.total * 38 + 21000,
    started_at: iso(startedAt)!,
    ended_at: iso(startedAt + seconds * SECOND)!,
    stopped_early: stoppedEarly,
    production,
    links: { self: `/api/v1/instances/${instance}/profiles/${id}`, download: `/api/v1/instances/${instance}/profiles/${id}/download` },
  };
  const list = [{ meta, tree }, ...(stored.get(instance) ?? [])].sort((a, b) => b.meta.started_at.localeCompare(a.meta.started_at)).slice(0, keep());
  stored.set(instance, list);
  return meta;
}

/** Two past recordings on the main production instance and one on a staging one, so the tab is not empty. */
export function seedProfiles(instances: Instance[]) {
  const production = instances.find((i) => i.stage === "production");
  const staging = instances.find((i) => i.stage === "staging");
  if (production) {
    save(production.name, "lena", OPENED - 26 * MINUTE, 300, 5, false, true, 11);
    save(production.name, "omar", OPENED - 3 * MINUTE - 2 * 3600 * SECOND, 92.4, 3, true, true, 5);
  }
  if (staging) save(staging.name, "karim", OPENED - 50 * MINUTE, 120, 2, false, false, 23);
}

export function profilerState(instance: Instance): ProfilerState {
  const current = running.get(instance.name);
  return {
    enabled: setting("profiler_enabled") !== false,
    production: instance.stage === "production",
    production_allowed: setting("profiler_production") !== false,
    running: !ASLEEP.includes(instance.status),
    status: instance.status,
    max_minutes: Number(setting("profiler_max_minutes") ?? 5),
    keep: keep(),
    rate: Number(setting("profiler_rate") ?? 100),
    current: current ? { ...current.run } : null,
    last_error: null,
    recordings: (stored.get(instance.name) ?? []).slice(0, keep()).map((s) => s.meta),
  };
}

function finish(instance: Instance, stoppedEarly: boolean) {
  const found = running.get(instance.name);
  if (!found) return;
  window.clearTimeout(found.timer);
  found.run.phase = "saving";
  found.timer = window.setTimeout(() => {
    running.delete(instance.name);
    const since = found.recordingSince ?? Date.now();
    const seconds = Math.max(1, Math.round((Date.now() - since) / 100) / 10);
    save(instance.name, found.run.user, since, Math.min(seconds, found.run.minutes * 60), found.run.minutes, stoppedEarly, found.run.production, hash(found.run.id));
    record(ME, "profile recorded", { instance: instance.name, detail: `${found.run.id}: ${seconds} s` });
  }, 1400);
}

export function startProfiler(instance: Instance, body: Record<string, unknown>): ProfilerRun {
  const state = profilerState(instance);
  if (!state.enabled) throw new Problem(403, "The profiler is switched off on this panel.");
  const minutes = body.minutes;
  if (typeof minutes !== "number" || !Number.isInteger(minutes) || minutes < 1 || minutes > state.max_minutes)
    throw new Problem(400, `Choose from 1 to ${state.max_minutes} minutes.`);
  if (state.production) {
    if (!state.production_allowed) throw new Problem(403, "Profiles of production instances are switched off on this panel.");
    if (body.confirm_production !== true)
      throw new Problem(400, `${instance.name} is a production instance: confirm that you want to record a profile of it ("confirm_production": true).`);
  }
  if (!state.running) throw new Problem(409, "The instance is not running: start it, then record a profile.");
  if (running.has(instance.name)) throw new Problem(409, "A profile of this instance is being recorded. Wait for it, or stop it.");
  const now = Date.now();
  const run: ProfilerRun = {
    id: `${new Date(now).toISOString().slice(0, 19).replace(/[-:]/g, "").replace("T", "-")}-${hex(6)}`,
    phase: "preparing",
    minutes,
    user: ME,
    started_at: iso(now)!,
    recording_since: null,
    ends_at: iso(now + minutes * MINUTE)!,
    stopping: false,
    production: state.production,
  };
  const entry: Running = { run, startedAt: now, recordingSince: null, timer: 0 };
  running.set(instance.name, entry);
  entry.timer = window.setTimeout(() => {
    entry.recordingSince = Date.now();
    run.phase = "recording";
    run.recording_since = iso(entry.recordingSince);
    run.ends_at = iso(entry.recordingSince + minutes * MINUTE)!;
    entry.timer = window.setTimeout(() => finish(instance, false), minutes * MINUTE);
  }, 1800);
  record(ME, "profiler started", { instance: instance.name, detail: `${run.id}: ${minutes} min${state.production ? " (production, confirmed)" : ""}` });
  return { ...run };
}

export function stopProfiler(instance: Instance): ProfilerRun {
  const found = running.get(instance.name);
  if (!found) throw new Problem(409, "No profile of this instance is being recorded.");
  found.run.stopping = true;
  if (found.run.phase === "preparing") {
    window.clearTimeout(found.timer);
    running.delete(instance.name);
  } else if (found.run.phase === "recording") finish(instance, true);
  record(ME, "profiler stopped", { instance: instance.name, detail: found.run.id });
  return { ...found.run };
}

export function profileOf(instance: Instance, id: string) {
  const found = (stored.get(instance.name) ?? []).find((s) => s.meta.id === id);
  if (!found) throw new Problem(404, "This recording does not exist.");
  return { ...found.meta, tree: found.tree };
}

export function deleteProfile(instance: Instance, id: string) {
  const list = stored.get(instance.name) ?? [];
  if (!list.some((s) => s.meta.id === id)) throw new Problem(404, "This recording does not exist.");
  stored.set(instance.name, list.filter((s) => s.meta.id !== id));
  record(ME, "profile deleted", { instance: instance.name, detail: id });
}
