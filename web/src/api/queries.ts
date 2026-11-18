// Every read of the API as a TanStack Query hook. The live event stream (live.ts) keeps these caches current, so
// most of them never need to ask again.

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "./client";
import type {
  LicenceStatus,
  LicenceText,
  ShellOptions,
  ShellSession,
  Invitation,
  InvitationPolicy,
  Alert,
  Billing,
  Receipt,
  Account,
  CatalogPlugin,
  MailTemplate,
  CatalogPluginDetail,
  InstalledPlugin,
  MarketplaceStatus,
  PluginNotice,
  CheckoutSession,
  Recommendation,
  AuditEntry,
  Backup,
  OffsiteCopy,
  OffsiteState,
  Credentials,
  DeployKey,
  Domains,
  DocPage,
  DocsContents,
  GitHubAppState,
  Instance,
  Job,
  Metrics,
  MetricsHistory,
  Permission,
  Plan,
  Project,
  ProjectAccess,
  ProjectDetail,
  ProjectMeta,
  RepositoryOwner,
  Quota,
  RoleInfo,
  Server,
  Session,
  InstanceResources,
  Setting,
  Team,
  Token,
  CaughtMail,
  CaughtMailDetail,
  CaughtMailMeta,
  HelpInfo,
  User,
  Version,
} from "./types";

export const keys = {
  session: ["session"] as const,
  alerts: ["alerts"] as const,
  instances: ["instances"] as const,
  projects: ["projects"] as const,
  project: (id: string) => ["project", id] as const,
  projectAccess: (id: string) => ["project", id, "access"] as const,
  teams: ["teams"] as const,
  instance: (name: string) => ["instance", name] as const,
  domains: (name: string) => ["domains", name] as const,
  mail: (name: string) => ["caught-mail", name] as const,
  resources: (name: string) => ["resources", name] as const,
  jobs: (instance?: string) => ["jobs", instance ?? null] as const,
  job: (id: string) => ["job", id] as const,
  jobLog: (id: string) => ["job-log", id] as const,
  metrics: ["metrics"] as const,
  history: ["metrics-history"] as const,
  settings: ["settings"] as const,
  account: ["account"] as const,
  mailTemplates: ["mail-templates"] as const,
  plugins: ["plugins"] as const,
  pluginNotices: ["plugins", "notices"] as const,
  marketplace: ["marketplace"] as const,
  catalog: (query: string) => ["marketplace", "catalog", query] as const,
  purchases: ["marketplace", "checkout-sessions"] as const,
  licence: ["licence"] as const,
  licenceText: ["licence", "text"] as const,
};

/** The licence agreement this release bundles; the same for the whole visit. */
export const useLicenceText = () =>
  useQuery({ queryKey: keys.licenceText, queryFn: () => api.get<LicenceText>("/licence/text"), staleTime: Infinity, retry: false });
export const useLicence = () => useQuery({ queryKey: keys.licence, queryFn: () => api.get<LicenceStatus>("/licence") });

export const useSession = () =>
  useQuery({
    queryKey: keys.session,
    queryFn: async () => {
      try {
        return await api.get<Session>("/session");
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) return null;
        throw error;
      }
    },
    staleTime: Infinity,
    retry: false,
  });

export const useInstances = () =>
  useQuery({ queryKey: keys.instances, queryFn: () => api.get<Instance[]>("/instances?limit=500") });

/** Every project, and the instances outside every project (plain Odoo). */
export const useProjects = () =>
  useQuery({
    queryKey: keys.projects,
    queryFn: async () => {
      const answer = await api.page<Project[]>("/projects");
      return { projects: answer.data, standalone: (answer.meta?.standalone as string[] | undefined) ?? [] };
    },
  });

/** A project with its branches (read from GitHub at most every 30 seconds by the server). */
export const useProject = (id: string) =>
  useQuery({
    queryKey: keys.project(id),
    queryFn: async () => {
      const answer = await api.page<ProjectDetail>(`/projects/${encodeURIComponent(id)}`);
      return { project: answer.data, meta: answer.meta as unknown as ProjectMeta };
    },
  });

