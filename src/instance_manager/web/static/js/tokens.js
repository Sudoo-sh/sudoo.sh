// API tokens (administrators): let other systems use the panel's API (/api/v1). Each token is shown once.
import { $, api, esc, notify, timeOf } from "./core.js";
import { askForm, showInfo } from "./dialogs.js";

async function showTokens() {
  const tokens = await api("/api/tokens");
  showInfo("API tokens", `<p class="muted">Other systems use the panel's API with a token and a signing secret: the token
      says who is calling, the signature proves each request is genuine and new.
      <a href="/docs/docs/PANEL_API.md" target="_blank" rel="noopener">How to call the API</a></p>
    <div class="box"><div class="table-wrap">${tokens.length ? `<table>
      <thead><tr><th>Name</th><th>Scope</th><th class="hide-sm">Created</th><th class="hide-sm">Last used</th><th></th></tr></thead>
      <tbody>${tokens.map((t) => `<tr><td>${esc(t.name)}</td><td>${t.scope === "write" ? "Read and write" : "Read only"}</td>
        <td class="num hide-sm">${esc(timeOf(t.created))}<div class="small muted">${esc(t.created_by)}</div></td>
        <td class="num hide-sm">${t.last_used ? esc(timeOf(t.last_used)) : "never"}</td>
        <td><button class="btn danger" type="button" data-act="token-revoke" data-arg="${esc(t.id)}" data-name="${esc(t.name)}">Revoke</button></td></tr>`).join("")}
      </tbody></table>` : `<div class="empty">No API tokens yet.</div>`}</div></div>
    <form class="box" id="token-form" autocomplete="off">
      <div class="box-head"><h2>Create a token</h2></div>
      <div class="box-body">
        <div class="grid2">
          <div><label class="field" for="t-name">Name (what uses it)</label>
            <input class="input" id="t-name" maxlength="40" required placeholder="e.g. CI pipeline"></div>
          <div><label class="field" for="t-scope">Scope</label>
            <select class="input" id="t-scope"><option value="read">Read only</option><option value="write">Read and write</option></select></div>
        </div>
        <div id="t-result"></div>
        <div class="form-actions"><button class="btn primary" type="submit">Create token</button></div>
      </div>
    </form>`);
  $("#token-form").onsubmit = async (event) => {
    event.preventDefault();
    try {
      const created = await api("/api/tokens", { name: $("#t-name").value.trim(), scope: $("#t-scope").value });
      await showTokens();
      $("#t-result").innerHTML = `<div class="callout success"><b>Token ${esc(created.name)} created.</b>Copy both values now and store
          them where the other system keeps secrets; they are not shown again.</div>
        <label class="field">Token</label>
        <div class="secret-box"><span class="mono">${esc(created.token)}</span><button class="btn" type="button" data-copy="${esc(created.token)}" data-label="Token copied.">Copy</button></div>
        <label class="field">Signing secret</label>
        <div class="secret-box"><span class="mono">${esc(created.signing_secret)}</span><button class="btn" type="button" data-copy="${esc(created.signing_secret)}" data-label="Signing secret copied.">Copy</button></div>`;
    } catch (error) {
      notify(error.message, "error");
    }
  };
}

export const actions = {
  "api-tokens": showTokens,
  "token-revoke": (t) => askForm({
    title: `Revoke ${t.dataset.name}`, ok: "Revoke token", danger: true,
    html: `<p>The system using <b>${esc(t.dataset.name)}</b> can no longer call the API, from now on. This cannot be undone.</p>`,
    onSubmit: async () => {
      await api(`/api/tokens/${encodeURIComponent(t.dataset.arg)}/revoke`, {});
      notify("Token revoked.", "ok");
      await showTokens();
    },
  }),
};
