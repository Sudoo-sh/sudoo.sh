import { describe, expect, it } from "vitest";
import { githubCommitUrl, shortSha } from "./git";

const SHA = "f25e4f6a9b0c1d2e3f405162738495a6b7c8d9e0";

describe("githubCommitUrl", () => {
  it("builds the commit's page from the repository's web address", () => {
    expect(githubCommitUrl("https://github.com/acme/odoo-addons", SHA)).toBe(`https://github.com/acme/odoo-addons/commit/${SHA}`);
  });

  it("reads the addresses git uses (.git, ssh, a trailing slash)", () => {
    const want = `https://github.com/acme/odoo-addons/commit/${SHA}`;
    expect(githubCommitUrl("https://github.com/acme/odoo-addons.git", SHA)).toBe(want);
    expect(githubCommitUrl("git@github.com:acme/odoo-addons.git", SHA)).toBe(want);
    expect(githubCommitUrl("ssh://git@github.com/acme/odoo-addons", SHA)).toBe(want);
    expect(githubCommitUrl("https://github.com/acme/odoo-addons/", SHA)).toBe(want);
  });

  it("accepts a short hash and writes it in lower case", () => {
    expect(githubCommitUrl("https://github.com/acme/erp", "F25E4F6")).toBe("https://github.com/acme/erp/commit/f25e4f6");
  });

  it("never keeps a token that was part of the address", () => {
    expect(githubCommitUrl("https://x-access-token:secret@github.com/acme/erp.git", SHA)).toBe(`https://github.com/acme/erp/commit/${SHA}`);
  });

  it("gives no link for a repository that is not on GitHub", () => {
    expect(githubCommitUrl("https://gitlab.com/acme/erp", SHA)).toBeNull();
    expect(githubCommitUrl("git@bitbucket.org:acme/erp.git", SHA)).toBeNull();
    expect(githubCommitUrl("https://git.example.com/acme/erp", SHA)).toBeNull();
    expect(githubCommitUrl("https://github.com.evil.test/acme/erp", SHA)).toBeNull();
    expect(githubCommitUrl("/srv/git/erp", SHA)).toBeNull();
    expect(githubCommitUrl(null, SHA)).toBeNull();
  });

  it("gives no link without a commit hash, or with something else in its place", () => {
    expect(githubCommitUrl("https://github.com/acme/erp", null)).toBeNull();
    expect(githubCommitUrl("https://github.com/acme/erp", "")).toBeNull();
    expect(githubCommitUrl("https://github.com/acme/erp", "main")).toBeNull();
    expect(githubCommitUrl("https://github.com/acme/erp", "../../evil")).toBeNull();
  });

  it("gives no link for an address that is not a repository", () => {
    expect(githubCommitUrl("https://github.com/acme", SHA)).toBeNull();
    expect(githubCommitUrl("https://github.com/acme/erp/tree/main", SHA)).toBeNull();
  });
});

describe("shortSha", () => {
  it("keeps the first seven characters", () => {
    expect(shortSha(SHA)).toBe("f25e4f6");
    expect(shortSha("abc")).toBe("abc");
  });
});
