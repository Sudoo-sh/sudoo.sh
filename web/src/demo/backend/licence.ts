// The demo's Sudoo.sh Licence Agreement: the text the real backend serves (licence.json, made from LICENSE.md with
// `uv run python -m sudoo.licence`; a test checks they agree) and acceptances kept in memory only. The installer's
// acceptance is there from the start; the panel's is not, so a super admin sees the acceptance step once (as after
// updating an existing installation to 3.0), and first-run setup can be tried at /setup.

import type { LicenceRecord, LicenceStatus, LicenceText, Session } from "@/api/types";
import { DAY, iso, OPENED, Problem } from "./util";
import text from "./licence.json";

export const LICENCE = text as LicenceText;
const REQUIRED = "1.0";

const records: LicenceRecord[] = [
  {
    id: "installer-demo",
    version: LICENCE.version,
    sha256: LICENCE.sha256,
    accepted_at: iso(OPENED - 3 * DAY)!,
    user: "root",
    host: "erp-demo",
    via: "prompt",
    installer_version: "3.0.0",
    surface: "installer",
    synced_at: null,
  },
];

const panelRecords = () => records.filter((r) => r.surface !== "installer");
export const licencePending = () => !panelRecords().some((r) => r.version >= REQUIRED);

export function licenceStatus(full: boolean): LicenceStatus {
  const accepted = panelRecords().at(-1) ?? null;
  return {
    bundled_version: LICENCE.version,
    required_version: REQUIRED,
    pending: licencePending(),
    update_needed: false,
    accepted: accepted && {
      version: accepted.version,
      sha256: accepted.sha256,
      accepted_at: accepted.accepted_at,
      user: accepted.user,
      user_name: accepted.user_name,
      use: accepted.use,
      on_behalf: accepted.on_behalf,
      surface: accepted.surface,
    },
    ...(full ? { history: records.map((r) => ({ ...r })) } : {}),
  };
}

/** Checks what the web app sends, as the backend does (licence.check_acceptance), and records it. */
export function acceptLicence(
  body: unknown,
  who: Pick<Session, "user" | "display_name" | "email">,
  surface: "panel_setup" | "panel",
) {
  const data = (body ?? {}) as Record<string, unknown>;
  if (data.accepted !== true) throw new Problem(400, "Tick the box to accept the Sudoo.sh Licence Agreement.");
  if (data.version !== LICENCE.version || data.sha256 !== LICENCE.sha256)
    throw new Problem(400, "The licence text changed while it was open. Reload the page and read it again.");
  const use = data.use;
  if (use !== "personal" && use !== "company" && use !== "service_provider")
    throw new Problem(400, "Choose how you will use Sudoo.sh: personal, my company or service provider.");
  const company = use !== "personal";
  if (company && data.on_behalf !== true)
    throw new Problem(400, "Confirm that you accept on behalf of your company and that you are authorised to bind it.");
  records.push({
    id: `demo-${records.length}`,
    version: LICENCE.version,
    sha256: LICENCE.sha256,
    accepted_at: iso(Date.now())!,
    user: who.user,
    user_name: who.display_name,
    user_email: who.email ?? "",
    use,
    on_behalf: company,
    surface,
    ip: "203.0.113.24",
    user_agent: navigator.userAgent.slice(0, 300),
    synced_at: null,
  });
}
