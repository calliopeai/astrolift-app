/**
 * Resource quota types over the codegen output.
 *
 * Frontend keeps the narrow string unions for switch-exhaustiveness;
 * the schema carries those fields as plain `String!`.
 */

import type {
  AstroliftQuota as GeneratedQuota,
  AstroliftQuotaIncreaseRequest as GeneratedQuotaIncreaseRequest,
  AstroliftQuotaUsagePoint as GeneratedQuotaUsagePoint,
} from "@/graphql/__generated__/schema";

export type AstroliftGuid = string;

type QuotaScopeKind = "ORG" | "TEAM" | "PROJECT";
type QuotaResource =
  | "apps"
  | "preview_envs"
  | "managed_services"
  | "cpu"
  | "gpu"
  | "memory"
  | "storage"
  | "egress_gb"
  | "requests_per_month";

export type QuotaIncreaseRequestStatus = "pending" | "approved" | "rejected";

export type AstroliftQuotaIncreaseRequest = Omit<GeneratedQuotaIncreaseRequest, "status"> & {
  status: QuotaIncreaseRequestStatus;
};

export type AstroliftQuota = Omit<GeneratedQuota, "scopeKind" | "resource" | "pendingRequest"> & {
  scopeKind: QuotaScopeKind;
  resource: QuotaResource;
  pendingRequest: AstroliftQuotaIncreaseRequest | null;
};

export type AstroliftQuotaUsagePoint = GeneratedQuotaUsagePoint;
