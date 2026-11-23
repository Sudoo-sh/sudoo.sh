// The client portal's reads (src/sudoo/web/v1/clients.py): the client's own side, and what staff see of a project's
// clients. The portal asks again every few seconds while it is open (a client receives few live events).

import { useQuery } from "@tanstack/react-query";
import { api, call } from "./client";
import type {
  AccessRequest,
  ActivityEntry,
  ClientBackup,
  ClientProject,
  ClientProjectMeta,
  ClientProjectSummary,
  PortalInfo,
  ProjectClients,
  ProjectClientsMeta,
} from "./types";

const REFRESH_MS = 15_000;
const enc = encodeURIComponent;

export const clientKeys = {
  portal: ["portal"] as const,
  mine: ["client", "projects"] as const,
  project: (id: string) => ["client", "project", id] as const,
  backups: (id: string) => ["client", "project", id, "backups"] as const,
  activity: (id: string, kind: string, offset: number) => ["client", "project", id, "activity", kind, offset] as const,
  staff: (id: string) => ["project", id, "clients"] as const,
};

/** Whether this address is the client portal's, its company name and colour (public). */
export const usePortal = () =>
  useQuery({ queryKey: clientKeys.portal, queryFn: () => api.get<PortalInfo>("/portal"), staleTime: Infinity, retry: false });

export const useClientProjects = () =>
  useQuery({
    queryKey: clientKeys.mine,
    queryFn: () => call<ClientProjectSummary[]>("GET", "/client/projects"),
    refetchInterval: REFRESH_MS,
  });

export const useClientProject = (id: string) =>
  useQuery({
    queryKey: clientKeys.project(id),
    queryFn: async () => {
      const answer = await call<ClientProject>("GET", `/client/projects/${enc(id)}`);
      return { project: answer.data, meta: answer.meta as unknown as ClientProjectMeta };
    },
    refetchInterval: REFRESH_MS,
  });

export const useClientBackups = (id: string, enabled: boolean) =>
  useQuery({ queryKey: clientKeys.backups(id), queryFn: () => api.get<ClientBackup[]>(`/client/projects/${enc(id)}/backups`), enabled });

export const useClientActivity = (id: string, kind: "all" | "access", offset: number, limit = 50) =>
  useQuery({
    queryKey: clientKeys.activity(id, kind, offset),
    queryFn: () => call<ActivityEntry[]>("GET", `/client/projects/${enc(id)}/activity?kind=${kind}&limit=${limit}&offset=${offset}`),
    refetchInterval: REFRESH_MS,
  });

export const activityExportUrl = (id: string, kind: "all" | "access") => `/api/v1/client/projects/${enc(id)}/activity/export?kind=${kind}`;

/** What staff see of a project's clients (GET /projects/<id>/clients). */
export const useProjectClients = (id: string, enabled = true) =>
  useQuery({
    queryKey: clientKeys.staff(id),
    queryFn: async () => {
      const answer = await call<ProjectClients>("GET", `/projects/${enc(id)}/clients`);
      return { clients: answer.data, meta: answer.meta as unknown as ProjectClientsMeta };
    },
    enabled,
  });

export const clientCalls = {
  setSwitch: (id: string, body: { branch?: string | null; state: string; hours?: number; inherit?: boolean }) =>
    api.put(`/client/projects/${enc(id)}/switch`, body),
  setAllow: (id: string, people: string[]) => api.put(`/client/projects/${enc(id)}/allow`, { people }),
  setEmergency: (id: string, allowed: boolean) => api.put(`/client/projects/${enc(id)}/emergency`, { allowed }),
  endPass: (id: string, passId: string) => api.del(`/client/projects/${enc(id)}/passes/${enc(passId)}`),
  approve: (requestId: string, body: { hours?: number; people?: string[]; note?: string }) =>
    api.post<AccessRequest>(`/client/requests/${enc(requestId)}/approve`, body),
  refuse: (requestId: string, note: string) => api.post<AccessRequest>(`/client/requests/${enc(requestId)}/refuse`, { note }),
  ask: (id: string, body: { branch?: string | null; reason: string; hours: number }) =>
    api.post<AccessRequest>(`/projects/${enc(id)}/access-requests`, body),
  emergency: (id: string, body: { branch?: string | null; reason: string; confirm: string }) =>
    api.post(`/projects/${enc(id)}/emergency-access`, body),
  addClient: (id: string, body: { email: string; first_name?: string; last_name?: string; backups: boolean; shell: boolean }) =>
    api.post<{ invited: boolean }>(`/projects/${enc(id)}/clients`, body),
  changeClient: (id: string, name: string, body: { backups: boolean; shell: boolean }) =>
    api.patch(`/projects/${enc(id)}/clients/${enc(name)}`, body),
  removeClient: (id: string, name: string) => api.del(`/projects/${enc(id)}/clients/${enc(name)}`),
};
