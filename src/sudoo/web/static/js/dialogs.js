// Dialogs: a generic form dialog, an information dialog and the job progress viewer.
import { $, RESULT, api, app, esc, formatDuration, timeOf, whoLabel } from "./core.js";

// A form in the shared form dialog. onSubmit(values) may return true to keep the dialog open.
export function askForm({ title, html, ok = "OK", danger = false, onSubmit }) {
  $("#form-title").textContent = title;
  $("#form-body").innerHTML = html;
  $("#form-error").hidden = true;
  const button = $("#form-ok");
  button.textContent = ok;
  button.hidden = false;
  button.className = danger ? "btn danger-solid" : "btn primary";
  $("#form-form").onsubmit = async (event) => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.target).entries());
    button.disabled = true;
    try {
      const keepOpen = await onSubmit(values);
      if (!keepOpen) $("#dlg-form").close();
    } catch (error) {
      $("#form-error").textContent = error.message;
      $("#form-error").hidden = false;
    } finally {
      button.disabled = false;
    }
  };
  $("#dlg-form").showModal();
  $("#form-body input")?.focus();
}

// The information dialog (logs, GitHub access, accounts, audit log).
export function showInfo(title, html) {
  $("#info-title").textContent = title;
  $("#info-body").innerHTML = html;
  if (!$("#dlg-info").open) $("#dlg-info").showModal();
}

// ------------------------------------------------------------------ job progress

let jobTimer = null;
["#dlg-job", "#dlg-create"].forEach((id) => $(id).addEventListener("close", () => clearTimeout(jobTimer)));

export function openJob(id, { container = "#job-body", inDialog = true, onDone } = {}) {
  clearTimeout(jobTimer);
  const box = $(container);
  let offset = 0;
  let log = "";
  box.innerHTML = `<p class="muted">Loading...</p>`;
  if (inDialog && !$("#dlg-job").open) $("#dlg-job").showModal();

  const tick = async () => {
    let job;
    try {
      job = await api(`/api/jobs/${id}?offset=${offset}`);
    } catch (error) {
      box.innerHTML = `<div class="callout error">${esc(error.message)}</div>`;
      return;
    }
    offset = job.offset;
    log += job.log;
    if (inDialog) $("#job-title").textContent = `${job.action}: ${job.instance}`;
    renderJob(box, job, log);
    if (["running", "queued"].includes(job.status)) {
      jobTimer = setTimeout(tick, 1000);
    } else {
      onDone?.(job);
      app.refresh();
    }
  };
  tick();
}

function renderJob(box, job, log) {
  const logOpen = box.querySelector("details.log-details")?.open;
  const previous = box.querySelector("pre.log");
  const stick = !previous || previous.scrollTop + previous.clientHeight >= previous.scrollHeight - 30;
  const created = job.status === "success" && job.kind === "create" && job.result?.url;
  const finished = !["running", "queued"].includes(job.status);
  const steps = job.steps.map((s) => `<li class="${esc(s.status)}"><span>${esc(s.title)}</span>
    <span class="s">${s.status === "running" ? `<span class="spinner"></span>In progress` : esc(RESULT[s.status === "done" ? "success" : s.status])}</span></li>`).join("");
  box.innerHTML = `
    <p class="small muted">Started ${esc(timeOf(job.started))} by ${esc(whoLabel(job.user))}${job.finished ? `, took ${formatDuration(job.finished - job.started)}` : ""}.</p>
    ${job.status === "queued" ? `<div class="callout info">Waiting for another action on this instance to finish.</div>` : ""}
    ${created ? createdSummary(job.result) : `<ul class="steps">${steps}</ul>`}
    ${job.notes.map((n) => `<div class="callout ${n.level === "warning" ? "warning" : "info"}">${esc(n.text)}</div>`).join("")}
    ${job.status === "failed" ? `<div class="callout error"><b>The action did not complete.</b>${esc(job.hint)}</div>` : ""}
    ${job.status === "success" && !created ? `<div class="callout success">Completed successfully.</div>` : ""}
    ${finished ? "" : `<p class="small muted">You can close this window. The action continues in the background and is listed under Activity.</p>`}
    <details class="more log-details" ${logOpen ? "open" : ""}><summary>Technical log</summary><pre class="log">${esc(log) || "No output yet."}</pre></details>`;
  const pre = box.querySelector("pre.log");
  pre.scrollTop = stick ? pre.scrollHeight : previous.scrollTop;
}

function createdSummary(result) {
  return `<div class="callout success"><b>The instance is ready.</b>Open it with its link and sign in with these details.</div>
    <dl class="dl">
      <dt>Link</dt><dd class="inline-copy"><a class="mono" href="${esc(result.url)}" target="_blank" rel="noopener noreferrer">${esc(result.url)}</a>
        <button class="btn" type="button" data-copy="${esc(result.url)}">Copy</button></dd>
      <dt>User</dt><dd class="mono">${esc(result.login)}</dd>
      <dt>Password</dt><dd class="inline-copy"><span class="mono">${esc(result.password)}</span>
        <button class="btn" type="button" data-copy="${esc(result.password)}" data-label="Password copied.">Copy</button></dd>
      <dt>Database</dt><dd class="mono">${esc(result.db)}</dd>
    </dl>
    <div class="form-actions"><button class="btn" type="button" data-go="/i/${esc(result.name)}">Manage instance</button>
      <a class="btn primary" href="${esc(result.url)}" target="_blank" rel="noopener noreferrer">Open Odoo</a></div>`;
}
