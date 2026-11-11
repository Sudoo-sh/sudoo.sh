// The page of one instance: overview, usage, actions, code, automation, activity, technical details.
import {
  $, STATUS, ago, api, app, contactSentence, esc, formatBytes, formatDuration, formatPercent, isAdmin, notify,
  sparkline, state, statusBadge, timeOf, versionLabel,
} from "./core.js";
import { activityTable } from "./dashboard.js";
import { askForm, openJob, showInfo } from "./dialogs.js";
import { runAccessCheck } from "./github.js";

const TABS = [   // notebook pages: [id, title, administrators only]
  ["usage", "Resource usage", false],
  ["actions", "Actions", false],
  ["code", "Code", false],
  ["activity", "Activity", false],
  ["automation", "Automation", true],
  ["technical", "Technical details", true],
];
// the status bar shows where an instance is in its life; transient states are shown by the badge
const STAGES = [["running", "Running"], ["paused", "Paused"], ["switched_off", "Switched off"]];
const STAGE_OF = { online: "running", starting: "running", busy: "running", paused: "paused", switched_off: "switched_off" };

export function renderInstance(full) {
  const d = state.detail;
  if (full || $("#inst")?.dataset.name !== d.name) renderFrame(d);
  const busy = d.status === "busy";
  const running = ["online", "starting", "busy"].includes(d.status);

  $("#inst-buttons").innerHTML = `
    ${d.url ? `<a class="btn primary" href="${esc(d.url)}" target="_blank" rel="noopener noreferrer">Open Odoo</a>` : ""}
    ${["paused", "stopped"].includes(d.status) ? `<button class="btn" data-act="start" ${busy ? "disabled" : ""}>Start</button>` : ""}
    <button class="btn" data-act="restart" ${busy || d.status === "switched_off" ? "disabled" : ""}>Restart</button>
    <button class="btn" data-act="update" ${busy || !d.repo ? "disabled" : ""}>Get latest code</button>
    <button class="btn" data-act="logs">View logs</button>`;
  $("#inst-statusbar").innerHTML = STAGES.map(([id, label]) =>
    `<span class="${STAGE_OF[d.status] === id ? "current" : ""}">${esc(label)}</span>`).join("");

  $("#inst-job").innerHTML = d.job ? `<div class="callout info"><b><span class="spinner"></span>${esc(d.job.action)} is in progress</b>
    ${esc(d.job.steps.length ? d.job.steps[d.job.steps.length - 1].title : "Starting")}.
    <button class="btn link" data-job="${esc(d.job.id)}">Show progress</button></div>` : "";

  $("#inst-title").innerHTML = `<h1>${esc(d.name)}</h1>${statusBadge(d.status)}`;
  $("#inst-link").innerHTML = d.url
    ? `<span class="inline-copy"><a class="mono" href="${esc(d.url)}" target="_blank" rel="noopener noreferrer">${esc(d.url)}</a>
       <button class="btn" data-copy="${esc(d.url)}">Copy link</button></span>
       <div class="small muted">The link changes when the server restarts; this page always shows the current one.
       <a href="/docs/docs/concepts/TUNNELING.md" target="_blank" rel="noopener">Why?</a></div>`
    : `<span class="muted">The link is being created.</span>`;

  const pauseText = d.status === "online" && d.pauses_at
    ? `Pauses in ${formatDuration(d.pauses_at - Date.now() / 1000)} if nobody opens it`
    : (d.auto_pause ? `On (after ${d.idle_hours} h without visits)` : "Off");
  $("#inst-overview").innerHTML = `<div class="groups">
    <dl class="dl">
      <dt>Status</dt><dd>${esc(STATUS[d.status][1])}</dd>
      <dt>Odoo</dt><dd>${esc(versionLabel(d.version, d.edition))}</dd>
      <dt>Branch</dt><dd class="mono">${esc(d.branch)}</dd>
      <dt>Database</dt><dd class="mono">${esc(d.db_name)}</dd>
    </dl>
    <dl class="dl">
      ${isAdmin() ? `<dt>Odoo login</dt><dd class="inline-copy"><span class="mono">admin</span> / <span class="mono" id="pw">hidden</span>
        <button class="btn" data-act="reveal">Show</button><button class="btn" data-copy="${esc(d.admin_password)}" data-label="Password copied.">Copy</button></dd>` : ""}
      <dt>Running for</dt><dd>${running ? esc((d.uptime || "-").replace(/^Up /, "")) : "Not running"}</dd>
      <dt>Last visit</dt><dd>${ago(d.last_visit)}</dd>
      <dt>Automatic pause</dt><dd>${esc(pauseText)}</dd>
      <dt>Created</dt><dd>${esc(timeOf(d.created_at))}</dd>
      <dt>Lifetime</dt><dd>${d.expires_at
        ? `Deleted on ${esc(timeOf(d.expires_at))} (in ${formatDuration(d.expires_at - Date.now() / 1000)}), after a backup`
        : "Kept until deleted by hand"}</dd>
    </dl></div>
    ${d.branch_missing ? `<div class="callout warning"><b>The branch ${esc(d.branch)} no longer exists on GitHub.</b>
      It was probably merged and deleted, so this instance cannot receive new code.
      ${isAdmin() ? `Use <b>Actions, Switch branch</b> to follow another branch, for example main.` : esc(contactSentence())}</div>` : ""}
    ${d.gateway_error ? `<div class="callout error"><b>This instance cannot be reached.</b>${esc(d.gateway_error)} ${isAdmin() ? "" : esc(contactSentence())}</div>` : ""}`;

  $("#inst-usage").innerHTML = `<div class="box-head"><h2>Resource usage</h2><p>Last 15 minutes, updated every few seconds</p></div>
    <div class="stats">
      <div class="stat"><div class="label">CPU (share of server)</div><div class="value">${running ? formatPercent(d.cpu) : "-"}</div>${sparkline(d.history.map((x) => x[1]))}</div>
      <div class="stat"><div class="label">Memory (Odoo and database)</div><div class="value">${running ? formatBytes(d.memory) : "-"}</div>${sparkline(d.history.map((x) => x[2]))}</div>
      <div class="stat"><div class="label">Database size</div><div class="value">${formatBytes(d.disk.db)}</div><div class="note">${d.disk.db == null ? "Measured while running" : "Data stored in PostgreSQL"}</div></div>
      <div class="stat"><div class="label">File storage</div><div class="value">${formatBytes(d.disk.files)}</div><div class="note">Attachments and images</div></div>
    </div>`;

  $("#inst-actions").innerHTML = renderActions(d, busy);
  $("#inst-code").innerHTML = renderCode(d, busy);
  $("#inst-activity").innerHTML = `<div class="box-head"><h2>Activity</h2><p>Select an entry to see its details</p></div>${activityTable(d.jobs, false)}`;
}

