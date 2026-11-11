// GitHub access: deploy keys and the repository access check (New instance, Add submodule).
import { $, api, contactSentence, esc, notify, state, timeOf } from "./core.js";
import { askForm, showInfo } from "./dialogs.js";

export async function runAccessCheck(box, url, branch) {
  box.innerHTML = `<p class="muted"><span class="spinner"></span>Checking access to the repository and its submodules...</p>`;
  const report = await api(`/api/repo-access?url=${encodeURIComponent(url)}&branch=${encodeURIComponent(branch || "")}`);
  box.innerHTML = accessTable(report);
  return report;
}

function accessTable(report) {
  const rows = report.repos.map((r) => {
    let status = r.ok ? `<span class="result-success">Access OK</span>` : `<span class="result-failed">No access</span>`;
    if (r.ok && r.has_key) status += `<div class="small muted">with deploy key</div>`;
    if (!r.ok && r.has_key) status += `<div class="small muted">Key created, not added on GitHub yet</div>`;
    let action = "";
    if (!r.ok && r.repo && report.can_manage_keys) {
      action = r.has_key
        ? `<button class="btn" type="button" data-act="deploy-key-show" data-arg="${esc(r.repo)}">Show key</button>`
        : `<button class="btn primary" type="button" data-act="deploy-key-create" data-arg="${esc(r.url)}">Create deploy key</button>`;
    }
    return `<tr><td><div class="mono">${esc(r.repo || r.url)}</div><div class="small muted">${r.role === "main" ? "Main repository" : "Submodule"}</div></td>
      <td>${status}</td><td>${action}</td></tr>`;
  }).join("");
  const missing = report.repos.filter((r) => !r.ok).length;
  const what = missing === 1 ? "one repository" : `${missing} repositories`;
  const note = missing === 0 ? `<div class="callout success">The server can read the repository and all its submodules.</div>`
    : report.can_manage_keys ? `<div class="callout warning">The server cannot read ${what}. Create a deploy key for each, add it on GitHub, then check again.</div>`
      : `<div class="callout warning">The server cannot read ${what}. ${esc(contactSentence())}</div>`;
  return `<div class="box"><div class="box-head"><h2>Repository access</h2>
      <button class="btn" type="button" data-act="access-recheck">Check again</button></div>
    <div class="table-wrap"><table><tbody>${rows}</tbody></table></div><div class="box-body">${note}</div></div>`;
}

function showDeployKey(key) {
  showInfo(`Deploy key for ${key.repo}`, `<p>Add this key to the repository on GitHub. It gives the server read-only access to this repository only.
    <a href="/docs/docs/concepts/DEPLOY_KEYS.md" target="_blank" rel="noopener">How deploy keys work</a></p>
    <ol>
      <li>Open <a href="${esc(key.github_url)}" target="_blank" rel="noopener noreferrer">the deploy key page of ${esc(key.repo)}</a> (needs admin rights on the repository).</li>
      <li>Title: <code>Sudoo</code></li>
      <li>Key: paste the key below.</li>
      <li>Leave "Allow write access" unticked. Tick it only if submodules should be added to this repository from the panel.</li>
      <li>Click "Add key", then come back and click "Check again".</li>
    </ol>
    <div class="secret-box"><span class="mono">${esc(key.public_key)}</span><button class="btn" type="button" data-copy="${esc(key.public_key)}" data-label="Key copied.">Copy key</button></div>
    <div class="form-actions"><button class="btn primary" type="button" data-act="access-recheck">I added it, check again</button></div>`);
}

async function showGithubAccess() {
  const keys = await api("/api/deploy-keys");
  state.recheck = showGithubAccess;
  showInfo("GitHub access", `
    <p>Public repositories need no setup. Each private repository, including private submodules, needs a read-only
      <b>deploy key</b>. One key per repository is shared by every instance that uses it. When you create an instance
      or add a submodule, the panel checks access and offers to create missing keys.
      <a href="/docs/docs/concepts/DEPLOY_KEYS.md" target="_blank" rel="noopener">What is a deploy key?</a></p>
    <div class="box"><div class="box-head"><h2>Deploy keys</h2><span class="muted">${keys.length} ${keys.length === 1 ? "repository" : "repositories"}</span></div>
      ${keys.length ? `<div class="table-wrap"><table><tbody>${keys.map((k) => `<tr>
        <td class="mono">${esc(k.spelling)}</td><td class="num small muted">created ${esc(timeOf(k.created))}</td>
        <td><div class="btn-row"><button class="btn" type="button" data-act="deploy-key-show" data-arg="${esc(k.repo)}">Show key</button>
          <button class="btn danger" type="button" data-act="deploy-key-delete" data-arg="${esc(k.repo)}">Delete</button></div></td></tr>`).join("")}
        </tbody></table></div>` : `<div class="box-body muted">No deploy keys yet.</div>`}
      <form class="box-body" id="dk-form" autocomplete="off">
        <label class="field" for="dk-repo">Add a repository</label>
        <div class="btn-row"><input class="input mono" id="dk-repo" maxlength="200" placeholder="company/repository or its GitHub address" spellcheck="false">
          <button class="btn primary" type="submit">Create deploy key</button></div>
      </form>
    </div>`);
  $("#dk-form").onsubmit = async (event) => {
    event.preventDefault();
    try {
      showDeployKey(await api("/api/deploy-keys", { repo: $("#dk-repo").value.trim() }));
    } catch (error) {
      notify(error.message, "error");
    }
  };
}

export const actions = {
  "github-access": showGithubAccess,
  "deploy-key-create": async (t) => showDeployKey(await api("/api/deploy-keys", { repo: t.dataset.arg })),
  "deploy-key-show": async (t) => showDeployKey((await api("/api/deploy-keys")).find((k) => k.repo === t.dataset.arg)),
  "deploy-key-delete": (t) => askForm({
    title: `Delete deploy key for ${t.dataset.arg}`, ok: "Delete key", danger: true,
    html: `<p>The server loses access to <code>${esc(t.dataset.arg)}</code>. Also remove the key on GitHub (repository Settings, Deploy keys).</p>`,
    onSubmit: async () => { await api("/api/deploy-keys/delete", { repo: t.dataset.arg }); await showGithubAccess(); },
  }),
  "access-recheck": () => {
    if ($("#dlg-info").open && $("#info-title").textContent.startsWith("Deploy key")) $("#dlg-info").close();
    state.recheck?.();
  },
};
