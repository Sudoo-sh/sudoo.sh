import { describe, expect, it } from "vitest";
import { change, preview, sendCode } from "./access-rules";

const RULES = {
  enabled: true,
  mode: "countries",
  scope: "back_office",
  countries: ["JO"],
  networks: [],
  exceptions: { presets: ["payments"], paths: [], networks: [] },
};

describe("demo access rules", () => {
  it("previews what is closed to others and whether you still get in", () => {
    const found = preview(RULES);
    expect(found.you?.allowed).toBe(false); // the demo's own address is in PS
    expect(found.warnings).toContain("you_are_outside");
    const open = Object.fromEntries(found.samples.map((s) => [s.path, s.open_to_everyone]));
    expect(open["/odoo"]).toBe(false);
    expect(open["/shop"]).toBe(true);
    expect(open["/payment/stripe/webhook"]).toBe(true);
    expect(open["/web/image/product.template/1/image_512"]).toBe(true);
    expect(preview({ ...RULES, countries: ["PS"] }, "198.51.100.4").test?.allowed).toBe(false);
  });

  it("refuses a range of every address and a change without the name or the code", () => {
    expect(() => preview({ ...RULES, mode: "ips", networks: [{ value: "0.0.0.0/0", label: "" }] })).toThrow(
      /every address/,
    );
    sendCode("acme-retail-main", "email");
    expect(() =>
      change("acme-retail-main", { rules: RULES, confirm_name: "acme", code: "246810", method: "email" }, "sara"),
    ).toThrow(/Type/);
    expect(() =>
      change(
        "acme-retail-main",
        { rules: RULES, confirm_name: "acme-retail-main", code: "111111", method: "email" },
        "sara",
      ),
    ).toThrow(/code/);
    const saved = change(
      "acme-retail-main",
      { rules: RULES, confirm_name: "acme-retail-main", code: "246810", method: "email" },
      "sara",
    );
    expect(saved.countries).toEqual(["JO"]);
  });
});
