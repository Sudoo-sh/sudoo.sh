// Settings › Servers: the Kubernetes engine's cluster (beta). Its nodes with their status and room, and "Add a node",
// which shows the `k3s agent` command with a join token once (POST /kubernetes/join-command; several servers is a
// premium feature: without it the plan card says which plans have it).

import { useQuery } from "@tanstack/react-query";
import { Boxes, Plus } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { ApiError, api, errorText } from "@/api/client";
import type { KubernetesJoin, KubernetesOverview } from "@/api/types";
import { useCan } from "@/components/Session";
import { Button, Callout, CopyField, Skeleton, Tag } from "@/components/app/basics";
import { Modal, useToast } from "@/components/app/overlay";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { PlanCard } from "@/premium/components";
import { useT } from "@/i18n";
import words from "@/i18n/kubernetes";
import { bytes, dateTime } from "@/lib/format";

export const kubernetesKey = ["kubernetes"] as const;

export const useKubernetes = (enabled = true) =>
  useQuery({ queryKey: kubernetesKey, queryFn: () => api.get<KubernetesOverview>("/kubernetes"), enabled, refetchInterval: 30_000 });

const cores = (value: number) => (Math.round(value * 10) / 10).toString();

export function KubernetesCard() {
  const t = useT(words);
  const can = useCan();
  const toast = useToast();
  const { data, isPending } = useKubernetes(can("settings.manage"));
  const [joining, setJoining] = useState(false);
  const [joined, setJoined] = useState<KubernetesJoin | null>(null);
  const [premium, setPremium] = useState(false);
  if (!can("settings.manage")) return null;

  const addNode = async () => {
    setJoining(true);
    try {
      setJoined(await api.post<KubernetesJoin>("/kubernetes/join-command"));
    } catch (error) {
      if (error instanceof ApiError && error.status === 402) setPremium(true);
      else toast("error", t.notAdded, errorText(error));
    } finally {
      setJoining(false);
    }
  };

  return (
    <section className="card">
      <div className="card-head">
        <Boxes />
        <div className="spacer">
          <h2>{t.cluster}</h2>
          <div className="sub">{t.clusterText}</div>
        </div>
        <Button asChild variant="ghost" size="sm"><Link to="/help/docs/KUBERNETES_ENGINE.md" viewTransition>{t.help}</Link></Button>
        {data?.enabled && data.reachable && (
          <Button size="sm" icon={<Plus />} loading={joining} disabled={!data.join.url_set} onClick={addNode}>{t.addNode}</Button>
        )}
      </div>
      <div className="card-body stack">
        {isPending || !data ? (
          <Skeleton height={80} />
        ) : !data.enabled ? (
          <p className="small muted">{t.off("sudo sudoo-install engine kubernetes", "KUBERNETES_ENGINE_BETA=true")}</p>
        ) : !data.reachable ? (
          <Callout tone="warning">{t.unreachable} {data.problem}</Callout>
        ) : (
          <>
            {!data.join.url_set && <p className="xsmall faint">{t.needsUrl("KUBERNETES_JOIN_URL")}</p>}
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t.node}</TableHead>
                  <TableHead>{t.status}</TableHead>
                  <TableHead>{t.roles}</TableHead>
                  <TableHead>{t.cpu}</TableHead>
                  <TableHead>{t.memory}</TableHead>
                  <TableHead>{t.version}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.nodes.map((node) => (
                  <TableRow key={node.name}>
                    <TableCell><bdi dir="ltr" className="mono">{node.name}</bdi>{node.address && <div className="xsmall faint"><bdi dir="ltr">{node.address}</bdi></div>}</TableCell>
                    <TableCell>
                      <Tag tone={node.ready ? "positive" : "negative"}>{node.ready ? t.ready : t.notReady}</Tag>
                      {!node.schedulable && <> <Tag tone="caution">{t.paused}</Tag></>}
                    </TableCell>
                    <TableCell className="small"><bdi dir="ltr">{node.roles.join(", ")}</bdi></TableCell>
                    <TableCell className="small">{t.cores(node.cpu_used == null ? null : cores(node.cpu_used), cores(node.cpu))}</TableCell>
                    <TableCell className="small">{t.used(node.memory_used_bytes == null ? null : bytes(node.memory_used_bytes), bytes(node.memory_bytes))}</TableCell>
                    <TableCell className="small mono"><bdi dir="ltr">{node.version}</bdi></TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </>
        )}
      </div>
      {joined && (
        <Modal title={t.addTitle} description={t.addText} wide onClose={() => setJoined(null)}
          footer={<Button variant="primary" onClick={() => setJoined(null)}>{t.done}</Button>}>
          <div className="stack">
            <CopyField value={joined.command} block />
            <Callout tone="warning">{t.addOnce} {t.expires(dateTime(joined.expires))}</Callout>
            <p className="xsmall faint">{t.ports}</p>
          </div>
        </Modal>
      )}
      {premium && (
        <Modal title={t.addTitle} onClose={() => setPremium(false)}>
          <PlanCard id="servers" compact />
        </Modal>
      )}
    </section>
  );
}
