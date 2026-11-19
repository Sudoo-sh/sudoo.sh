// What GET /api/v1/premium answers (src/sudoo/premium/loader.py overview).

export type PremiumId = "teams" | "domains" | "offsite" | "support" | "servers";
export type PremiumStatus = "active" | "pending" | "restart_pending" | "failed" | "switched_off" | "not_included";

export interface PremiumRoute {
  path: string;
  module: string;
  export: string;
  permission: string | null;
  nav: { group?: string; icon?: string; label?: string } | null;
}

export interface PremiumSection {
  slot: string;
  module: string;
  export: string;
  permission: string | null;
}

export interface PremiumUi {
  routes: PremiumRoute[];
  sections: PremiumSection[];
  styles: string[];
  strings: string | null;
  files: Record<string, string>;
}

export interface PremiumPackage {
  id: PremiumId;
  name: string;
  features: string[];
  plans: string;
  included: boolean;
  status: PremiumStatus;
  version: string | null;
  installed_at: number | null;
  problem?: string | null;
  binding_until?: number | null;
  ui?: PremiumUi;
}

export interface PremiumOverview {
  packages: PremiumPackage[];
  auto_install: boolean;
  marketplace: boolean;
  last_checked_at: number | null;
  last_error: string | null;
  job: string | null;
  build: { python_tag: string; platform: string };
}
