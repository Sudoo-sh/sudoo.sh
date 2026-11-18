// The demo's web shell: the same rules as webshell/policy.py (permissions, production, root, recordings), one-time
// tickets, and a simulated shell behind a fake WebSocket. No process runs anywhere: a fake prompt answers a few
// commands (ls, odoo --version, psql -c "select 1", ...) with canned output; anything else gets a short note.

import type { Instance, Permission, ShellOptions, ShellRecording, ShellSession, ShellTarget, ShellTicket } from "@/api/types";
import { ASLEEP, ME, record, roleInfo, session, setting } from "./state";
import { iso, MINUTE, OPENED, Problem } from "./util";

const TARGETS: ShellTarget[] = ["shell", "odoo-shell", "psql"];
const PERMISSION: Record<ShellTarget, Permission> = { shell: "instances.shell", "odoo-shell": "instances.shell", psql: "instances.database_shell" };
const USER: Record<ShellTarget, string> = { shell: "odoo", "odoo-shell": "odoo", psql: "postgres" };

type Refusal = { code: string; reason: string };

function refusal(instance: Instance, target: ShellTarget, asRoot: boolean): Refusal | null {
  const role = session.role;
  if (setting("shell_enabled") === false) return { code: "disabled", reason: "Shells are switched off on this panel." };
  if (!roleInfo(role).permissions.includes(PERMISSION[target]))
    return { code: "permission", reason: "Your role cannot do this (open a terminal in instances' containers)." };
  if (instance.stage === "production") {
    const rule = String(setting("shell_production") ?? "super_admins");
    if (rule === "off") return { code: "production-off", reason: "Shells in production instances are switched off on this panel." };
    if (rule === "super_admins" && role !== "super_admin") return { code: "production-super-admins", reason: "Only super admins open shells in production instances." };
  }
  if (asRoot) {
    if (target !== "shell") return { code: "root-target", reason: "Only the Odoo container's terminal can run as root." };
    if (setting("shell_root") !== true) return { code: "root-off", reason: "Root shells are switched off on this panel." };
    if (role !== "super_admin") return { code: "root-super-admins", reason: "Only super admins open root shells." };
  }
  return null;
}

function recorded(instance: Instance, asRoot: boolean) {
  const mode = String(setting("shell_recording") ?? "production");
  return mode === "all" || (mode === "production" && (instance.stage === "production" || asRoot));
}

export function shellOptions(instance: Instance): ShellOptions {
  const choice = (target: ShellTarget, asRoot = false) => {
    const refused = refusal(instance, target, asRoot);
    return refused ? { allowed: false as const, ...refused } : { allowed: true as const, user: asRoot ? "root" : USER[target], recorded: recorded(instance, asRoot) };
  };
  const root = choice("shell", true);
  return {
    enabled: setting("shell_enabled") !== false,
    production: instance.stage === "production",
    running: !ASLEEP.includes(instance.status),
    status: instance.status,
    targets: Object.fromEntries(TARGETS.map((t) => [t, choice(t)])) as ShellOptions["targets"],
    root: root.allowed ? { allowed: true, recorded: root.recorded } : root,
    limits: {
      idle_minutes: Number(setting("shell_idle_minutes") ?? 15),
      max_minutes: Number(setting("shell_max_minutes") ?? 60),
      per_user: Number(setting("shell_max_per_user") ?? 2),
      per_instance: Number(setting("shell_max_per_instance") ?? 3),
    },
    open: open.size,
    open_mine: open.size,
  };
}

// ---------------------------------------------------------------- tickets

interface Waiting {
  instance: Instance;
  target: ShellTarget;
  asRoot: boolean;
  expires: number;
}
const tickets = new Map<string, Waiting>();

