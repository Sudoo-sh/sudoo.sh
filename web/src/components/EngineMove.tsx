// An instance's engine (Docker, or Kubernetes in beta): the engines this panel offers, and "Move to another engine"
// (POST /instances/<name>/actions/change-engine, a job: switched off, backed up, created on the other engine from the
// backup, started; put back where it was when it does not start).

import { useQuery } from "@tanstack/react-query";
import { ArrowRightLeft } from "lucide-react";
import { api } from "@/api/client";
import type { Engine, Instance, Plan } from "@/api/types";
import { Button, Callout } from "@/components/app/basics";
import { Modal } from "@/components/app/overlay";
import { useT } from "@/i18n";
import common from "@/i18n/common";
import words from "@/i18n/kubernetes";

/** The engines a new instance may use now (GET /instance-plan): ["docker"] while the Kubernetes beta is off. */
export const useOfferedEngines = (version: string, enabled: boolean) =>
  useQuery({
    queryKey: ["engines", version],
    queryFn: () => api.get<Plan>(`/instance-plan?version=${encodeURIComponent(version)}&edition=community`),
    enabled,
    staleTime: 60_000,
    select: (plan): Engine[] => plan.engines ?? ["docker"],
  });

/** Where the instance would move: the other engine, when this panel offers it (back to Docker always). */
export function otherEngine(instance: Instance, offered: Engine[] | undefined): Engine | null {
  const current = instance.engine ?? "docker";
  if (current === "kubernetes") return "docker";
  return offered?.includes("kubernetes") && instance.odoo.edition !== "enterprise" ? "kubernetes" : null;
}

export function EngineMoveDialog({
  instance,
  to,
  onClose,
  onRun,
}: {
  instance: Instance;
  to: Engine;
  onClose: () => void;
  onRun: () => void;
}) {
  const t = useT(words);
  const c = useT(common).common;
  const target = to === "kubernetes" ? t.kubernetes : t.docker;
  return (
    <Modal
      title={t.moveTitle(target)}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>{c.cancel}</Button>
          <Button
            variant="primary"
            icon={<ArrowRightLeft />}
            onClick={() => {
              onRun();
              onClose();
            }}
          >
            {t.moveConfirm}
          </Button>
        </>
      }
    >
      <div className="stack">
        <p className="small">{t.moveText(instance.name, target)}</p>
        <Callout tone="warning">
          {instance.stage === "production" && <b>{t.moveProduction} </b>}
          {t.moveDowntime}
        </Callout>
      </div>
    </Modal>
  );
}