function renderFrame(d) {
  const tabs = TABS.filter(([, , adminOnly]) => !adminOnly || isAdmin());
  if (!tabs.some(([id]) => id === state.tab)) state.tab = tabs[0][0];
  $("#view").innerHTML = `<div id="inst" data-name="${esc(d.name)}">
    <div class="page-head">
      <div class="crumbs"><a href="/" data-go="/">Instances</a><span>/</span>${esc(d.name)}</div>
      ${isAdmin() ? `<details class="dropdown"><summary class="btn">Actions</summary><div class="dropdown-menu">
        <button type="button" data-act="switch-branch">Switch branch</button>
        <button type="button" class="danger" data-act="delete">Delete instance</button></div></details>` : ""}
    </div>
    <div class="form-header"><div class="btn-row" id="inst-buttons"></div><div class="statusbar" id="inst-statusbar"></div></div>
    <div class="sheet">
      <div id="inst-job"></div>
      <div class="sheet-title" id="inst-title"></div>
      <div class="sheet-subtitle" id="inst-link"></div>
      <div id="inst-overview"></div>
      <div class="notebook" role="tablist">${tabs.map(([id, title]) =>
        `<button type="button" role="tab" data-act="tab" data-arg="${id}">${esc(title)}</button>`).join("")}</div>
      <section class="box" data-pane="usage" id="inst-usage"></section>
      <section class="box" data-pane="actions" id="inst-actions"></section>
      <section class="box" data-pane="code" id="inst-code"></section>
      <section class="box" data-pane="activity" id="inst-activity"></section>
      ${isAdmin() ? `<section class="box" data-pane="automation" id="inst-settings"></section>
        <section class="box" data-pane="technical" id="inst-tech"></section>` : ""}
    </div>
  </div>`;
  showTab(state.tab);
  if (isAdmin()) {
    renderSettings(d);
    renderTechnical(d);
  }
}

