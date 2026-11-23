import { describe, expect, it } from "vitest";
import { odooSeries, repositoryName, repositoryShortName } from "./naming";

describe("repositoryName", () => {
  it("reads GitHub addresses and owner/repo text as the server does", () => {
    expect(repositoryName("https://github.com/Acme/ERP")).toBe("Acme/ERP");
    expect(repositoryName(" https://github.com/acme/odoo-addons.git ")).toBe("acme/odoo-addons");
    expect(repositoryName("https://github.com/acme/shop/")).toBe("acme/shop");
    expect(repositoryName("git@github.com:OCA/web.git")).toBe("OCA/web");
    expect(repositoryName("acme/erp")).toBe("acme/erp");
  });

  it("refuses what is not a repository", () => {
    for (const text of ["", "acme", "https://gitlab.com/acme/erp", "https://github.com/acme", "acme/../erp", "a/b/c"])
      expect(repositoryName(text)).toBeNull();
  });
});

describe("odooSeries", () => {
  it("reads a version's series as the server does", () => {
    expect(odooSeries("18.0")).toBe("18.0");
    expect(odooSeries("17.0+e")).toBe("17.0");
    expect(odooSeries("saas~17.1")).toBe("17.1");
    expect(odooSeries("master")).toBeNull();
  });
});

describe("repositoryShortName", () => {
  it("is the repository's own name, or nothing", () => {
    expect(repositoryShortName("https://github.com/OCA/web")).toBe("web");
    expect(repositoryShortName("Acme/ERP")).toBe("ERP");
    expect(repositoryShortName("not a repository")).toBe("");
  });
});
