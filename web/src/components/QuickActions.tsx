import { ScrollText, Trash2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { useInstancePermissions } from "@/api/queries";
import type { Instance } from "@/api/types";
import { useT } from "@/i18n";
import common from "@/i18n/common";
import dashboard from "@/i18n/dashboard";
import { DeleteDialog } from "@/pages/Instance";
import { Button } from "./app/basics";
import { Extend, useExtendChoices } from "./ExpiringSoon";
import { useCan } from "./Session";

/** The few things done most often to an instance, right on its card: only those the person may do (the role, narrowed
 *  by the project's access and the branch's lock, as everywhere else). Deleting asks the same confirmation as the
 *  instance page. */
export function QuickActions({ instance }: { instance: Instance }) {
  const t = useT(dashboard).card;
  const remove = useT(common).common.delete;
  const roleCan = useCan();
  const choices = useExtendChoices();
  const [deleting, setDeleting] = useState(false);
  const { data: allowed } = useInstancePermissions(
    instance.name,
    JSON.stringify([instance.lock ?? null, instance.client_access ?? null]),
  );
  const may = (permission: "instances.operate" | "instances.delete" | "instances.logs") =>
    roleCan(permission) && (!allowed || allowed.includes(permission));
  const failing = !!instance.update_failure || instance.status === "unreachable";
  return (
    <div className="ic-actions row-wrap">
      {failing && may("instances.logs") && (
        <Button asChild variant="ghost" size="sm">
          <Link to={`/i/${instance.name}/logs`} aria-label={t.errorsLabel(instance.name)}>
            <ScrollText /> {t.errors}
          </Link>
        </Button>
      )}
      {instance.expires_at && !instance.expired_at && may("instances.operate") && (
        <Extend instance={instance} choices={choices} />
      )}
      {may("instances.delete") && (
        <Button
          variant="ghost"
          size="sm"
          icon={<Trash2 />}
          aria-label={t.deleteLabel(instance.name)}
          onClick={() => setDeleting(true)}
        >
          {remove}
        </Button>
      )}
      {deleting && (
        <DeleteDialog
          name={instance.name}
          production={instance.stage === "production"}
          onClose={() => setDeleting(false)}
        />
      )}
    </div>
  );
}
