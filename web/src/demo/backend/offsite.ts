// The demo's off-site copies (GET /api/v1/offsite, /offsite/copies): the production instances' backups copied to a
// sample R2 bucket, several days of them, one verified, one that failed and was not copied again; a new backup of a
// production instance is copied (uploading, then stored) and "Verify now" goes through verifying to verified, with
// the live event "offsite", as on a real server. Nothing leaves the browser.

import type { Backup, OffsiteCopy, OffsiteState } from "@/api/types";
import { publish } from "./bus";
import { startJob } from "./jobs";
import { backups, instances, record, session, setting } from "./state";
import { DAY, hash, HOUR, MINUTE, OPENED, SECOND } from "./util";

const MIB = 1024 * 1024;
const JOB_INSTANCE = "offsite:copies";
// a well-formed Ed25519 public key that belongs to nobody
const PUBLIC_KEY = "-----BEGIN PUBLIC KEY-----\nMCowBQYDK2VwAyEAq3Jm0n8x5cW2fJ1yTzv9hE4oKbL7sR6uD0pNiGaYw3Q=\n-----END PUBLIC KEY-----\n";
const FINGERPRINT = "SHA256:4e1d8c0b9a7f3e2d6c5b4a39281706f5e4d3c2b1a09f8e7d6c5b4a3928170615";

const copies: OffsiteCopy[] = [];
let seeded = false;

const stampOf = (at: number) => new Date(at).toISOString().replace(/[-:]/g, "").replace("T", "-").slice(0, 15);
const location = () => `${String(setting("offsite_bucket") || "acme-backups")}/${String(setting("offsite_prefix") ?? "sudoo")}`;

function copy(instance: string, kind: Backup["kind"], at: number, size: number, values: Partial<OffsiteCopy> = {}): OffsiteCopy {
  const stamp = stampOf(at);
  const database = instances.get(instance)?.odoo.database ?? instance.replace(/-/g, "_");
  const found: OffsiteCopy = {
    id: `${instance}__${stamp}__${kind}`,
    instance,
    stage: instances.get(instance)?.stage ?? "production",
    kind,
    stamp,
    created: at / 1000,
    status: "stored",
    reason: null,
    size,
    files: [{ name: `${instance}__${database}__${stamp}__${kind}.zip`, db: database, size }],
    location: location(),
    uploaded_at: (at + 3 * MINUTE) / 1000,
    verified_at: null,
    verified_with: null,
    job: null,
    found_in_bucket: false,
    ...values,
  };
  copies.push(found);
  return found;
}

function seed() {
  if (seeded) return;
  seeded = true;
  // what is on the server now
  for (const backup of backups) {
    const instance = instances.get(backup.instance);
    if (instance?.stage === "production" && ["manual", "daily", "deletion"].includes(backup.kind)) {
      copy(backup.instance, backup.kind, backup.created * 1000, backup.size);
    }
  }
  // the earlier nights, kept off-site only (the server keeps one daily backup per instance)
  const midnight = new Date(OPENED);
  midnight.setUTCHours(0, 0, 12, 0);
  const lastNight = midnight.getTime() > OPENED ? midnight.getTime() - DAY : midnight.getTime();
  for (const instance of instances.values()) {
    if (instance.stage !== "production") continue;
    for (let night = 1; night <= 4; night++) {
      const at = lastNight - night * DAY + (hash(instance.name) % 400) * SECOND;
      const size = Math.round((380 + (hash(instance.name + night) % 240)) * MIB);
      copy(instance.name, "daily", at, size, night === 2 ? { status: "verified", verified_at: (at + 2 * HOUR) / 1000, verified_with: FINGERPRINT } : {});
    }
  }
  // a manual backup the storage refused last week, never copied again (its file is gone from the server)
  copy("nile-logistics-main", "manual", OPENED - 6 * DAY - 5 * HOUR, 402 * MIB, {
    status: "failed",
    uploaded_at: null,
    reason: "The storage refused PUT nile-logistics-main/manual: the access key may not do this in this bucket (AccessDenied).",
  });
  // the deleted instance's last backup: kept off-site too
  copy("acme-retail-black-friday", "deletion", OPENED - 9 * DAY - 3 * HOUR, 388 * MIB, { stage: "staging" });
  copies.sort((a, b) => (b.created ?? 0) - (a.created ?? 0));
}