function showTab(id) {
  state.tab = id;
  document.querySelectorAll("#inst [data-pane]").forEach((pane) => { pane.hidden = pane.dataset.pane !== id; });
  document.querySelectorAll("#inst .notebook button").forEach((b) => {
    b.classList.toggle("active", b.dataset.arg === id);
    b.setAttribute("aria-selected", String(b.dataset.arg === id));
  });
}

function actionRow(act, title, description, { button = title, primary = false, danger = false, disabled = false } = {}) {
  const cls = primary ? "btn primary" : danger ? "btn danger" : "btn";
  return `<div class="action"><div><div class="t">${esc(title)}</div><div class="d">${esc(description)}</div></div>
    <button class="${cls}" data-act="${esc(act)}" ${disabled ? "disabled" : ""}>${esc(button)}</button></div>`;
}

function renderActions(d, busy) {
  let html = `<div class="box-head"><h2>Actions</h2></div>
    <div class="section-label">Code from GitHub</div>
    ${actionRow("update", "Get latest code", "Downloads new changes from GitHub, upgrades the modules that changed and restarts Odoo.", { button: "Update", primary: true, disabled: busy || !d.repo })}
    ${actionRow("update-submodules", "Update submodules", "Moves every submodule to its newest version and restarts Odoo.", { button: "Update", disabled: busy || !d.submodules.length })}
    <div class="section-label">Instance</div>
    ${actionRow("restart", "Restart", "Stops and starts Odoo. Takes about 30 seconds.", { disabled: busy })}`;
  if (["paused", "stopped"].includes(d.status)) {
    html += actionRow("start", "Start now", "Starts Odoo now instead of waiting for the first visitor.", { button: "Start", disabled: busy });
  }
  html += actionRow("logs", "View logs", "The latest messages written by Odoo. Useful when something does not work.", { button: "View" });
  if (!isAdmin()) return `${html}<div class="box-body small muted">Other actions are reserved for the administrator. ${esc(contactSentence())}</div>`;
  html += `<div class="section-label">Administrator</div>`;
  if (d.status === "switched_off") {
    html += actionRow("start", "Switch on", "Starts this Odoo again.", { button: "Switch on", disabled: busy });
  } else {
    if (!["paused", "stopped"].includes(d.status)) html += actionRow("pause", "Pause", "Frees memory now. It starts again when someone opens it.", { disabled: busy });
    html += actionRow("switch-off", "Switch off", "Stops it completely. It does not start when someone opens it.", { button: "Switch off", danger: true, disabled: busy });
  }
  return html + actionRow("upgrade", "Upgrade modules", "Applies module changes in Odoo, like Upgrade in the Apps menu.", { button: "Choose modules", disabled: busy })
    + actionRow("backup", "Back up now", "Saves the database and files as a backup that Odoo can restore. See Backups in the top bar.", { button: "Back up", disabled: busy });
}

function renderCode(d, busy) {
  const c = d.commit;
  return `<div class="box-head"><h2>Code</h2>
      <button class="btn" data-act="add-submodule" ${busy || !d.repo ? "disabled" : ""}>Add submodule</button></div>
    <div class="box-body"><dl class="dl">
      <dt>Repository</dt><dd class="mono">${esc(d.repo || "None")}</dd>
      <dt>Branch</dt><dd class="mono">${esc(d.branch)}</dd>
      <dt>Latest change</dt><dd>${c ? `${esc(c.subject)}<div class="small muted">${esc(c.author)}, ${esc(c.when)} (${esc(c.sha)})</div>` : "-"}</dd>
    </dl></div>
    ${d.commits.length > 1 ? `<div class="section-label">Recent changes</div><div class="table-wrap"><table><tbody>
      ${d.commits.map((m) => `<tr><td class="mono">${esc(m.sha)}</td><td>${esc(m.subject)}<div class="small muted">${esc(m.author)}, ${esc(m.when)}</div></td></tr>`).join("")}
      </tbody></table></div>` : ""}
    <div class="section-label">Submodules (${d.submodules.length})</div>
    ${d.submodules.length ? `<div class="table-wrap"><table><tbody>${d.submodules.map((s) => `<tr>
      <td><div class="mono">${esc(s.path)}</div><div class="small muted">${esc(s.url)}</div></td><td class="mono">${esc(s.sha)}</td>
      ${isAdmin() ? `<td><button class="btn danger" data-act="remove-submodule" data-arg="${esc(s.path)}" ${busy ? "disabled" : ""}>Remove</button></td>` : ""}</tr>`).join("")}
      </tbody></table></div>` : `<div class="box-body small muted">No submodules. Submodules include other repositories, such as OCA modules, in this instance.</div>`}`;
}

