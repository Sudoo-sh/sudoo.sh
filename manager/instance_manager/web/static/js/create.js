// The New instance dialog: repository, branch, version, edition, access check, progress and result.
import { $, api, esc, notify, state } from "./core.js";
import { openJob } from "./dialogs.js";
import { runAccessCheck } from "./github.js";

let repoTimer = null;
let nameTimer = null;
const form = { planOk: false, accessOk: false };

async function open() {
  document.querySelectorAll("dialog[open]").forEach((d) => d.close());
  $("#create-form").reset();
  $("#create-form").hidden = false;
  $("#create-job").hidden = true;
  $("#create-title").textContent = "New instance";
  $("#c-access").innerHTML = "";
  Object.assign(form, { planOk: false, accessOk: false });
  setBranches(null);
  setRepoHelp("On GitHub, open the repository, click \"Code\" and copy its address.");
  updateSubmit();
  $("#dlg-create").showModal();
  $("#c-repo").focus();
  state.versions ??= await api("/api/versions");
  const usable = state.versions.filter((v) => v.published);
  $("#c-version").innerHTML = state.versions.map((v) => `<option value="${esc(v.version)}" ${v.published ? "" : "disabled"}
    ${v === usable[usable.length - 1] ? "selected" : ""}>Odoo ${esc(v.version.replace(/\.0$/, ""))}${v.published ? "" : " (not published yet)"}</option>`).join("");
  updateEdition();
  const days = state.overview?.delete_after_days ?? 30;
  $("#c-lifetime").innerHTML = `${days > 0 ? `<option value="${days}" selected>Delete ${days} days after creation</option>` : ""}
    ${days !== 30 ? `<option value="30">Delete 30 days after creation</option>` : ""}
    <option value="0" ${days > 0 ? "" : "selected"}>Keep until deleted by hand</option>`;
  updatePlan();
}

function setRepoHelp(text, kind = "") {
  $("#c-repo-help").className = `help ${kind}`;
  $("#c-repo-help").textContent = text;
}

// `keep`: the branch the user already chose; it stays selected when the repository is checked again
// (for example after adding a deploy key), instead of jumping back to the repository's default branch.
function setBranches(info, keep = null) {
  const select = $("#c-branch");
  select.disabled = !info;
  const chosen = info && info.branches.includes(keep) ? keep : info?.default;
  select.innerHTML = info
    ? info.branches.map((b) => `<option ${b === chosen ? "selected" : ""}>${esc(b)}</option>`).join("")
    : "<option>Enter the repository first</option>";
}

function updateEdition() {
  const version = state.versions.find((v) => v.version === $("#c-version").value);
  const option = $('#c-edition option[value="enterprise"]');
  const label = version.version.replace(/\.0$/, "");
  option.disabled = !version.enterprise;
  option.textContent = version.enterprise ? "Enterprise" : `Enterprise (not available for Odoo ${label})`;
  if (option.disabled) $("#c-edition").value = "community";
}

function matchVersionToBranch() {
  const match = $("#c-branch").value.match(/^(\d+)\.0/);
  const option = match && [...$("#c-version").options].find((o) => o.value === `${match[1]}.0` && !o.disabled);
  if (option) {
    option.selected = true;
    updateEdition();
  }
}

function updateSubmit() {
  const repo = $("#c-repo").value.trim();
  $("#c-submit").disabled = !form.planOk || (Boolean(repo) && !form.accessOk);
}

async function checkRepo() {
  const url = $("#c-repo").value.trim();
  const chosenBranch = $("#c-branch").disabled ? null : $("#c-branch").value;
  form.accessOk = false;
  $("#c-access").innerHTML = "";
  if (!url) { setBranches(null); updatePlan(); return; }
  setRepoHelp("Checking the repository...");
  state.recheck = () => { delete state.branches[url]; checkRepo(); };
  state.branches[url] ??= api(`/api/repo-branches?url=${encodeURIComponent(url)}`);
  let info;
  try {
    info = await state.branches[url];
  } catch (error) {
    delete state.branches[url];
    setRepoHelp(error.message, "error");
    setBranches(null);
    updatePlan();
    return;
  }
  if ($("#c-repo").value.trim() !== url) return;
  if (info.ok) {
    setRepoHelp(`Repository found with ${info.branches.length} branch${info.branches.length === 1 ? "" : "es"}.`, "ok");
    setBranches(info, chosenBranch);
    if (!chosenBranch) matchVersionToBranch();
  } else {
    delete state.branches[url];
    setRepoHelp(info.error, "error");
    setBranches(null);
  }
  updatePlan();
  await checkAccess();
}

