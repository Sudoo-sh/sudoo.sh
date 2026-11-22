// The demo's network: window.fetch and window.EventSource answer /api/v1 and /healthz from the in-browser backend
// (backend/api.ts), so the web app runs unchanged and nothing is sent anywhere. Links and downloads that would leave
// the demo are stopped with a short explanation instead.

import { answer, jobOfLog, odooLog } from "./backend/api";
import { subscribe, type Topic } from "./backend/bus";
import { DemoShellSocket } from "./backend/shell";
import { ASLEEP, instances, logs, roleInfo, session } from "./backend/state";
import { websiteLink } from "./referrer";

const ours = (url: URL) => url.origin === location.origin && (url.pathname.startsWith("/api/") || url.pathname === "/healthz");

/** A little latency, as over a network: loading states show for a moment instead of never. */
const latency = () => new Promise((resolve) => setTimeout(resolve, 60 + Math.random() * 140));

function installFetch() {
  const realFetch = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(input instanceof Request ? input.url : String(input), location.href);
    if (!ours(url)) return realFetch(input, init);
    await latency();
    if (url.pathname === "/healthz") return new Response("ok", { status: 200, headers: { "Content-Type": "text/plain" } });
    const method = (init?.method ?? (input instanceof Request ? input.method : "GET")).toUpperCase();
    const text = typeof init?.body === "string" ? init.body : "";
    let body: unknown;
    try {
      body = text ? JSON.parse(text) : undefined;
    } catch {
      return json(400, { type: "about:blank", title: "Bad Request", status: 400, detail: "The request is not JSON." });
    }
    // a piece of an uploaded file (Import database): the backend only needs its size
    if (init?.body instanceof Blob) body = { size: init.body.size };
    const result = await answer(method, url.pathname.replace(/^\/api\/v1/, ""), url.searchParams, body);
    if (result.status === 204) return new Response(null, { status: 204, headers: result.headers });
    return json(result.status, result.body, result.headers);
  };
}

const json = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": status >= 400 ? "application/problem+json" : "application/json", ...headers },
  });

/** GET /api/v1/events in the browser: the backend's bus as Server-Sent Events, framed as the real stream frames them. */
class DemoEventSource extends EventTarget {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  readonly CONNECTING = 0;
  readonly OPEN = 1;
  readonly CLOSED = 2;
  readonly url: string;
  readonly withCredentials = false;
  readyState = 0;
  onopen: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  private stop: (() => void) | null = null;
  private timer: ReturnType<typeof setTimeout>;

  constructor(url: string | URL) {
    super();
    this.url = new URL(String(url), location.href).href;
    this.timer = setTimeout(() => this.open(), 80);
  }

  private open() {
    if (this.readyState === 2) return;
    if (!session.signedIn) {
      this.readyState = 2;
      const event = new Event("error");
      this.onerror?.(event);
      this.dispatchEvent(event);
      return;
    }
    this.readyState = 1;
    const opened = new Event("open");
    this.onopen?.(opened);
    this.dispatchEvent(opened);
    this.stop = subscribe((topic: Topic, key, data, id) => {
      // alerts reach only those who may read the audit log, as on the real stream
      if (topic === "alert" && !roleInfo(session.role).permissions.includes("audit.view")) return;
      const message = new MessageEvent(topic, { data: JSON.stringify({ key, data }), lastEventId: String(id) });
      this.dispatchEvent(message);
    });
  }

  close() {
    clearTimeout(this.timer);
    this.readyState = 2;
    this.stop?.();
    this.stop = null;
  }
}

const LOG_STREAM = /^\/api\/v1\/instances\/([^/]+)\/logs\/stream$/;

/** GET /api/v1/instances/<name>/logs/stream: Odoo's sample log, a few new lines every second or two (filtered as the
 *  server filters: plain text, any case); a job's log is sent whole and ends. */
class DemoLogSource extends EventTarget {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  readonly CONNECTING = 0;
  readonly OPEN = 1;
  readonly CLOSED = 2;
  readonly url: string;
  readonly withCredentials = false;
  readyState = 0;
  onopen: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  private timers: ReturnType<typeof setTimeout>[] = [];
  private sent = 0;

