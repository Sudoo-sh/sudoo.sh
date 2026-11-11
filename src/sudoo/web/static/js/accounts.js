// Accounts (administrators), the audit log, and changing one's own password.
import { $, api, esc, notify, state, timeOf, whoLabel } from "./core.js";
import { askForm, showInfo } from "./dialogs.js";

async function showAccounts() {
  const users = await api("/api/users");
  showInfo("Accounts", `<div class="box"><div class="table-wrap"><table>
    <thead><tr><th>Account</th><th>Role</th><th class="hide-sm">Last sign-in</th><th class="hide-sm">Password changed</th><th></th></tr></thead>
    <tbody>${users.map((u) => `<tr><td class="mono">${esc(u.name)}${u.name === state.me.user ? ` <span class="small muted">(you)</span>` : ""}</td>
      <td>${esc(u.role_label)}</td>
      <td class="num hide-sm">${u.last_login ? esc(timeOf(u.last_login)) : "never"}</td>
      <td class="num hide-sm">${esc(timeOf(u.password_changed))}</td>
      <td><div class="btn-row">${u.name === state.me.user
        ? `<button class="btn" type="button" data-act="change-password">Change password</button>`
        : `<button class="btn" type="button" data-act="reset-password" data-arg="${esc(u.name)}">Reset password</button>
           <button class="btn danger" type="button" data-act="delete-user" data-arg="${esc(u.name)}">Delete</button>`}</div></td></tr>`).join("")}
    </tbody></table></div></div>
    <form class="box" id="user-form" autocomplete="off">
      <div class="box-head"><h2>Create an account</h2></div>
      <div class="box-body">
        <div class="grid2">
          <div><label class="field" for="u-name">User name</label>
            <input class="input mono" id="u-name" maxlength="32" required placeholder="e.g. ahmad.dev" spellcheck="false" autocapitalize="none"></div>
          <div><label class="field" for="u-role">Role</label>
            <select class="input" id="u-role"><option value="operator">Sync operator</option><option value="admin">Administrator</option></select></div>
        </div>
        <div class="help">A secure password is generated and shown once. The person can change it after signing in.</div>
        <div id="u-result"></div>
        <div class="form-actions"><button class="btn primary" type="submit">Create account</button></div>
      </div>
    </form>
    <p class="small muted"><b>Administrator</b>: everything, including GitHub access, creating and deleting instances and managing accounts.
      <b>Sync operator</b>: get the latest code, update and add submodules, restart and start instances, view logs.
      Deleting an account or resetting its password signs it out everywhere.
      <a href="/docs/docs/USER_MANUAL.md#8-accounts-and-passwords" target="_blank" rel="noopener">More in the user manual</a></p>`);
  $("#user-form").onsubmit = async (event) => {
    event.preventDefault();
    try {
      const created = await api("/api/users", { name: $("#u-name").value.trim(), role: $("#u-role").value });
      await showAccounts();
      $("#u-result").innerHTML = `<div class="callout success"><b>Account ${esc(created.name)} created.</b>Give this password to the person; it is not shown again.</div>
        <div class="secret-box"><span class="mono">${esc(created.password)}</span><button class="btn" type="button" data-copy="${esc(created.password)}" data-label="Password copied.">Copy</button></div>`;
    } catch (error) {
      notify(error.message, "error");
    }
  };
}

async function showAudit() {
  const entries = await api("/api/audit");
  showInfo("Audit log", `<p class="muted">Sign-ins and actions, newest first (last 200 entries).</p>
    <div class="box"><div class="table-wrap"><table>
    <thead><tr><th>Time</th><th>Account</th><th>Event</th><th>Instance</th><th class="hide-sm">Address</th><th>Result</th></tr></thead>
    <tbody>${entries.map((e) => `<tr><td class="num">${esc(timeOf(e.ts))}</td><td>${esc(whoLabel(e.user))}</td>
      <td>${esc(e.event)}${e.detail ? `<div class="small muted">${esc(e.detail)}</div>` : ""}</td><td>${esc(e.instance || "")}</td>
      <td class="hide-sm mono">${esc(e.ip || "")}</td><td class="${e.ok ? "result-success" : "result-failed"}">${e.ok ? "OK" : "Refused"}</td></tr>`).join("")
      || `<tr><td colspan="6" class="empty">No entries yet.</td></tr>`}</tbody></table></div></div>`);
}

function changePassword() {
  $("#dlg-info").close();
  askForm({
    title: "Change password", ok: "Change password",
    html: `<label class="field" for="pw-current">Current password</label>
      <input class="input" id="pw-current" name="current" type="password" autocomplete="current-password" required>
      <label class="field" for="pw-new">New password</label>
      <input class="input" id="pw-new" name="new" type="password" autocomplete="new-password" minlength="12" maxlength="128" required>
      <div class="help">12 to 128 characters, at least 6 different ones, not containing your user name or a common word such as
        "password" or "admin". A short sentence is easy to remember and hard to guess. Other sessions of this account are signed out.</div>
      <label class="field" for="pw-repeat">Repeat new password</label>
      <input class="input" id="pw-repeat" name="repeat" type="password" autocomplete="new-password" required>`,
    onSubmit: async (values) => {
      if (values.new !== values.repeat) throw new Error("The new passwords do not match.");
      await api("/api/account/password", { current: values.current, new: values.new });
      notify("Your password was changed.", "ok");
    },
  });
}

export const actions = {
  accounts: showAccounts,
  audit: showAudit,
  "change-password": changePassword,
  logout: async () => { await api("/api/logout", {}); window.location.replace("/login"); },
  "reset-password": (t) => askForm({
    title: `Reset password for ${t.dataset.arg}`, ok: "Reset password", danger: true,
    html: `<p>A new random password is generated and shown once. The account is signed out everywhere.</p>`,
    onSubmit: async () => {
      const { password } = await api(`/api/users/${encodeURIComponent(t.dataset.arg)}/reset-password`, {});
      $("#form-body").innerHTML = `<p>New password for <b>${esc(t.dataset.arg)}</b>. Copy it now; it is not shown again.</p>
        <div class="secret-box"><span class="mono">${esc(password)}</span><button class="btn" type="button" data-copy="${esc(password)}">Copy</button></div>`;
      $("#form-ok").hidden = true;
      return true;
    },
  }),
  "delete-user": (t) => askForm({
    title: `Delete account ${t.dataset.arg}`, ok: "Delete account", danger: true,
    html: `<p>The account <code>${esc(t.dataset.arg)}</code> is removed and signed out everywhere. This cannot be undone.</p>`,
    onSubmit: async () => { await api(`/api/users/${encodeURIComponent(t.dataset.arg)}/delete`, {}); notify("Account deleted.", "ok"); await showAccounts(); },
  }),
};
