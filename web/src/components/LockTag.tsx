// The "Locked" tag of a branch or an instance (src/sudoo/projects/access.py), on its own so that lists load it without
// the Access tab.

import { Lock } from "lucide-react";
import type { BranchLock } from "@/api/types";
import { Tag } from "@/components/app/basics";
import { useT } from "@/i18n";
import words from "@/i18n/teams";

/** Who a lock allows, as names people read. */
export function allowedNames(lock: BranchLock): string {
  return lock.allow.map((e) => e.name ?? e.id).join(", ");
}

/** The "Locked" tag of a branch or an instance, with who locked it and why on hover. */
export function LockTag({ lock, branch }: { lock: BranchLock; branch: string }) {
  const t = useT(words).lock;
  const title = `${t.lockedTitle(branch, lock.locked_by ?? "")}${lock.reason ? ` · ${lock.reason}` : ""}. ${t.allows(allowedNames(lock))}`;
  return (
    <span title={title}>
      <Tag tone="caution" icon={<Lock />}>
        {t.locked}
      </Tag>
    </span>
  );
}
