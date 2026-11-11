// Backups (administrators): the Odoo-format backup zips taken before deletions and on request.
import { $, api, esc, formatBytes, notify, timeOf } from "./core.js";
import { askForm, showInfo } from "./dialogs.js";

async function showBackups() {
  const list = await api("/api/backups");
  showInfo("Backups", `<p class="muted">A backup is taken before every deletion, automatic or by hand, and with
      <b>Back up now</b> on an instance's page. Each file is a standard Odoo backup (database and files): restore it
      with <b>Restore Database</b> in any Odoo's database manager.
      <a href="/docs/docs/ADMIN_GUIDE.md#7-backups" target="_blank" rel="noopener">More about backups</a></p>
    <div class="box"><div class="table-wrap">${list.length ? `<table>
      <thead><tr><th>Instance</th><th>Database</th><th>Taken</th><th class="hide-sm">Size</th><th></th></tr></thead>
      <tbody>${list.map((b) => `<tr><td>${esc(b.instance)}</td><td class="mono">${esc(b.db)}</td>
        <td class="num">${esc(timeOf(b.created))}</td><td class="num hide-sm">${formatBytes(b.size)}</td>
        <td><div class="btn-row"><a class="btn" href="/api/backups/${encodeURIComponent(b.file)}" download>Download</a>
          <button class="btn danger" type="button" data-act="backup-delete" data-arg="${esc(b.file)}">Delete</button></div></td></tr>`).join("")}
      </tbody></table>` : `<div class="empty">No backups yet.</div>`}</div></div>`);
}

export const actions = {
  backups: showBackups,
  "backup-delete": (t) => askForm({
    title: "Delete backup", ok: "Delete backup", danger: true,
    html: `<p>The backup <code>${esc(t.dataset.arg)}</code> is deleted from the server. This cannot be undone.</p>`,
    onSubmit: async () => {
      await api(`/api/backups/${encodeURIComponent(t.dataset.arg)}/delete`, {});
      notify("Backup deleted.", "ok");
      await showBackups();
    },
  }),
};
