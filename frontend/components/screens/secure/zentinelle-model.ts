/**
 * The Zentinelle integration screen's shapes and rules (#1888): which tabs
 * a viewer gets, how a cluster gateway's health reads, and how a scope
 * filter becomes query variables. The server enforces every permission;
 * this side only decides what to offer.
 */
import type {
  ZentinelleAuditPageQuery,
  ZentinelleClusterFieldsFragment,
  ZentinelleConnectionFieldsFragment,
  ZentinelleHealthQuery,
  ZentinellePoliciesQuery,
  ZentinelleUsageQuery,
} from "@/graphql/__generated__/operations";
import type { PermissionCheck } from "@/lib/permissions/astrolift-permissions";

export type ZentinelleConnection = ZentinelleConnectionFieldsFragment;
export type ZentinelleCluster = ZentinelleClusterFieldsFragment;
export type ZentinelleUsageRow = ZentinelleUsageQuery["astroliftZentinelleUsage"]["rows"][number];
export type ZentinellePolicy = ZentinellePoliciesQuery["astroliftZentinellePolicies"][number];
export type ZentinelleAuditEvent =
  ZentinelleAuditPageQuery["astroliftZentinelleAuditPage"]["events"][number];
export type ZentinelleHealthCounts = ZentinelleHealthQuery["astroliftZentinelleHealth"];
export type ZentinelleBudget = NonNullable<ZentinelleHealthCounts["budgets"]>[number];

export const ZENTINELLE_TABS = ["overview", "clusters", "usage", "policies", "audit"] as const;
export type ZentinelleTab = (typeof ZENTINELLE_TABS)[number];

/** What the viewer may see and do, from their zentinelle.* grants. */
export interface ZentinelleAccess {
  status: boolean;
  connect: boolean;
  gatewayManage: boolean;
  usage: boolean;
  policies: boolean;
  audit: boolean;
  auditExport: boolean;
}

export function zentinelleAccess(can: (check: PermissionCheck) => boolean): ZentinelleAccess {
  return {
    status: can("zentinelle.status_view"),
    connect: can("zentinelle.connect"),
    gatewayManage: can("zentinelle.gateway_manage"),
    usage: can("zentinelle.usage_view"),
    policies: can("zentinelle.policy_view"),
    audit: can("zentinelle.audit_view"),
    auditExport: can("zentinelle.audit_export"),
  };
}

export function hasAnyAccess(access: ZentinelleAccess): boolean {
  return Object.values(access).some(Boolean);
}

/**
 * The tabs a viewer gets. Before the organization is connected only the
 * overview exists (it holds the connect form); after, each section needs
 * its own read.
 */
export function visibleTabs(access: ZentinelleAccess, connected: boolean): ZentinelleTab[] {
  if (!connected) return ["overview"];
  return ZENTINELLE_TABS.filter((tab) => {
    switch (tab) {
      case "overview":
        return access.status || access.usage;
      case "clusters":
        return access.status;
      case "usage":
        return access.usage;
      case "policies":
        return access.policies;
      case "audit":
        return access.audit;
    }
  });
}

export type GatewayHealth = "healthy" | "stale" | "pending" | "off" | "error";

/** Zentinelle reads a gateway not heard from for this long as stale (#2436). */
export const GATEWAY_STALE_AFTER_MS = 5 * 60_000;

/**
 * One cluster's gateway at a glance: an error while the last operation's
 * failure stands, off when turned off for the cluster or the install, else
 * healthy once deployed and pending until then. A deployed gateway Zentinelle
 * last heard from more than five minutes ago is stale; one Zentinelle cannot
 * say anything about (an older release) stays healthy, as before.
 */
export function gatewayHealth(
  cluster: ZentinelleCluster,
  installEnabled: boolean,
  now: number = Date.now()
): GatewayHealth {
  if (cluster.lastError || cluster.status === "error") return "error";
  if (!cluster.gatewayEnabled || !installEnabled) return "off";
  if (!cluster.gatewayDeployed) return "pending";
  if (cluster.lastSeenAt && now - Date.parse(cluster.lastSeenAt) > GATEWAY_STALE_AFTER_MS) {
    return "stale";
  }
  return "healthy";
}

export const HEALTH_DOT = {
  healthy: "ok",
  stale: "warn",
  pending: "warn",
  off: "muted",
  error: "error",
} as const;

/** Registered clusters, without the ones unregistered since. */
export function liveClusters(connection: ZentinelleConnection | null): ZentinelleCluster[] {
  return connection?.clusters.filter((c) => !c.unregistered) ?? [];
}

/** True when any registered cluster's gateway needs attention. */
export function isDegraded(connection: ZentinelleConnection | null): boolean {
  if (!connection) return false;
  return liveClusters(connection).some((c) => {
    const health = gatewayHealth(c, connection.gatewayFeatureEnabled);
    return health === "error" || health === "pending" || health === "stale";
  });
}

/** The budget closest to its limit, or null when none is set. */
export function tightestBudget(budgets: readonly ZentinelleBudget[]): ZentinelleBudget | null {
  return budgets.reduce<ZentinelleBudget | null>(
    (worst, b) => (worst === null || b.percent > worst.percent ? b : worst),
    null
  );
}

/** Narrow a read to one agent, project or team; the server takes one at most. */
export type ZentinelleScope =
  | { kind: "all" }
  | { kind: "agent"; slug: string }
  | { kind: "project"; id: string }
  | { kind: "team"; id: string };

export const ALL_SCOPE: ZentinelleScope = { kind: "all" };

export function scopeVariables(scope: ZentinelleScope) {
  return {
    agentSlug: scope.kind === "agent" ? scope.slug : null,
    projectId: scope.kind === "project" ? scope.id : null,
    teamId: scope.kind === "team" ? scope.id : null,
  };
}

export interface ScopeOption {
  id: string;
  name: string;
}

export interface ZentinelleScopeOptions {
  projects: ScopeOption[];
  teams: ScopeOption[];
}

export const WINDOWS = ["24h", "7d", "30d", "month"] as const;
export type ZentinelleWindow = (typeof WINDOWS)[number];

/** The start of a window, as an ISO timestamp, from `now`. */
export function windowStart(range: ZentinelleWindow, now: Date): string {
  if (range === "month") {
    return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1)).toISOString();
  }
  const hours = { "24h": 24, "7d": 24 * 7, "30d": 24 * 30 }[range];
  return new Date(now.getTime() - hours * 3600_000).toISOString();
}

export interface UsageTotals {
  requests: number;
  inputTokens: number;
  outputTokens: number;
  costUsd: number;
  agents: number;
}

export function usageTotals(rows: ZentinelleUsageRow[]): UsageTotals {
  return {
    requests: rows.reduce((n, r) => n + r.requests, 0),
    inputTokens: rows.reduce((n, r) => n + r.inputTokens, 0),
    outputTokens: rows.reduce((n, r) => n + r.outputTokens, 0),
    costUsd: rows.reduce((n, r) => n + r.costUsd, 0),
    agents: new Set(rows.map((r) => r.agentSlug).filter(Boolean)).size,
  };
}

/** One read's state, as a panel renders it. */
export interface ReadState<T> {
  data: T;
  loading: boolean;
  error: string | null;
  onRetry?: () => void;
}
