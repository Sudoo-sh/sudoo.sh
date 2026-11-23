import { describe, expect, it } from "vitest";
import type { SetupDraft } from "@/api/types";
import { back, blocker, canOpen, codeFromHash, next, OPTIONAL, resumeAt, STEPS } from "./setup-steps";

const DRAFT: SetupDraft = {
  step: "welcome",
  language: "en",
  engine: "docker",
  exposure: "direct",
  name: { kind: "server", domain: "" },
  mail: { enabled: false, host: "", port: 587, security: "starttls", username: "", sender: "", password_set: false },
  github: { connect: false },
  account: { link: false, link_code: "", company_label: "" },
  admin: { first_name: "", last_name: "", email: "", phone: "" },
};

describe("the setup steps", () => {
  it("are the eight steps of the wizard, in order, with three optional", () => {
    expect(STEPS).toEqual(["welcome", "server", "address", "email", "github", "account", "admin", "done"]);
    expect([...OPTIONAL]).toEqual(["email", "github", "account"]);
    expect(next("welcome")).toBe("server");
    expect(next("done")).toBe("done");
    expect(back("server")).toBe("welcome");
    expect(back("welcome")).toBe("welcome");
  });

  it("resume at the saved step, never past the licence when it is not accepted here, never at done", () => {
    expect(resumeAt("email", true)).toBe("email");
    expect(resumeAt("email", false)).toBe("welcome");
    expect(resumeAt("done", true)).toBe("admin");
    expect(resumeAt(undefined, true)).toBe("welcome");
  });

  it("open only steps already reached, never done", () => {
    expect(canOpen("server", "address", "email")).toBe(true);
    expect(canOpen("github", "address", "email")).toBe(false);
    expect(canOpen("done", "admin", "done")).toBe(false);
    expect(canOpen("welcome", "done", "done")).toBe(false);
  });

  it("stop Next while a step is incomplete", () => {
    expect(blocker("address", DRAFT)).toBeNull();
    expect(blocker("address", { ...DRAFT, exposure: "proxy" })).toBe("nameNeeded");
    expect(blocker("address", { ...DRAFT, name: { kind: "domain", domain: "panel" } })).toBe("nameNeeded");
    expect(blocker("address", { ...DRAFT, name: { kind: "domain", domain: "panel.example.com" } })).toBeNull();
    expect(blocker("email", { ...DRAFT, mail: { ...DRAFT.mail, enabled: true, host: "smtp.example.com" } })).toBe("mailIncomplete");
    expect(blocker("email", { ...DRAFT, mail: { ...DRAFT.mail, enabled: true, host: "smtp.example.com", username: "a@example.com" } })).toBeNull();
    expect(blocker("account", { ...DRAFT, account: { ...DRAFT.account, link: true, link_code: "<x>" } })).toBe("linkCodeNeeded");
    expect(blocker("account", { ...DRAFT, account: { ...DRAFT.account, link: true, link_code: "link-7q2x" } })).toBeNull();
  });

  it("read the setup code from the link's #code=", () => {
    expect(codeFromHash("#code=abcd-efgh-jklm")).toBe("ABCD-EFGH-JKLM");
    expect(codeFromHash("#x=1&code=ABCD-EFGH-JKLM")).toBe("ABCD-EFGH-JKLM");
    expect(codeFromHash("#code=<script>")).toBe("");
    expect(codeFromHash("")).toBe("");
  });
});