const changed = (found: OffsiteCopy) => publish("offsite", found.id, null);

export function state(): OffsiteState {
  const enabled = Boolean(setting("offsite_backups"));
  const configured = Boolean(setting("offsite_endpoint") && setting("offsite_bucket") && setting("offsite_access_key_id") && setting("offsite_secret_access_key"));
  return {
    enabled,
    configured,
    active: enabled && configured,
    location: configured ? location() : null,
    endpoint: String(setting("offsite_endpoint") ?? "") || null,
    other_stages: Boolean(setting("offsite_other_stages")),
    kinds: String(setting("offsite_kinds") ?? "").split(",").filter(Boolean),
    public_key: PUBLIC_KEY,
    fingerprint: FINGERPRINT,
    last_scan: { at: (OPENED - 7 * HOUR) / 1000, found: 0, missing: 0 },
  };
}

export function all(): OffsiteCopy[] {
  seed();
  return copies;
}

export const find = (id: string) => all().find((c) => c.id === id);

/** A backup the demo just took: copied off-site when the rules say so (production, or every stage when chosen). */
export function backupTaken(backup: Backup) {
  seed();
  const current = state();
  const instance = instances.get(backup.instance);
  if (!current.active || !instance || !current.kinds.includes(backup.kind)) return;
  if (instance.stage !== "production" && !current.other_stages) return;
  const found = copy(backup.instance, backup.kind, backup.created * 1000, backup.size, { status: "uploading", uploaded_at: null });
  copies.sort((a, b) => (b.created ?? 0) - (a.created ?? 0));
  changed(found);
  const job = startJob({
    instance: JOB_INSTANCE, action: `Off-site copy of ${backup.instance}`, kind: "offsite-upload", panel: true, seconds: 2.5,
    steps: ["Reading the backup files", `Copying ${backup.file} to ${location()}`, "Signing and writing the manifest", "Applying the off-site retention rules"],
    onDone: () => {
      found.status = "stored";
      found.uploaded_at = Date.now() / 1000;
      changed(found);
    },
  });
  found.job = job.id;
}

export function verify(found: OffsiteCopy, withKey: boolean) {
  const before = found.status;
  found.status = "verifying";
  changed(found);
  const name = found.files[0]?.name ?? found.id;
  const job = startJob({
    instance: JOB_INSTANCE, action: `Check of the off-site copy ${found.id}`, kind: "offsite-verify", panel: true, seconds: 2,
    steps: ["Checking the signature of the manifest", `Downloading and checking ${name}`],
    notes: [{ text: `The copy is intact: signature (${FINGERPRINT}) and every checksum match${withKey ? " (with the public key given for this check)" : ""}`, level: "info" }],
    onDone: () => {
      if (before === "failed" && found.uploaded_at === null) {
        found.status = "failed";
        found.reason = "The copy has no manifest.json in the bucket: it is incomplete or was removed.";
      } else {
        found.status = "verified";
        found.reason = null;
        found.verified_at = Date.now() / 1000;
        found.verified_with = FINGERPRINT;
      }
      changed(found);
    },
  });
  found.job = job.id;
  record(session.user, "off-site copy check started", { instance: found.instance, detail: found.id });
  return job;
}

export function retry(found: OffsiteCopy) {
  found.status = "uploading";
  found.reason = null;
  changed(found);
  const job = startJob({
    instance: JOB_INSTANCE, action: `Off-site copy of ${found.instance}`, kind: "offsite-upload", panel: true, seconds: 2.5,
    steps: ["Reading the backup files", `Copying ${found.files[0]?.name} to ${location()}`, "Signing and writing the manifest"],
    onDone: () => {
      found.status = "stored";
      found.uploaded_at = Date.now() / 1000;
      changed(found);
    },
  });
  found.job = job.id;
  return job;
}

/** The newest good copy of its instance (verified, or stored before any check): kept. */
export function isProtected(found: OffsiteCopy): boolean {
  const same = all().filter((c) => c.instance === found.instance && (c.status === "stored" || c.status === "verified"));
  const newest = same.find((c) => c.status === "verified") ?? same[0];
  return newest?.id === found.id;
}

export function remove(found: OffsiteCopy) {
  copies.splice(copies.indexOf(found), 1);
  changed(found);
  record(session.user, "off-site copy deleted", { instance: found.instance, detail: found.id });
}