  constructor(url: string | URL, name: string) {
    super();
    this.url = new URL(String(url), location.href).href;
    const query = new URL(this.url).searchParams;
    const log = query.get("log") ?? "odoo.log";
    const wanted = (query.get("q") ?? "").trim().toLowerCase();
    const keep = (line: string) => !wanted || line.toLowerCase().includes(wanted);
    const tail = Number(query.get("tail") ?? 300);
    this.timers.push(
      setTimeout(() => {
        const instance = instances.get(name);
        if (!session.signedIn || !instance) return this.fail();
        this.readyState = 1;
        const opened = new Event("open");
        this.onopen?.(opened);
        this.dispatchEvent(opened);
        if (log !== "odoo.log") {
          const job = jobOfLog(name, log);
          const text = job ? (logs.get(job.id) ?? "") : "";
          this.send("lines", { lines: text.split("\n").filter(keep).slice(-tail || undefined) });
          this.send("end", { reason: "finished" });
          return;
        }
        if (ASLEEP.includes(instance.status)) {
          this.send("lines", { lines: [] });
          this.send("end", { reason: "stopped" });
          return;
        }
        this.send("lines", { lines: tail ? odooLog(instance, Math.max(tail, 50)).trimEnd().split("\n").filter(keep).slice(-tail) : [] });
        const more = () => {
          if (this.readyState === 2) return;
          const fresh = odooLog(instance, 1 + (this.sent % 3)).trimEnd().split("\n").filter(keep);
          if (fresh.length) this.send("lines", { lines: fresh });
          this.timers.push(setTimeout(more, 900 + Math.random() * 1400));
        };
        this.timers.push(setTimeout(more, 1200));
      }, 120),
    );
  }

  private send(event: string, data: unknown) {
    this.sent++;
    const message = new MessageEvent(event, { data: JSON.stringify(data), lastEventId: String(this.sent) });
    this.dispatchEvent(message);
  }

  private fail() {
    this.readyState = 2;
    const event = new Event("error");
    this.onerror?.(event);
    this.dispatchEvent(event);
  }

  close() {
    this.readyState = 2;
    for (const timer of this.timers) clearTimeout(timer);
  }
}

function installEventSource() {
  const RealEventSource = window.EventSource;
  const Demo = function (url: string | URL, init?: EventSourceInit) {
    const address = new URL(String(url), location.href);
    if (!ours(address)) return new RealEventSource(url, init);
    const followed = LOG_STREAM.exec(address.pathname);
    if (followed) return new DemoLogSource(url, decodeURIComponent(followed[1]!));
    return new DemoEventSource(url);
  } as unknown as typeof EventSource;
  Object.assign(Demo, { CONNECTING: 0, OPEN: 1, CLOSED: 2 });
  window.EventSource = Demo;
}

// ---------------------------------------------------------------- links that would leave the demo

type Notify = (title: string, text: string) => void;
let notify: Notify = () => undefined;
export const setNotifier = (fn: Notify) => {
  notify = fn;
};

/** The sudoo.sh sites open normally; everything else outside the demo is fictional. */
const ALLOWED = new Set(["sudoo.sh", "www.sudoo.sh", "docs.sudoo.sh"]);

function why(url: URL, link: HTMLAnchorElement): [string, string] | null {
  if (url.origin === location.origin) {
    if (link.hasAttribute("download") || url.pathname.startsWith("/api/"))
      return ["Downloads are off in the demo", "The backups and files here are sample data. On your own server this downloads the file."];
    return null;
  }
  if (ALLOWED.has(url.hostname) || url.href === websiteLink()) return null;
  if (url.hostname.endsWith(".demo.sudoo.sh"))
    return ["There is no Odoo behind this instance", "The instances in the demo are sample data. On your own server this opens the instance's Odoo."];
  if (url.hostname === "github.com" || url.hostname.endsWith(".github.com"))
    return ["GitHub is not connected in the demo", "The repositories and the GitHub App here are fictional. On your own server this opens GitHub."];
  if (url.protocol === "mailto:" || url.protocol === "tel:") return null;
  return ["Links outside the demo are off", "This page would leave the demo. On your own server it opens normally."];
}

function guardLinks() {
  document.addEventListener(
    "click",
    (event) => {
      const link = (event.target as Element | null)?.closest?.("a[href]") as HTMLAnchorElement | null;
      if (!link) return;
      let url: URL;
      try {
        url = new URL(link.href, location.href);
      } catch {
        return;
      }
      const reason = why(url, link);
      if (!reason) return;
      event.preventDefault();
      event.stopPropagation();
      notify(...reason);
    },
    true,
  );
  // the GitHub App manifest is posted to GitHub as a form; the demo's backend refuses it before, this is a fallback
  document.addEventListener(
    "submit",
    (event) => {
      const form = event.target as HTMLFormElement;
      if (new URL(form.action, location.href).origin !== location.origin) {
        event.preventDefault();
        notify("GitHub is not connected in the demo", "On your own server this continues on GitHub.");
      }
    },
    true,
  );
}

/** The web shell's WebSocket (/api/v1/instances/<name>/shell/socket) answered by the simulated shell; any other
 *  WebSocket is the browser's own. */
function installWebSocket() {
  const RealWebSocket = window.WebSocket;
  const Demo = function (url: string | URL, protocols?: string | string[]) {
    const address = new URL(String(url), location.href);
    if (address.host === location.host && /^\/api\/v1\/instances\/[^/]+\/shell\/socket$/.test(address.pathname)) {
      return new DemoShellSocket(url, protocols);
    }
    return new RealWebSocket(url, protocols);
  } as unknown as typeof WebSocket;
  Object.assign(Demo, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });
  window.WebSocket = Demo;
}

export function installTransport() {
  installFetch();
  installEventSource();
  installWebSocket();
  guardLinks();
}
