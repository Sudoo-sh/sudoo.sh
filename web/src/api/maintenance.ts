// Maintenance windows (src/sudoo/web/v1/maintenance.py): what Sudoo.sh planned on this panel, for the panel's people
// (/maintenance) and for clients (/client/maintenance), and choosing the time. Asked again every minute: a countdown
// is drawn from these times, and the banner follows the status.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { call } from "./client";
import type { ClientActionAnswer, MaintenanceItem, MaintenanceMeta, MaintenancePreset } from "./types";

const REFRESH_MS = 60_000;
const enc = encodeURIComponent;

export const maintenanceKeys = {
  staff: ["maintenance"] as const,
  client: ["client", "maintenance"] as const,
};

export interface MaintenanceList {
  items: MaintenanceItem[];
  meta: MaintenanceMeta;
}

const read = async (path: string): Promise<MaintenanceList> => {
  const answer = await call<MaintenanceItem[]>("GET", path);
  return { items: answer.data, meta: answer.meta as unknown as MaintenanceMeta };
};

export const useMaintenance = (client: boolean, enabled = true) =>
  useQuery({
    queryKey: client ? maintenanceKeys.client : maintenanceKeys.staff,
    queryFn: () => read(client ? "/client/maintenance" : "/maintenance"),
    refetchInterval: REFRESH_MS,
    enabled,
    retry: false,
  });

export type WindowChoice = { preset: MaintenancePreset } | { start: string };

/** Choose a window: 200 at once, or 202 with a confirmation the client confirms by e-mail. */
export function useChooseWindow(client: boolean) {
  const queries = useQueryClient();
  return useMutation({
    mutationFn: ({ id, choice }: { id: string; choice: WindowChoice }) =>
      call<ClientActionAnswer | MaintenanceItem>(
        "PUT",
        `${client ? "/client" : ""}/maintenance/${enc(id)}/window`,
        choice,
      ),
    onSettled: () => queries.invalidateQueries({ queryKey: client ? maintenanceKeys.client : maintenanceKeys.staff }),
  });
}

/** A client lets the maintenance run on its closed or locked project. */
export function useApproveMaintenance() {
  const queries = useQueryClient();
  return useMutation({
    mutationFn: ({ id, project }: { id: string; project: string }) =>
      call<ClientActionAnswer>("POST", `/client/maintenance/${enc(id)}/approve`, { project }),
    onSettled: () => queries.invalidateQueries({ queryKey: maintenanceKeys.client }),
  });
}
