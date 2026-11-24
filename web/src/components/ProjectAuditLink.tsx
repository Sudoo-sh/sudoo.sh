// The project header's link to the audit log, filtered to the project (shown to those who may read the log).

import { ScrollText } from "lucide-react";
import { Link } from "react-router";
import { useCan } from "@/components/Shell";
import { useT } from "@/i18n";
import words from "@/i18n/github-check";

export function ProjectAuditLink({ project }: { project: string }) {
  const t = useT(words);
  const can = useCan();
  if (!can("audit.view")) return null;
  return (
    <Link
      to={`/activity?project=${encodeURIComponent(project)}`}
      viewTransition
      className="small row"
      title={t.auditLogHint}
    >
      <ScrollText size={14} /> {t.auditLog}
    </Link>
  );
}
