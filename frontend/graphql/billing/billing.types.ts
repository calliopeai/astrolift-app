export type AstroliftGuid = string;

export interface AstroliftQuota {
  id: AstroliftGuid;
  scopeKind: "ORG" | "TEAM" | "PROJECT";
  scopeId: string;
  resource:
    | "apps"
    | "preview_envs"
    | "managed_services"
    | "cpu"
    | "memory"
    | "storage"
    | "egress_gb"
    | "requests_per_month";
  hardLimit: number;
  softLimit: number;
  currentUsage: number;
}

export interface AstroliftBudget {
  id: AstroliftGuid;
  scopeKind: "ORG" | "TEAM" | "PROJECT";
  scopeId: string;
  amountCents: number;
  currency: string;
  period: "monthly" | "quarterly" | "annual";
  currentSpendCents: number;
  alertsAtPct: number[];
}

export interface AstroliftCostSnapshot {
  id: AstroliftGuid;
  projectId: string | null;
  registeredAppId: string | null;
  takenAt: string;
  by: "workload" | "managed_service" | "egress" | "storage" | "other";
  amountCents: number;
  currency: string;
  source: "provider_estimate" | "platform_meter";
}
