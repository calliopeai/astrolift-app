/**
 * Billing types — facade over the codegen output.
 *
 * Frontend keeps the narrow string unions for switch-exhaustiveness;
 * the schema carries those fields as plain `String!`.
 */

import type {
  AstroliftBudget as GeneratedBudget,
  AstroliftCostAttribution as GeneratedCostAttribution,
  AstroliftCostBindingRow as GeneratedCostBindingRow,
  AstroliftCostForecast as GeneratedCostForecast,
  AstroliftCostSnapshot as GeneratedCostSnapshot,
  AstroliftCostTrendPoint as GeneratedCostTrendPoint,
  AstroliftQuota as GeneratedQuota,
  CostWindow as GeneratedCostWindow,
  ForecastConfidence as GeneratedForecastConfidence,
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

export type CostWindow = GeneratedCostWindow;
export type ForecastConfidence = GeneratedForecastConfidence;

export type AstroliftCostTrendPoint = GeneratedCostTrendPoint;

export type AstroliftCostForecast = GeneratedCostForecast;

export type AstroliftCostBindingRow = Omit<GeneratedCostBindingRow, "by"> & {
  by: CostKind;
};

export type AstroliftCostAttribution = Omit<GeneratedCostAttribution, "attributedRows"> & {
  attributedRows: AstroliftCostBindingRow[];
};
