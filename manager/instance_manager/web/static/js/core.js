// Shared helpers: API calls, escaping, formatting, notifications, small charts.
// All markup is built from escaped strings; no inline styles or handlers (strict Content-Security-Policy).

export const $ = (selector, root = document) => root.querySelector(selector);

export const state = { me: null, page: "dashboard", name: null, tab: "usage", overview: null, detail: null, versions: null, branches: {} };

// router.js fills these in; other modules call them after an action
export const app = { refresh: () => {}, go: () => {} };

export const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[c]));

export const isAdmin = () => state.me?.role === "admin";

export async function api(path, body) {
  const options = { headers: {} };
  if (body !== undefined) {
    options.method = "POST";
    options.body = JSON.stringify(body);
    options.headers["Content-Type"] = "application/json";
    options.headers["X-CSRF-Token"] = state.me?.csrf || "";
  }
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (response.status === 401 && data.login) {
    window.location.replace("/login");
    throw new Error(data.error);
  }
  if (!response.ok) {
    const error = new Error(data.error || `The request failed (error ${response.status}).`);
    error.status = response.status;
    throw error;
  }
  return data;
}

export function notify(message, kind = "") {
  const el = document.createElement("div");
  el.className = `notice ${kind}`;
  el.textContent = message;
  $("#notices").append(el);
  setTimeout(() => el.remove(), kind === "error" ? 9000 : 4000);
}

export async function copyText(text, label = "Copied to the clipboard.") {
  try {
    await navigator.clipboard.writeText(text);
    notify(label, "ok");
  } catch {
    notify("Copying is not possible here. Select the text and copy it manually.", "error");
  }
}

export function contactSentence() {
  const c = state.me?.contact || {};
  const details = [c.phone, c.email].filter(Boolean).join(", ");
  return `Please contact the administrator, ${c.name}${details ? ` (${details})` : ""}.`;
}

// ------------------------------------------------------------------ formatting

export function formatBytes(bytes) {
  if (bytes == null) return "-";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (bytes >= 1024 && i < units.length - 1) { bytes /= 1024; i += 1; }
  return `${bytes.toFixed(bytes < 10 && i > 0 ? 1 : 0)} ${units[i]}`;
}

export const formatPercent = (v) => `${(v ?? 0).toFixed((v ?? 0) < 10 ? 1 : 0)}%`;

export function formatDuration(seconds) {
  seconds = Math.max(0, Math.round(seconds));
  if (seconds < 60) return `${seconds} s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = minutes / 60;
  if (hours < 48) return `${hours < 10 ? hours.toFixed(1).replace(/\.0$/, "") : Math.round(hours)} h`;
  return `${Math.round(hours / 24)} days`;
}

export const ago = (ts) => (ts ? `${formatDuration(Date.now() / 1000 - ts)} ago` : "never");
export const timeOf = (ts) => new Date(ts * 1000).toLocaleString([], { dateStyle: "short", timeStyle: "short" });
export const versionLabel = (version, edition) => `${version} ${edition === "enterprise" ? "Enterprise" : "Community"}`;

// ------------------------------------------------------------------ labels

export const STATUS = {
  online: ["Running", "Running and ready to use."],
  starting: ["Starting", "Odoo is starting. This usually takes less than a minute."],
  busy: ["Working", "An action is in progress on this instance."],
  paused: ["Paused", "Paused to save server resources. It starts automatically when someone opens its link."],
  switched_off: ["Switched off", "Switched off by the administrator. It does not start automatically."],
  stopped: ["Stopped", "Odoo stopped unexpectedly. Restart it; if the problem returns, check the logs."],
  unknown: ["Checking", "Reading the current state."],
};
export const statusBadge = (s) => `<span class="status status-${esc(s)}" title="${esc(STATUS[s][1])}">${esc(STATUS[s][0])}</span>`;
export const RESULT = { success: "Completed", failed: "Failed", running: "In progress", queued: "Waiting" };
export const whoLabel = (user) => ({ visitor: "Visitor", system: "Automatic" }[user] || user);

// ------------------------------------------------------------------ charts (SVG, styled by app.css)

export function meter(percent) {
  const p = Math.max(0, Math.min(100, percent || 0));
  return `<svg class="meter${p > 85 ? " high" : ""}" viewBox="0 0 100 6" preserveAspectRatio="none" aria-hidden="true">
    <rect class="track" width="100" height="6" rx="3"/><rect class="fill" width="${p.toFixed(1)}" height="6" rx="3"/></svg>`;
}

export function sparkline(values, max) {
  const w = 200;
  const h = 32;
  const base = `<line x1="0" y1="${h - 1}" x2="${w}" y2="${h - 1}"/>`;
  if (values.length < 2) return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">${base}</svg>`;
  const top = max ?? (Math.max(...values) * 1.2 || 1);
  const points = values.map((v, i) => `${(i * w / (values.length - 1)).toFixed(1)},${(h - 2 - Math.min(v / top, 1) * (h - 4)).toFixed(1)}`);
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">
    ${base}<path d="M${points.join("L")}" vector-effect="non-scaling-stroke"/></svg>`;
}

export const linkOrPending = (url) => (url
  ? `<a class="mono" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(url.replace(/^https:\/\//, ""))}</a>`
  : `<span class="muted">Link is being created</span>`);
