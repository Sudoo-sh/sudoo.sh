import { afterEach, beforeAll, describe, expect, it } from "vitest";
import type { Alert } from "@/api/types";

/** The standing alert while the demo's ODOO_ADMIN_PASSWORD is the well-known "admin": listed for admins only, never
 *  carrying the password, gone once the setting is changed, back when it is reset. */

// the language module reads the document's cookie when it loads; there is no page in a test
let answer: typeof import("./api").answer;
let session: typeof import("./state").session;
let setAdminPasswordDefault: typeof import("./state").setAdminPasswordDefault;

beforeAll(async () => {
  (globalThis as { document?: unknown }).document ??= { cookie: "", documentElement: { lang: "", dir: "" } };
  ({ answer } = await import("./api"));
  ({ session, setAdminPasswordDefault } = await import("./state"));
});

const getAlerts = async () => {
  const response = await answer("GET", "/alerts", new URLSearchParams(), undefined);
  if (response.status !== 200) throw new Error(`GET /alerts answered ${response.status}`);
  return (response.body as { data: Alert[] }).data;
};

const listed = (alerts: Alert[]) => alerts.find((a) => a.type === "default_admin_password");

afterEach(async () => {
  session.role = "super_admin";
  await answer("DELETE", "/settings/odoo_admin_password", new URLSearchParams(), undefined); // also restores the flag
  session.role = "admin";
  setAdminPasswordDefault(true);
});

describe("demo default admin password alert", () => {
  it("is listed for an admin, without a link (an admin cannot change the panel's settings)", async () => {
    session.role = "admin";
    const alert = listed(await getAlerts());
    expect(alert).toBeDefined();
    expect(alert?.severity).toBe("problem");
    expect(alert?.link).toBeNull();
    expect(alert?.resolved_at).toBeNull();
  });

  it("links a super admin to the setting", async () => {
    session.role = "super_admin";
    const alert = listed(await getAlerts());
    expect(alert?.link).toBe("/settings?tab=instances&focus=odoo_admin_password");
  });

  it("is not listed for other roles: they may not read the alerts at all", async () => {
    session.role = "developer";
    const response = await answer("GET", "/alerts", new URLSearchParams(), undefined);
    expect(response.status).toBe(403);
  });

  it("is gone once the password is changed, which never sends the password itself", async () => {
    session.role = "super_admin";
    const changed = await answer("PATCH", "/settings", new URLSearchParams(), {
      odoo_admin_password: "sudoo-demo-marker",
    });
    expect(changed.status).toBe(200);
    expect(JSON.stringify(changed.body)).not.toContain("sudoo-demo-marker");
    expect(listed(await getAlerts())).toBeUndefined();
  });

  it("is back when the setting is reset to .env", async () => {
    session.role = "super_admin";
    await answer("PATCH", "/settings", new URLSearchParams(), { odoo_admin_password: "sudoo-demo-marker" });
    expect(listed(await getAlerts())).toBeUndefined();
    await answer("DELETE", "/settings/odoo_admin_password", new URLSearchParams(), undefined);
    expect(listed(await getAlerts())).toBeDefined();
  });
});
