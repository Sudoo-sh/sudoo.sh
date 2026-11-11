// Entry point: page routing, periodic refresh, and one click handler that dispatches data-act buttons to the
// action tables of the other modules.
import { actions as accountActions } from "./accounts.js";
import { actions as backupActions } from "./backups.js";
import { $, api, app, copyText, esc, isAdmin, notify, state } from "./core.js";
import { actions as createActions } from "./create.js";
import { renderDashboard } from "./dashboard.js";
import { openJob } from "./dialogs.js";
import { actions as githubActions } from "./github.js";
import { actions as instanceActions, renderInstance } from "./instance.js";
import { actions as tokenActions } from "./tokens.js";

const ACTIONS = { ...accountActions, ...backupActions, ...createActions, ...githubActions, ...instanceActions, ...tokenActions };

function parseRoute() {
  const match = location.pathname.match(/^\/i\/([a-z0-9-]+)$/);
  state.page = match ? "instance" : "dashboard";
  state.name = match ? match[1] : null;
}

function go(path) {
  if (location.pathname !== path) history.pushState({}, "", path);
  parseRoute();
  $("#view").innerHTML = `<p class="muted">Loading...</p>`;
  window.scrollTo(0, 0);
  refresh(true);
}

async function refresh(full = false) {
  try {
    if (state.page === "dashboard") {
      state.overview = await api("/api/overview");
      renderDashboard();
    } else {
      state.detail = await api(`/api/instances/${state.name}`);
      renderInstance(full);
    }
  } catch (error) {
    if (error.status === 404) {
      $("#view").innerHTML = `<div class="box"><div class="empty"><p>This instance does not exist.</p><a href="/" data-go="/">Back to instances</a></div></div>`;
    } else if (full) {
      $("#view").innerHTML = `<div class="callout error"><b>The panel could not load this page.</b>${esc(error.message)}</div>`;
    }
  }
}

document.addEventListener("click", async (event) => {
  // a click anywhere, or on an entry of the menu, closes an open dropdown menu
  document.querySelectorAll("details.dropdown[open]").forEach((menu) => {
    if (!menu.contains(event.target) || event.target.closest(".dropdown-menu")) menu.open = false;
  });
  const target = event.target.closest("[data-close],[data-copy],[data-job],[data-act],[data-go]");
  if (!target || target.matches("a[target=_blank]")) return;
  if (target.dataset.close !== undefined) { target.closest("dialog").close(); return; }
  if (target.dataset.copy !== undefined) { copyText(target.dataset.copy, target.dataset.label); return; }
  if (target.dataset.job) { openJob(target.dataset.job); return; }
  event.preventDefault();
  if (target.dataset.go) {
    document.querySelectorAll("dialog[open]").forEach((d) => d.close());
    go(target.dataset.go);
    return;
  }
  try {
    await ACTIONS[target.dataset.act](target);
  } catch (error) {
    notify(error.message, "error");
  }
});

window.addEventListener("popstate", () => { parseRoute(); refresh(true); });
setInterval(() => { if (!document.hidden && !document.querySelector("dialog[open]")) refresh(); }, 5000);

state.me = await api("/api/me");
$("#me-name").textContent = state.me.user;
$("#me-initial").textContent = state.me.user.charAt(0);
$("#me-role").textContent = `(${state.me.role_label})`;
document.querySelectorAll("[data-admin]").forEach((el) => { el.hidden = !isAdmin(); });
Object.assign(app, { refresh, go });
parseRoute();
refresh(true);
