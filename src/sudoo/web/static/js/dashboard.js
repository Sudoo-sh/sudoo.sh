// The main page: server usage, the instance list and recent activity.
import {
  $, RESULT, ago, esc, formatBytes, formatPercent, isAdmin, linkOrPending, meter, sparkline, state, statusBadge,
  timeOf, versionLabel, whoLabel,
} from "./core.js";

export function renderDashboard() {
  const { host, instances, jobs, panel_url: panelUrl, idle_hours: idleHours } = state.overview;
  const memoryPct = host.memory_total ? host.memory_used / host.memory_total * 100 : 0;
  const diskPct = host.disk_total ? host.disk_used / host.disk_total * 100 : 0;
  const count = (...statuses) => instances.filter((i) => statuses.includes(i.status)).length;

  $("#view").innerHTML = `
    <div class="page-head">
      <div class="title">${isAdmin() ? `<button class="btn primary" data-act="new">New</button>` : ""}<h1>Instances</h1></div>
      ${isAdmin() ? `<button class="btn" data-act="github-access">GitHub access</button>` : ""}
    </div>

    <section class="box">
      <div class="stats">
        <div class="stat"><div class="label">Server CPU</div><div class="value">${host.cpu != null ? formatPercent(host.cpu) : "-"}</div>
          <div class="note">${esc(host.cores ?? "-")} cores</div>${sparkline((host.history || []).map((x) => x[1]), 100)}</div>
        <div class="stat"><div class="label">Memory</div><div class="value">${formatBytes(host.memory_used)} <span class="of">of ${formatBytes(host.memory_total)}</span></div>
          ${meter(memoryPct)}<div class="note">${memoryPct > 85 ? "Almost full. Pause instances that are not in use." : `${formatBytes(host.memory_total - host.memory_used)} available`}</div></div>
        <div class="stat"><div class="label">Disk</div><div class="value">${formatBytes(host.disk_used)} <span class="of">of ${formatBytes(host.disk_total)}</span></div>
          ${meter(diskPct)}<div class="note">${diskPct > 85 ? "Almost full. Delete instances that are no longer needed." : `${formatBytes(host.disk_total - host.disk_used)} free`}</div></div>
        <div class="stat"><div class="label">Instances</div><div class="value">${instances.length}</div>
          <div class="note">${count("online", "starting", "busy")} running, ${count("paused")} paused, ${count("switched_off", "stopped")} stopped</div>
          <div class="note">Paused automatically after ${esc(idleHours)} h without visits</div></div>
      </div>
      <div class="box-body small muted">Panel link: ${linkOrPending(panelUrl)}. Links change when the server restarts; this page always shows the current ones.
        <a href="/docs/docs/concepts/TUNNELING.md" target="_blank" rel="noopener">Why?</a></div>
    </section>

    <section class="box">
      <div class="table-wrap">${instances.length ? `<table>
        <thead><tr><th>Name</th><th>Status</th><th class="hide-sm">Odoo</th><th class="hide-sm">Branch</th><th>Link</th><th class="hide-sm">CPU</th><th class="hide-sm">Memory</th><th class="hide-sm">Last visit</th><th class="hide-sm">Lifetime</th><th></th></tr></thead>
        <tbody>${instances.map(instanceRow).join("")}</tbody></table>`
        : `<div class="empty">${isAdmin() ? `There are no instances yet. <button class="btn link" data-act="new">Create the first instance</button>` : "There are no instances yet."}</div>`}</div>
    </section>

    <section class="box">
      <div class="box-head"><h2>Recent activity</h2></div>
      ${activityTable(jobs, true)}
    </section>`;
}

function instanceRow(i) {
  const running = ["online", "starting", "busy"].includes(i.status);
  return `<tr class="clickable" data-go="/i/${esc(i.name)}">
    <td><a href="/i/${esc(i.name)}" data-go="/i/${esc(i.name)}"><b>${esc(i.name)}</b></a>${i.job ? `<div class="small muted"><span class="spinner"></span>${esc(i.job.action)}</div>` : ""}</td>
    <td>${statusBadge(i.status)}</td>
    <td class="hide-sm">${esc(versionLabel(i.version, i.edition))}</td>
    <td class="hide-sm mono">${esc(i.branch)}</td>
    <td>${linkOrPending(i.url)}</td>
    <td class="hide-sm num">${running ? formatPercent(i.cpu) : "-"}</td>
    <td class="hide-sm num">${running && i.memory ? formatBytes(i.memory) : "-"}</td>
    <td class="hide-sm num">${ago(i.last_visit)}</td>
    <td class="hide-sm num">${i.expires_at ? `until ${esc(new Date(i.expires_at * 1000).toLocaleDateString())}` : "kept"}</td>
    <td><button class="btn" type="button" data-act="restart" data-arg="${esc(i.name)}" ${i.status === "busy" ? "disabled" : ""}>Restart</button></td>
  </tr>`;
}

export function activityTable(jobs, showInstance) {
  if (!jobs.length) return `<div class="empty">No activity yet.</div>`;
  return `<div class="table-wrap"><table>
    <thead><tr><th>Time</th>${showInstance ? "<th>Instance</th>" : ""}<th>Action</th><th class="hide-sm">By</th><th>Result</th></tr></thead>
    <tbody>${jobs.map((j) => `<tr class="clickable" data-job="${esc(j.id)}">
      <td class="num">${esc(timeOf(j.started))}</td>
      ${showInstance ? `<td>${esc(j.instance)}</td>` : ""}
      <td>${esc(j.action)}</td>
      <td class="hide-sm">${esc(whoLabel(j.user))}</td>
      <td class="result-${esc(j.status)}">${esc(RESULT[j.status])}</td></tr>`).join("")}</tbody></table></div>`;
}