export const useRepositoryOwners = (enabled: boolean) =>
  useQuery({
    queryKey: ["repository-owners"],
    queryFn: async () => {
      const answer = await api.page<RepositoryOwner[]>("/projects/repository-owners");
      return { owners: answer.data, connected: Boolean(answer.meta?.connected) };
    },
    enabled,
    retry: false,
  });

export const useInstance = (name: string) =>
  useQuery({ queryKey: keys.instance(name), queryFn: () => api.get<Instance>(`/instances/${name}`) });

/** What the caller may do in an instance (its project's access and its branch's lock): GET /instances/<name>'s
 *  meta.permissions, read again when the lock changes. */
export const useInstancePermissions = (name: string, lock: string) =>
  useQuery({
    queryKey: [...keys.instance(name), "permissions", lock] as const,
    queryFn: async () => ((await api.page<Instance>(`/instances/${name}`)).meta?.permissions ?? null) as Permission[] | null,
    enabled: !!name,
    staleTime: 30_000,
  });

export const useInstanceResources = (name: string) =>
  useQuery({ queryKey: keys.resources(name), queryFn: () => api.get<InstanceResources>(`/instances/${name}/resources`) });

export const useJobs = (instance?: string, enabled = true) =>
  useQuery({
    queryKey: keys.jobs(instance),
    queryFn: () => api.get<Job[]>(`/jobs?limit=40${instance ? `&instance=${encodeURIComponent(instance)}` : ""}`),
    enabled,
  });

export const useJob = (id: string | null) =>
  useQuery({ queryKey: keys.job(id ?? ""), queryFn: () => api.get<Job>(`/jobs/${id}`), enabled: !!id });

export interface JobLog {
  text: string;
  next: number;
}
export const useJobLog = (id: string | null) =>
  useQuery({
    queryKey: keys.jobLog(id ?? ""),
    enabled: !!id,
    staleTime: Infinity,
    queryFn: async (): Promise<JobLog> => {
      const page = await api.page<{ text: string }>(`/jobs/${id}/log?offset=0`);
      return { text: page.data.text, next: Number(page.meta?.next_offset ?? 0) };
    },
  });

export const useMetrics = () => useQuery({ queryKey: keys.metrics, queryFn: () => api.get<Metrics>("/metrics") });
/** How many instances the server runs comfortably (kept current by the "metrics" live event); null until measured. */
export const useCapacity = () => useMetrics().data?.capacity ?? null;
export const useHistory = () =>
  useQuery({
    queryKey: keys.history,
    queryFn: () => api.get<MetricsHistory>("/metrics/history?minutes=60"),
    staleTime: Infinity,
  });

export const useServer = () => useQuery({ queryKey: ["server"], queryFn: () => api.get<Server>("/server") });
export const useSettings = () => useQuery({ queryKey: keys.settings, queryFn: () => api.get<Setting[]>("/settings") });
export const useVersions = () =>
  useQuery({ queryKey: ["versions"], queryFn: () => api.get<Version[]>("/versions"), staleTime: 600_000 });
export const useQuota = () => useQuery({ queryKey: ["quota"], queryFn: () => api.get<Quota>("/instance-quota") });
export const useAccount = () => useQuery({ queryKey: keys.account, queryFn: () => api.get<Account>("/account") });
export const useRoles = () =>
  useQuery({ queryKey: ["roles"], queryFn: () => api.get<{ roles: RoleInfo[] }>("/roles"), staleTime: Infinity });
export const useSetup = () =>
  useQuery({ queryKey: ["setup"], queryFn: () => api.get<{ needed: boolean; contact: string; default_language?: string }>("/setup"), retry: false });
export const useDocs = () =>
  useQuery({ queryKey: ["docs"], queryFn: () => api.get<DocsContents>("/docs"), staleTime: Infinity });
export const useDocPage = (path: string) =>
  useQuery({ queryKey: ["docs", path], queryFn: () => api.get<DocPage>(`/docs/${path}`), staleTime: Infinity, enabled: !!path });
export const useMailTemplates = () =>
  useQuery({
    queryKey: keys.mailTemplates,
    queryFn: async () => {
      const answer = await api.page<MailTemplate[]>("/mail-templates");
      return { templates: answer.data, mailReady: Boolean(answer.meta?.mail_ready) };
    },
  });
