/**
 * Billing types — facade over the codegen output.
 *
 * Frontend keeps the narrow string unions for switch-exhaustiveness;
 * the schema carries those fields as plain `String!`.
 */

import type {
  AstroliftBudget as GeneratedBudget,
  AstroliftCostSnapshot as GeneratedCostSnapshot,
  AstroliftQuota as GeneratedQuota,
} from "@/graphql/__generated__/schema";

export type AstroliftGuid = string;

type BillingScopeKind = "ORG" | "TEAM" | "PROJECT";
type BillingPeriod = "monthly" | "quarterly" | "annual";
type CostKind = "workload" | "managed_service" | "egress" | "storage" | "other";
type CostSource = "provider_estimate" | "platform_meter";
type QuotaResource =
  | "apps"
  | "preview_envs"
  | "managed_services"
  | "cpu"
  | "memory"
  | "storage"
  | "egress_gb"
  | "requests_per_month";

export type AstroliftQuota = Omit<GeneratedQuota, "scopeKind" | "resource"> & {
  scopeKind: BillingScopeKind;
  resource: QuotaResource;
};

export type AstroliftBudget = Omit<GeneratedBudget, "scopeKind" | "period"> & {
  scopeKind: BillingScopeKind;
  period: BillingPeriod;
};

export type AstroliftCostSnapshot = Omit<GeneratedCostSnapshot, "by" | "source"> & {
  by: CostKind;
  source: CostSource;
};