function renderSettings(d) {
  $("#inst-settings").innerHTML = `<div class="box-head"><h2>Automation</h2></div>
    <form class="box-body" id="settings-form">
      <label class="check"><input type="checkbox" name="auto_update" ${d.auto_update ? "checked" : ""}>
        <span>Update automatically from GitHub<span class="d">Checks GitHub every ${esc(d.poll_minutes)} minutes and updates when there is new code.</span></span></label>
      <label class="check"><input type="checkbox" name="auto_upgrade" ${d.auto_upgrade ? "checked" : ""}>
        <span>Upgrade changed modules<span class="d">After an update, upgrades only the modules whose files changed.</span></span></label>
      <label class="check"><input type="checkbox" name="auto_pause" ${d.auto_pause ? "checked" : ""}>
        <span>Pause when not used<span class="d">Pauses after ${esc(d.idle_hours)} hours without visits and starts again on the next visit.</span></span></label>
      <label class="check"><input type="checkbox" name="auto_delete" ${d.delete_after_days > 0 ? "checked" : ""}>
        <span>Delete automatically<span class="d">Deletes the instance this many days after it was created. A backup of the
          database and files is taken first. Untick to keep it until it is deleted by hand.</span></span></label>
      <label class="field" for="s-days">Days after creation</label>
      <input class="input" id="s-days" name="delete_after_days" type="number" min="1" max="3650" value="${esc(d.delete_after_days || 30)}">
      <div class="form-actions"><button class="btn" type="button" data-act="switch-branch" ${d.repo ? "" : "disabled"}>Switch branch</button>
        <button class="btn primary" type="submit">Save</button></div>
    </form>`;
  $("#settings-form").onsubmit = async (event) => {
    event.preventDefault();
    const form = event.target.elements;
    try {
      await api(`/api/instances/${d.name}/settings`, {
        auto_update: form.auto_update.checked, auto_upgrade: form.auto_upgrade.checked, auto_pause: form.auto_pause.checked,
        delete_after_days: form.auto_delete.checked ? Number(form.delete_after_days.value) : 0,
      });
      notify("Settings saved.", "ok");
      app.refresh();
    } catch (error) {
      notify(error.message, "error");
    }
  };
}

function renderTechnical(d) {
  $("#inst-tech").innerHTML = `<div class="box-head"><h2>Technical details</h2></div><div class="box-body"><dl class="dl">
    <dt>Folder</dt><dd><code>/opt/odoo/instances/${esc(d.name)}</code></dd>
    <dt>Ports (127.0.0.1)</dt><dd>gateway ${esc(d.ports.GATEWAY_PORT)}, Odoo ${esc(d.ports.ODOO_PORT)}, connector status ${esc(d.ports.CONNECTOR_METRICS_PORT)}</dd>
    <dt>Master password</dt><dd class="inline-copy"><code>hidden</code><button class="btn" data-copy="${esc(d.master_password)}" data-label="Master password copied.">Copy</button>
      <span class="small muted">Stored encrypted; Odoo keeps only its hash</span></dd>
    </dl></div>`;
}

// ------------------------------------------------------------------ actions of this page

async function run(name, action, body = {}) {
  const result = await api(`/api/instances/${name}/${action}`, body);
  openJob(result.job);
  app.refresh();
}