export const useTeams = (enabled = true) => useQuery({ queryKey: keys.teams, queryFn: () => api.get<Team[]>("/teams"), enabled });
export const useProjectAccess = (id: string, enabled = true) =>
  useQuery({ queryKey: keys.projectAccess(id), queryFn: () => api.get<ProjectAccess>(`/projects/${encodeURIComponent(id)}/access`), enabled });
export const useUsers = () => useQuery({ queryKey: ["users"], queryFn: () => api.get<User[]>("/users") });
export const useTokens = () => useQuery({ queryKey: ["tokens"], queryFn: () => api.get<Token[]>("/tokens") });
export const useBackups = () => useQuery({ queryKey: ["backups"], queryFn: () => api.get<Backup[]>("/backups") });
/** Off-site copies: the state (where they go, the signing key) and the copies; the live event "offsite" refreshes both. */
export const useOffsite = (enabled = true) =>
  useQuery({ queryKey: ["offsite", "state"], queryFn: () => api.get<OffsiteState>("/offsite"), enabled });
export const useOffsiteCopies = (enabled = true) =>
  useQuery({ queryKey: ["offsite", "copies"], queryFn: () => api.get<OffsiteCopy[]>("/offsite/copies"), enabled });
export const useDeployKeys = () =>
  useQuery({ queryKey: ["deploy-keys"], queryFn: () => api.get<DeployKey[]>("/deploy-keys") });
export const useGitHubApp = () =>
  useQuery({
    queryKey: ["github-app"],
    queryFn: async () => {
      const answer = await api.page<GitHubAppState>("/github/app");
      return { ...answer.data, can_manage: Boolean(answer.meta?.can_manage) };
    },
  });
export const useAudit = (limit: number) =>
  useQuery({
    queryKey: ["audit", limit],
    queryFn: () => api.get<AuditEntry[]>(`/audit?limit=${limit}`),
    placeholderData: keepPreviousData,
  });
export const useDomains = (name: string) =>
  useQuery({ queryKey: keys.domains(name), queryFn: () => api.get<Domains>(`/instances/${name}/domains`) });

export const useCredentials = (name: string, enabled: boolean) =>
  useQuery({
    queryKey: ["credentials", name],
    queryFn: () => api.get<Credentials>(`/instances/${name}/credentials`),
    enabled,
    staleTime: Infinity,
  });
/** Pending and expired invitations, and what the Invite form may offer (e-mail ready, password allowed). */
export const useInvitations = () =>
  useQuery({
    queryKey: ["invitations"],
    queryFn: async () => {
      const page = await api.page<Invitation[]>("/invitations");
      return { invitations: page.data, policy: page.meta as unknown as InvitationPolicy };
    },
  });
/** What the caller may open in the instance's Shell tab, and why not (web shell). */
export const useShellOptions = (name: string) =>
  useQuery({
    queryKey: ["shell", name],
    queryFn: () => api.get<ShellOptions>(`/instances/${name}/shell`),
  });
/** Open and past shells of an instance (super admins). */
export const useShells = (instance: string, enabled: boolean) =>
  useQuery({
    queryKey: ["shells", instance],
    queryFn: () => api.get<ShellSession[]>(`/shells?instance=${encodeURIComponent(instance)}`),
    enabled,
    refetchInterval: 15_000,
  });
export const useOdooLog = (name: string, lines: number) =>
  useQuery({
    queryKey: ["odoo-log", name, lines],
    queryFn: () => api.get<{ text: string }>(`/instances/${name}/logs?lines=${lines}`),
  });
export const usePlan = (repo: string, branch: string, name: string, version = "", edition = "") =>
  useQuery({
    queryKey: ["plan", repo, branch, name, version, edition],
    queryFn: () =>
      api.get<Plan>(
        `/instance-plan?repo=${encodeURIComponent(repo)}&branch=${encodeURIComponent(branch)}&name=${encodeURIComponent(name)}` +
          `&version=${encodeURIComponent(version)}&edition=${encodeURIComponent(edition)}`,
      ),
    staleTime: Infinity, // a plain instance's suggested name has a random part: keep the one shown
    placeholderData: keepPreviousData,
  });