export function issueTicket(instance: Instance, body: Record<string, unknown>): ShellTicket {
  const target = (body.target ?? "shell") as ShellTarget;
  if (!TARGETS.includes(target)) throw new Problem(400, 'Choose "shell", "odoo-shell" or "psql".');
  const asRoot = body.as_root === true;
  const refused = refusal(instance, target, asRoot);
  if (refused) throw new Problem(refused.code === "root-target" ? 400 : 403, refused.reason);
  if (instance.stage === "production" && body.confirm_production !== true)
    throw new Problem(400, `${instance.name} is a production instance: confirm that you want to open a shell in it ("confirm_production": true).`);
  if (ASLEEP.includes(instance.status)) throw new Problem(409, "The instance is not running: start it, then open the shell.");
  if (open.size >= Number(setting("shell_max_per_user") ?? 2)) throw new Problem(429, `You already have ${open.size} shells open: close one first.`);
  const ticket = Array.from(crypto.getRandomValues(new Uint8Array(24)), (b) => b.toString(16).padStart(2, "0")).join("");
  const seconds = Number(setting("shell_ticket_seconds") ?? 15);
  tickets.set(ticket, { instance, target, asRoot, expires: Date.now() + seconds * 1000 });
  return {
    ticket,
    expires_in: seconds,
    socket: `/api/v1/instances/${instance.name}/shell/socket`,
    protocol: "sudoo.shell.v1",
    target,
    container: `odoo-${instance.name}-${target === "psql" ? "db" : "odoo"}-1`,
    container_user: asRoot ? "root" : USER[target],
    root: asRoot,
    production: instance.stage === "production",
    recorded: recorded(instance, asRoot),
  };
}

// ---------------------------------------------------------------- open and past shells

const open = new Map<string, { session: ShellSession; end: (reason: string) => void }>();
const past: ShellSession[] = [];
const recordings = new Map<string, ShellRecording>();

/** A past shell in the sample data, with its recording: someone looked at a production database last week. */
function seedPast(found: Instance) {
  const instance = found.name;
  if (found.stage !== "production" || past.some((s) => s.instance === instance)) return;
  const started = OPENED - 6 * 24 * 60 * MINUTE + 9 * 60 * MINUTE;
  const id = `demo${instance.replace(/[^a-z0-9]/g, "")}`.slice(0, 24);
  const item: ShellSession = {
    id, instance, target: "psql", container_user: "postgres", root: false, production: true, user: "omar", ip: "203.0.113.24",
    started_at: iso(started)!, ended_at: iso(started + 4 * MINUTE + 12_000), duration_seconds: 252, reason: "exit", recorded: true,
    recording: true, open: false,
  };
  past.push(item);
  const db = found.odoo.database;
  const prompt = `${db}=# `;
  recordings.set(id, {
    session: item,
    header: { version: 2, width: 100, height: 30, timestamp: Math.floor(started / 1000), title: `${instance} (psql) by omar` },
    events: [
      [0.2, "o", `psql (16.4)\r\nType "help" for help.\r\n\r\n${prompt}`],
      [3.1, "i", "select count(*) from sale_order where state = 'draft';\r"],
      [3.1, "o", "select count(*) from sale_order where state = 'draft';\r\n"],
      [3.4, "o", ` count \r\n-------\r\n    14\r\n(1 row)\r\n\r\n${prompt}`],
      [12.8, "i", "\\q\r"],
      [12.8, "o", "\\q\r\n"],
      [12.9, "m", "closed: exit"],
    ],
  });
}

export function shellSessions(instance: Instance | null): ShellSession[] {
  if (instance) seedPast(instance);
  const all = [...[...open.values()].map((o) => o.session), ...past.slice().reverse()];
  return instance ? all.filter((s) => s.instance === instance.name) : all;
}

export function endShell(id: string) {
  const found = open.get(id);
  if (!found) throw new Problem(404, "No open shell has this id.");
  found.end("ended");
  record(ME, "shell ended by a super admin", { detail: id });
}

export function recordingOf(id: string): ShellRecording {
  const found = recordings.get(id);
  if (!found) throw new Problem(404, "No recording has this id.");
  record(ME, "shell recording played", { instance: found.session?.instance, detail: id });
  return found;
}

