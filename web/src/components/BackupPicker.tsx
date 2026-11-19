import { Archive, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { useBackups } from "@/api/queries";
import type { Backup, BackupKind } from "@/api/types";
import { Empty, Skeleton, Tag } from "@/components/app/basics";
import { Input } from "@/components/ui/input";
import { useT, words } from "@/i18n";
import instanceWords from "@/i18n/instance";
import { ago, bytes, dateTime } from "@/lib/format";

/** The kind of a backup, in words: "By hand", "Daily", "Before a restore"… */
export const backupKind = (kind: BackupKind) => words(instanceWords).backupKinds[kind] ?? kind;

/** The Odoo series of a version ("17.0+e" and "17.0" give "17.0", "saas~17.1" gives "17.1"), as the panel reads it. */
export function series(version: string | null | undefined): string | null {
  const match = /^(?:saas~)?(\d{1,3})\.(\d{1,2})/.exec((version ?? "").trim());
  return match ? `${Number(match[1])}.${Number(match[2])}` : null;
}

/** Why a backup cannot go into an instance of this version and edition (the panel checks the same), or null. */
export function backupProblem(backup: Backup, version: string, edition: string): string | null {
  const t = words(instanceWords).picker;
  if (!backup.series) return t.unknownVersion;
  if (backup.series !== series(version)) return t.otherVersion(backup.series, series(version) ?? version);
  if (backup.edition === "enterprise" && edition !== "enterprise") return t.needsEnterprise;
  return null;
}

/**
 * Every backup the user may see (GET /backups, permission backups.manage), grouped by instance, newest first.
 * Backups that cannot go into an instance of `version` and `edition` are disabled with the reason, or left out
 * with `onlyCompatible`.
 */
export function BackupPicker({
  version,
  edition,
  value,
  onChange,
  onlyCompatible,
}: {
  version: string;
  edition: string;
  value: string | null;
  onChange: (file: string | null) => void;
  onlyCompatible?: boolean;
}) {
  const { data: backups, isPending } = useBackups();
  const [query, setQuery] = useState("");
  const t = useT(instanceWords).picker;
  const groups = useMemo(() => {
    const found = new Map<string, { backup: Backup; problem: string | null }[]>();
    for (const backup of backups ?? []) {
      const problem = backupProblem(backup, version, edition);
      if (onlyCompatible && problem) continue;
      if (query && !`${backup.instance} ${backup.db}`.includes(query.trim().toLowerCase())) continue;
      found.set(backup.instance, [...(found.get(backup.instance) ?? []), { backup, problem }]);
    }
    return [...found.entries()];
  }, [backups, version, edition, onlyCompatible, query]);

  if (isPending) return <Skeleton height={120} />;
  if (!backups?.length)
    return <Empty icon={<Archive />} title={t.noneTitle}>{t.noneText}</Empty>;
  return (
    <div className="backup-picker">
      {backups.length > 6 && (
        <div className="input-group">
          <Search />
          <Input placeholder={t.filter} value={query} onChange={(e) => setQuery(e.target.value)} aria-label={t.filterLabel} />
        </div>
      )}
      <div className="backup-picker-list" role="listbox" aria-label={t.list}>
        {groups.length === 0 && (
          <p className="small muted">{onlyCompatible ? t.noneOfVersion(series(version) ?? version) : t.noMatch}</p>
        )}
        {groups.map(([instance, rows]) => (
          <div key={instance} className="backup-picker-group">
            <h3 className="xsmall faint"><bdi>{instance}</bdi></h3>
            {rows.map(({ backup, problem }) => (
              <button
                key={backup.file}
                type="button"
                role="option"
                className="choice"
                aria-pressed={value === backup.file}
                aria-selected={value === backup.file}
                disabled={!!problem}
                title={problem ?? backup.file}
                onClick={() => onChange(value === backup.file ? null : backup.file)}
              >
                <span className="spacer">
                  <span className="t">{dateTime(backup.created)}</span> <span className="faint xsmall">· {ago(backup.created)}</span>
                  <br />
                  <span className="d">
                    {backupKind(backup.kind)} · {t.database} <span className="mono">{backup.db}</span> · {bytes(backup.size)}
                  </span>
                </span>
                {problem ? <Tag tone="negative">{problem}</Tag> : <Tag mono>{backup.series}{backup.edition === "enterprise" ? " EE" : ""}</Tag>}
              </button>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