export const usePlugins = () =>
  useQuery({ queryKey: keys.plugins, queryFn: () => api.get<InstalledPlugin[]>("/plugins") });
/** The latest alerts, newest first (the bell); new ones arrive as the "alert" live event. */
export const useAlerts = (enabled: boolean) =>
  useQuery({ queryKey: keys.alerts, queryFn: () => api.get<Alert[]>("/alerts?limit=30"), enabled, staleTime: Infinity });
export const usePluginNotices = (enabled: boolean) =>
  useQuery({ queryKey: keys.pluginNotices, queryFn: () => api.get<PluginNotice[]>("/plugins/notices"), enabled, staleTime: 300_000 });
export const useMarketplace = () =>
  useQuery({ queryKey: keys.marketplace, queryFn: () => api.get<MarketplaceStatus>("/marketplace") });
export const useCatalog = (query: string, enabled: boolean) =>
  useQuery({
    queryKey: keys.catalog(query),
    queryFn: () => api.get<CatalogPlugin[]>(`/marketplace/catalog?${query}`),
    enabled,
    retry: false,
    placeholderData: keepPreviousData,
  });
export const useCategories = (enabled: boolean) =>
  useQuery({
    queryKey: ["marketplace", "categories"],
    queryFn: () => api.get<{ type: string; count: number }[]>("/marketplace/categories"),
    enabled,
    retry: false,
    staleTime: 600_000,
  });
export const useCatalogPlugin = (id: string | null) =>
  useQuery({
    queryKey: ["marketplace", "plugin", id],
    queryFn: () => api.get<CatalogPluginDetail>(`/marketplace/catalog/${id}`),
    enabled: !!id,
    retry: false,
  });
export const useBilling = (enabled: boolean) =>
  useQuery({ queryKey: ["marketplace", "billing"], queryFn: () => api.get<Billing>("/marketplace/billing"), enabled, retry: false });
export const useReceipts = (enabled: boolean) =>
  useQuery({ queryKey: ["marketplace", "receipts"], queryFn: () => api.get<Receipt[]>("/marketplace/receipts"), enabled, retry: false });
export const usePurchases = (enabled: boolean) =>
  useQuery({ queryKey: keys.purchases, queryFn: () => api.get<CheckoutSession[]>("/marketplace/checkout-sessions"), enabled, retry: false });
export const useRecommendations = (enabled: boolean) =>
  useQuery({
    queryKey: ["marketplace", "recommendations"],
    queryFn: () => api.get<Recommendation[]>("/marketplace/recommendations?limit=8"),
    enabled,
    retry: false,
    staleTime: 600_000,
  });

export const useHelpInfo = (enabled = true) =>
  useQuery({ queryKey: ["help"], queryFn: () => api.get<HelpInfo>("/help"), enabled, staleTime: 300_000 });

/** An instance's caught e-mails, newest first; asked again every 10 seconds while the tab is open. */
export const useCaughtMail = (name: string, query: string, limit: number) =>
  useQuery({
    queryKey: [...keys.mail(name), query, limit],
    queryFn: async () => {
      const answer = await api.page<CaughtMail[]>(`/instances/${name}/mail?limit=${limit}&q=${encodeURIComponent(query)}`);
      return { items: answer.data, meta: answer.meta as unknown as CaughtMailMeta };
    },
    placeholderData: keepPreviousData,
    refetchInterval: 10_000,
  });

export const useCaughtMailDetail = (name: string, id: string | null) =>
  useQuery({
    queryKey: [...keys.mail(name), "detail", id],
    queryFn: () => api.get<CaughtMailDetail>(`/instances/${name}/mail/${id}`),
    enabled: !!id,
    staleTime: Infinity,
  });

/** A mutation that refreshes the given queries afterwards. */
export function useCall<TArgs, TResult>(fn: (args: TArgs) => Promise<TResult>, refresh: readonly (readonly unknown[])[] = []) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => Promise.all(refresh.map((queryKey) => client.invalidateQueries({ queryKey }))),
  });
}
