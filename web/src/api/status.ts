// A project's status (GET /projects/<id>/status), its public page, and its notification channels
// (/projects/<id>/notifications): the shapes the API answers (src/sudoo/web/v1/project_status.py, notifications.py)
// and the queries that read them.

import { useQuery } from "@tanstack/react-query";
import { api } from "./client";

export type TargetState = "up" | "degraded" | "down";
export type TargetKind = "instance" | "backups" | "offsite" | "server";
export type Overall = "operational" | "degraded" | "down" | "unknown";

export interface StatusDay {
  /** the UTC day's start, Unix seconds */
  day: number;
  /** % of checked minutes that were up; null without checks */
  uptime: number | null;
  avg_ms?: number | null;
  degraded?: number;
}

export interface StatusTarget {
  id: string;
  kind: TargetKind;
  name: string;
  branch?: string | null;
  state: TargetState | null;
  since?: number | null;
  checked_at?: number | null;
  detail?: string | null;
  response_ms?: number | null;
  uptime: { day: number | null; week: number | null; month: number | null };
  days: StatusDay[];
  hourly_ms?: (number | null)[] | null;
}

export interface Incident {
  id: string;
  target: string;
  kind: TargetKind;
  name: string;
  start: number;
  end: number | null;
  duration: number;
  severity: "down" | "degraded";
  what: string;
}

export interface PublicPageState {
  enabled: boolean;
  allowed: boolean;
  path: string | null;
}

export interface ProjectStatus {
  project: string;
  state: Overall;
  checks: boolean;
  interval_seconds: number;
  slow_ms: number;
  production: boolean;
  targets: StatusTarget[];
  incidents: Incident[];
  /** null for those who may not manage the project */
  public: PublicPageState | null;
}

export interface PublicStatus {
  name: string;
  state: Overall;
  targets: Pick<StatusTarget, "kind" | "name" | "state" | "uptime" | "days">[];
  at: number;
}

export type ChannelType = "slack" | "discord" | "teams" | "telegram" | "webhook" | "email";
export type NotificationEvent =
  | "build_failed"
  | "build_warning"
  | "build_recovered"
  | "stage_changed"
  | "promoted"
  | "production_down"
  | "production_up"
  | "backup_failed"
  | "restore_done"
  | "storage_over";

export interface Delivery {
  at: number;
  ok?: boolean;
  result?: string;
  event: string;
  attempts: number;
  retry_at?: number;
  test?: boolean;
}

export interface Channel {
  id: string;
  project: string;
  type: ChannelType;
  name: string;
  events: NotificationEvent[];
  enabled: boolean;
  config: { chat_id?: string; addresses?: string[] };
  /** each secret's last 4 characters ("…abcd"), never the secret itself; null when not set */
  secrets: Record<string, string | null>;
  created_at: number | null;
  created_by: string | null;
  last: Delivery | null;
  /** a webhook's signing secret: only in the answer that made it */
  signing_secret?: string;
}

export interface Notifications {
  project: string;
  enabled: boolean;
  channels: Channel[];
  events: NotificationEvent[];
  default_events: NotificationEvent[];
  types: ChannelType[];
  limit: number;
}

export const statusKeys = {
  status: (id: string) => ["project", id, "status"] as const,
  notifications: (id: string) => ["project", id, "notifications"] as const,
};

/** The project's status, read again every half minute while the tab is open. */
export const useProjectStatus = (id: string) =>
  useQuery({
    queryKey: statusKeys.status(id),
    queryFn: () => api.get<ProjectStatus>(`/projects/${encodeURIComponent(id)}/status`),
    refetchInterval: 30_000,
  });

export const useNotifications = (id: string, enabled = true) =>
  useQuery({
    queryKey: statusKeys.notifications(id),
    queryFn: () => api.get<Notifications>(`/projects/${encodeURIComponent(id)}/notifications`),
    enabled,
  });