export const actions = {
  tab: (t) => showTab(t.dataset.arg),
  update: (t) => run(t.dataset.arg || state.detail.name, "update"),
  "update-submodules": () => run(state.detail.name, "update-submodules"),
  restart: (t) => run(t.dataset.arg || state.detail.name, "restart"),
  start: () => run(state.detail.name, "start"),
  pause: () => run(state.detail.name, "pause"),
  backup: () => run(state.detail.name, "backup"),
  reveal: (t) => {
    const pw = $("#pw");
    const shown = pw.dataset.shown === "1";
    pw.textContent = shown ? "hidden" : state.detail.admin_password;
    pw.dataset.shown = shown ? "0" : "1";
    t.textContent = shown ? "Show" : "Hide";
  },
  logs: async () => {
    const name = state.detail.name;
    showInfo(`Logs: ${name}`, `<p class="muted">The latest 300 lines written by Odoo. Lines with ERROR or Traceback usually explain a problem.</p>
      <div class="btn-row"><button class="btn" id="logs-reload">Reload</button></div><pre class="log" id="logs-pre">Loading...</pre>`);
    const load = async () => {
      const { log } = await api(`/api/instances/${name}/logs`);
      $("#logs-pre").textContent = log;
      $("#logs-pre").scrollTop = $("#logs-pre").scrollHeight;
    };
    $("#logs-reload").onclick = () => load().catch((e) => notify(e.message, "error"));
    await load();
  },
  "switch-off": () => askForm({
    title: `Switch off ${state.detail.name}`, ok: "Switch off", danger: true,
    html: `<p>Nobody can use this Odoo until it is switched on again here. Unlike Pause, it does not start when someone opens it.</p>`,
    onSubmit: () => run(state.detail.name, "switch-off"),
  }),
  upgrade: () => askForm({
    title: "Upgrade modules", ok: "Upgrade",
    html: `<p class="muted">Enter the technical names of the modules, separated by commas. Odoo is unavailable for a short time while they are upgraded.</p>
      <label class="field" for="f-modules">Modules</label><input class="input mono" id="f-modules" name="modules" maxlength="4000" required placeholder="sale_custom, stock_custom">
      <div class="help">Enter "all" to upgrade every module. This can take several minutes.</div>`,
    onSubmit: (values) => run(state.detail.name, "upgrade", values),
  }),
  "switch-branch": async () => {
    const d = state.detail;
    const info = await api(`/api/repo-branches?url=${encodeURIComponent(d.repo)}`);
    askForm({
      title: "Switch branch", ok: "Switch branch",
      html: `<p class="muted">The code switches to the chosen branch and Odoo restarts.</p>
        <label class="field" for="f-branch">Branch</label><select class="input" id="f-branch" name="branch">
        ${info.branches.map((b) => `<option ${b === d.branch ? "selected" : ""}>${esc(b)}</option>`).join("")}</select>`,
      onSubmit: (values) => run(d.name, "switch-branch", values),
    });
  },
  "add-submodule": () => {
    const d = state.detail;
    askForm({
      title: "Add submodule", ok: "Add submodule",
      html: `<p class="muted">Includes another repository, for example an OCA module collection, in this instance. The change is also saved to the repository on GitHub.</p>
        <label class="field" for="f-url">Repository address</label><input class="input mono" id="f-url" name="url" maxlength="200" required placeholder="https://github.com/OCA/web">
        <div class="grid2"><div><label class="field" for="f-path">Folder (optional)</label><input class="input mono" id="f-path" name="path" maxlength="100" placeholder="oca/web"></div>
        <div><label class="field" for="f-branch">Branch (optional)</label><input class="input mono" id="f-branch" name="branch" maxlength="100" placeholder="${esc(d.version)}"></div></div>
        <div class="help">For OCA repositories use the branch that matches the Odoo version (${esc(d.version)}).</div>
        <div id="f-access"></div>`,
      onSubmit: async (values) => {
        state.recheck = () => runAccessCheck($("#f-access"), values.url, values.branch);
        const report = await runAccessCheck($("#f-access"), values.url, values.branch);
        if (!report.all_ok) throw new Error("The server cannot read this repository yet. See Repository access above.");
        return run(d.name, "add-submodule", values);
      },
    });
  },
  "remove-submodule": (t) => askForm({
    title: "Remove submodule", ok: "Remove", danger: true,
    html: `<p>Removes <code>${esc(t.dataset.arg)}</code> from this instance and from the repository on GitHub. Uninstall its modules in Odoo first.</p>`,
    onSubmit: () => run(state.detail.name, "remove-submodule", { path: t.dataset.arg }),
  }),
  delete: () => {
    const name = state.detail.name;
    askForm({
      title: `Delete ${name}`, ok: "Delete permanently", danger: true,
      html: `<div class="callout error">This deletes the instance, its database and all its files. A backup of the database and
        files is taken first and kept under <b>Backups</b>; if the backup fails, nothing is deleted.</div>
        <label class="field" for="f-confirm">Type <code>${esc(name)}</code> to confirm</label>
        <input class="input mono" id="f-confirm" name="confirm" maxlength="40" required autocomplete="off" spellcheck="false">`,
      onSubmit: async (values) => {
        if (values.confirm !== name) throw new Error(`Type ${name} exactly to confirm.`);
        const { job } = await api(`/api/instances/${name}/delete`, values);
        openJob(job, { onDone: (j) => { if (j.status === "success") { $("#dlg-job").close(); notify(`${name} was deleted.`, "ok"); app.go("/"); } } });
      },
    });
  },
};