async function checkAccess() {
  const url = $("#c-repo").value.trim();
  form.accessOk = false;
  updateSubmit();
  const report = await runAccessCheck($("#c-access"), url, $("#c-branch").disabled ? "" : $("#c-branch").value);
  form.accessOk = report.all_ok && !$("#c-branch").disabled;
  updateSubmit();
}

async function updatePlan() {
  const repo = $("#c-repo").value.trim();
  const name = $("#c-name").value.trim();
  if (!repo && !name) {
    $("#c-plan").innerHTML = `<span class="muted">Details appear here once a repository is entered.</span>`;
    form.planOk = false;
    updateSubmit();
    return;
  }
  const branch = $("#c-branch").disabled ? "" : $("#c-branch").value;
  let plan;
  try {
    plan = await api(`/api/plan?repo=${encodeURIComponent(repo)}&branch=${encodeURIComponent(branch)}&name=${encodeURIComponent(name)}`);
  } catch (error) {
    $("#c-plan").innerHTML = `<div class="callout error">${esc(error.message)}</div>`;
    form.planOk = false;
    updateSubmit();
    return;
  }
  const edition = $("#c-edition").value === "enterprise" ? "Enterprise" : "Community";
  $("#c-plan").innerHTML = `<dl class="dl">
    <dt>Name</dt><dd class="mono">${esc(plan.name)}</dd>
    <dt>Database</dt><dd class="mono">${esc(plan.db_name)}</dd>
    ${branch ? `<dt>Branch</dt><dd class="mono"><b>${esc(branch)}</b> (updates come only from this branch)</dd>` : ""}
    <dt>Odoo</dt><dd>${esc($("#c-version").value.replace(/\.0$/, ""))} ${edition}</dd>
    <dt>Link</dt><dd>Its own https link, shown when the instance is ready</dd>
    <dt>Odoo login</dt><dd class="mono">${esc(plan.login)}</dd>
    <dt>Lifetime</dt><dd>${Number($("#c-lifetime").value) > 0
      ? `Deleted ${esc($("#c-lifetime").value)} days after creation, after a backup` : "Kept until deleted by hand"}</dd></dl>`;
  form.planOk = !(repo && $("#c-branch").disabled);
  updateSubmit();
}

async function submit(event) {
  event.preventDefault();
  $("#c-submit").disabled = true;
  try {
    const result = await api("/api/instances", {
      repo: $("#c-repo").value.trim(),
      branch: $("#c-branch").disabled ? "main" : $("#c-branch").value,
      version: $("#c-version").value,
      edition: $("#c-edition").value,
      name: $("#c-name").value.trim(),
      demo: $("#c-demo").checked,
      install_modules: $("#c-install").checked,
      delete_after_days: Number($("#c-lifetime").value),
    });
    $("#create-form").hidden = true;
    $("#create-job").hidden = false;
    $("#create-title").textContent = `Creating ${result.name}`;
    openJob(result.job, {
      container: "#create-job",
      inDialog: false,
      onDone: (job) => {
        $("#create-title").textContent = job.status === "success" ? `${result.name} is ready` : "The instance could not be created";
        if (job.status === "failed") {
          $("#create-job").insertAdjacentHTML("beforeend", `<div class="form-actions"><button class="btn primary" data-act="new">Try again</button></div>`);
        }
      },
    });
  } catch (error) {
    notify(error.message, "error");
    updateSubmit();
  }
}

$("#c-repo").addEventListener("input", () => { clearTimeout(repoTimer); repoTimer = setTimeout(checkRepo, 600); });
$("#c-branch").addEventListener("change", () => { matchVersionToBranch(); updatePlan(); checkAccess(); });
$("#c-version").addEventListener("change", () => { updateEdition(); updatePlan(); });
$("#c-edition").addEventListener("change", updatePlan);
$("#c-lifetime").addEventListener("change", updatePlan);
$("#c-name").addEventListener("input", () => { clearTimeout(nameTimer); nameTimer = setTimeout(updatePlan, 400); });
$("#create-form").addEventListener("submit", submit);

export const actions = { new: open };