// ---------------------------------------------------------------- the simulated shell

const encoder = new TextEncoder();
const RESET = "\x1b[0m";
const BLUE = "\x1b[1;34m";
const GREEN = "\x1b[32m";
const DIM = "\x1b[2m";

function answers(instance: Instance, target: ShellTarget, user: string): { banner: string; prompt: () => string; run: (line: string) => string | null } {
  const db = instance.odoo.database;
  const version = instance.odoo.version;
  const note = `${DIM}This is the demo: a simulated shell with a few answers. Try ls, odoo --version, psql -c "select 1", exit.${RESET}\r\n`;
  if (target === "psql") {
    return {
      banner: `psql (16.4)\r\nType "help" for help.\r\n\r\n`,
      prompt: () => `${db}=# `,
      run: (line) => {
        const sql = line.trim().replace(/;$/, "").toLowerCase();
        if (sql === "\\q" || sql === "exit" || sql === "quit") return null;
        if (sql === "select 1") return " ?column? \r\n----------\r\n        1\r\n(1 row)\r\n\r\n";
        if (sql === "\\dt") return "              List of relations\r\n Schema |      Name       | Type  | Owner \r\n--------+-----------------+-------+-------\r\n public | res_company     | table | odoo\r\n public | res_partner     | table | odoo\r\n public | res_users       | table | odoo\r\n public | sale_order      | table | odoo\r\n(4 rows)\r\n\r\n";
        if (sql.startsWith("select count(*) from res_users")) return " count \r\n-------\r\n    12\r\n(1 row)\r\n\r\n";
        if (!sql) return "";
        return `${DIM}The demo has no database: try select 1; \\dt or \\q.${RESET}\r\n`;
      },
    };
  }
  if (target === "odoo-shell") {
    return {
      banner: `${DIM}odoo shell -d ${db}${RESET}\r\nenv: <odoo.api.Environment object at 0x7f3a2c1b9e50>\r\nodoo: <module 'odoo' from '/usr/lib/python3/dist-packages/odoo/__init__.py'>\r\nopenerp: <module 'odoo' ...>\r\nself: res.users(1,)\r\nPython 3.12.3 (main) [GCC 13.2.0] on linux\r\nType "help", "copyright", "credits" or "license" for more information.\r\n(InteractiveConsole)\r\n`,
      prompt: () => ">>> ",
      run: (line) => {
        const code = line.trim();
        if (code === "exit()" || code === "quit()") return null;
        if (code === "self.env.user.name") return "'Administrator'\r\n";
        if (code.startsWith("env['res.users'].search_count") || code.startsWith('env["res.users"].search_count')) return "12\r\n";
        if (code === "env.cr.dbname") return `'${db}'\r\n`;
        if (!code) return "";
        return `${DIM}The demo has no Odoo running: try self.env.user.name, env['res.users'].search_count([]) or exit().${RESET}\r\n`;
      },
    };
  }
  const home = "/mnt/extra-addons";
  const sign = user === "root" ? "#" : "$";
  return {
    banner: note,
    prompt: () => `${GREEN}${user}@${instance.name}${RESET}:${BLUE}${home}${RESET}${sign} `,
    run: (line) => {
      const command = line.trim();
      if (command === "exit" || command === "logout") return null;
      if (command === "ls" || command === "ls -l") return `${BLUE}sale_custom${RESET}  ${BLUE}stock_extra${RESET}  ${BLUE}website_theme${RESET}  README.md\r\n`;
      if (command === "pwd") return `${home}\r\n`;
      if (command === "whoami") return `${user}\r\n`;
      if (command === "id") return user === "root" ? "uid=0(root) gid=0(root) groups=0(root)\r\n" : "uid=100(odoo) gid=101(odoo) groups=101(odoo)\r\n";
      if (command === "odoo --version") return `Odoo Server ${version}-20261112\r\n`;
      if (/^psql -c ["']select 1;?["']$/i.test(command)) return " ?column? \r\n----------\r\n        1\r\n(1 row)\r\n\r\n";
      if (command === "hostname") return `${instance.name}\r\n`;
      if (command === "date") return `${new Date().toUTCString()}\r\n`;
      if (command === "uname -a") return "Linux odoo 6.8.0 #1 SMP x86_64 GNU/Linux\r\n";
      if (command.startsWith("echo ")) return `${command.slice(5).replace(/^["']|["']$/g, "")}\r\n`;
      if (command === "help") return note;
      if (!command) return "";
      return `bash: ${command.split(/\s+/)[0]}: not in the demo. ${DIM}Try ls, odoo --version, psql -c "select 1".${RESET}\r\n`;
    },
  };
}

/** The shell's WebSocket in the demo: the messages of web/v1/shell_socket.py, answered by the simulated shell. */
export class DemoShellSocket extends EventTarget {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  readonly CONNECTING = 0;
  readonly OPEN = 1;
  readonly CLOSING = 2;
  readonly CLOSED = 3;
  readonly url: string;
  readyState = 0;
  protocol = "";
  binaryType: BinaryType = "blob";
  bufferedAmount = 0;
  extensions = "";
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  private line = "";
  private history: string[] = [];
  private shell: ReturnType<typeof answers> | null = null;
  private id = "";
  private target: ShellTarget = "shell";
  /** what was shown and typed, when the shell is recorded (asciicast events) */
  private events: ShellRecording["events"] | null = null;
  private started = 0;

  constructor(url: string | URL, protocols?: string | string[]) {
    super();
    this.url = String(url);
    const offered = typeof protocols === "string" ? [protocols] : (protocols ?? []);
    const token = offered.find((p) => p.startsWith("ticket."))?.slice(7) ?? "";
    setTimeout(() => this.connect(token, offered.includes("sudoo.shell.v1")), 120);
  }

  private emit(type: string, init: MessageEventInit | CloseEventInit = {}) {
    const event = type === "message" ? new MessageEvent("message", init) : type === "close" ? new CloseEvent("close", init) : new Event(type);
    const handler = (this as unknown as Record<string, ((e: Event) => void) | null>)[`on${type}`];
    handler?.(event);
    this.dispatchEvent(event);
  }

  private json(data: unknown) {
    this.emit("message", { data: JSON.stringify(data) });
  }

  private at() {
    return Math.round(Date.now() - this.started) / 1000;
  }

  private out(text: string) {
    this.events?.push([this.at(), "o", text]);
    const bytes = encoder.encode(text);
    this.emit("message", { data: this.binaryType === "arraybuffer" ? bytes.buffer : new Blob([bytes]) });
  }

  private connect(token: string, protocol: boolean) {
    const waiting = tickets.get(token);
    tickets.delete(token);
    if (!protocol || !waiting || waiting.expires < Date.now() || !session.signedIn) {
      this.readyState = 3;
      this.emit("error");
      this.emit("close", { code: 1006 });
      return;
    }
    const { instance, target, asRoot } = waiting;
    this.target = target;
    const user = asRoot ? "root" : USER[target];
    this.id = Array.from(crypto.getRandomValues(new Uint8Array(12)), (b) => b.toString(16).padStart(2, "0")).join("");
    this.readyState = 1;
    this.protocol = "sudoo.shell.v1";
    this.emit("open");
    const now = Date.now();
    const item: ShellSession = {
      id: this.id, instance: instance.name, target, container_user: user, root: asRoot, production: instance.stage === "production",
      user: ME, ip: null, started_at: iso(now)!, ended_at: null, duration_seconds: null, reason: null, recorded: recorded(instance, asRoot),
      recording: false, open: true,
    };
    open.set(this.id, { session: item, end: (reason) => this.finish(reason) });
    record(ME, "shell opened", { instance: instance.name, detail: `${target} as ${user}; ${item.recorded ? "recorded" : "not recorded"}; ${this.id}` });
    this.json({
      type: "ready", session: this.id, instance: instance.name, target, container: `odoo-${instance.name}-${target === "psql" ? "db" : "odoo"}-1`,
      container_user: user, user: ME, root: asRoot, production: item.production, recorded: item.recorded,
      idle_seconds: Number(setting("shell_idle_minutes") ?? 15) * 60, max_seconds: Number(setting("shell_max_minutes") ?? 60) * 60, started: Math.floor(now / 1000),
    });
    this.started = now;
    if (item.recorded) this.events = [];
    this.shell = answers(instance, target, user);
    this.out(this.shell.banner + this.shell.prompt());
  }

  send(data: string | ArrayBufferLike | Blob | ArrayBufferView) {
    if (this.readyState !== 1 || !this.shell) return;
    if (typeof data === "string") return; // resize: nothing to do here
    const bytes = data instanceof ArrayBuffer ? new Uint8Array(data) : ArrayBuffer.isView(data) ? new Uint8Array(data.buffer, data.byteOffset, data.byteLength) : null;
    if (!bytes) return;
    const text = new TextDecoder().decode(bytes);
    this.events?.push([this.at(), "i", text]);
    this.typed(text);
  }

  private typed(text: string) {
    const shell = this.shell!;
    // arrow keys: up and down walk the history; other escape sequences are ignored
    if (text === "\x1b[A" || text === "\x1b[B") {
      if (!this.history.length) return;
      const at = this.history.indexOf(this.line);
      const next = text === "\x1b[A" ? (at <= 0 ? this.history.length - 1 : at - 1) : at === -1 || at >= this.history.length - 1 ? -1 : at + 1;
      this.out("\b \b".repeat(this.line.length));
      this.line = next === -1 ? "" : this.history[next]!;
      this.out(this.line);
      return;
    }
    if (text.startsWith("\x1b")) return;
    for (const char of text) {
      if (char === "\r" || char === "\n") {
        this.out("\r\n");
        const line = this.line;
        this.line = "";
        if (line.trim()) this.history.push(line);
        if (line.trim() === "clear") {
          this.out("\x1b[2J\x1b[H" + shell.prompt());
          continue;
        }
        const answer = shell.run(line);
        if (answer === null) {
          if (this.target === "shell") this.out("logout\r\n");
          this.finish("exit");
          return;
        }
        this.out(answer + shell.prompt());
      } else if (char === "\x7f" || char === "\b") {
        if (this.line) {
          this.line = this.line.slice(0, -1);
          this.out("\b \b");
        }
      } else if (char === "\x03") {
        this.line = "";
        this.out("^C\r\n" + shell.prompt());
      } else if (char === "\x04") {
        if (!this.line) {
          this.finish("exit");
          return;
        }
      } else if (char === "\x0c") {
        this.out("\x1b[2J\x1b[H" + shell.prompt() + this.line);
      } else if (char >= " ") {
        this.line += char;
        this.out(char);
      }
    }
  }

  private finish(reason: string) {
    if (this.readyState !== 1) return;
    const found = open.get(this.id);
    open.delete(this.id);
    if (found) {
      const ended = Date.now();
      const started = new Date(found.session.started_at).getTime();
      const done: ShellSession = { ...found.session, open: false, ended_at: iso(ended), duration_seconds: Math.round((ended - started) / 1000), reason, recording: !!this.events };
      past.push(done);
      if (this.events) {
        this.events.push([this.at(), "m", `closed: ${reason}`]);
        recordings.set(this.id, { session: done, header: { version: 2, width: 100, height: 30, timestamp: Math.floor(started / 1000) }, events: this.events });
      }
      record(ME, "shell closed", { instance: found.session.instance, detail: `${found.session.target}; ${reason}; ${this.id}` });
    }
    this.json({ type: "closed", reason, detail: "" });
    this.readyState = 3;
    this.emit("close", { code: 1000, wasClean: true });
  }

  close() {
    this.finish("closed");
  }
}
