/**
 * The pure half of the Builder and Operator panels: ordering, the KPI
 * figures and the spend meter, so the views only draw and the hooks only
 * fetch. Tested in panel-model.test.ts.
 */

import type { AlertEvent } from "@/components/screens/alerts/use-alerts";
import type { ClusterRow } from "@/components/screens/clusters/list/use-clusters-list";
import type { AstroliftBudget, AstroliftCostForecast } from "@/graphql/billing/billing.types";
import type { AstroliftDeploymentMetrics } from "@/graphql/lifecycle/lifecycle.types";

// ---------------------------------------------------------------- alerts

const SEVERITY_RANK: Record<string, number> = {
  critical: 0,
  error: 1,
  warn: 2,
  warning: 2,
  info: 3,
};

/**
 * Firing first (not acknowledged), then acknowledged; within each, the
 * worst severity, then the newest. Resolved events sort last.
 */
export function alertsFiringFirst(events: AlertEvent[]): AlertEvent[] {
  const state = (e: AlertEvent) => (e.resolvedAt ? 2 : e.acknowledgedAt ? 1 : 0);
  return [...events].sort(
    (a, b) =>
      state(a) - state(b) ||
      (SEVERITY_RANK[a.severity] ?? 4) - (SEVERITY_RANK[b.severity] ?? 4) ||
      Date.parse(b.firedAt) - Date.parse(a.firedAt)
  );
}

// ---------------------------------------------------------------- clusters

export type ClusterHealth = "error" | "offline" | "degraded" | "never_seen" | "connected";

/** One word for a cluster's health: a failed setup outranks its heartbeat. */
export function clusterHealth(c: ClusterRow): ClusterHealth {
  if (c.lifecycle === "error") return "error";
  return c.heartbeatStatus ?? "never_seen";
}

const HEALTH_RANK: Record<ClusterHealth, number> = {
  error: 0,
  offline: 1,
  degraded: 2,
  never_seen: 3,
  connected: 4,
};

/** The least healthy first, then by name, so a healthy fleet reads alphabetically. */
export function clustersWorstFirst(fleet: ClusterRow[]): ClusterRow[] {
  return [...fleet].sort(
    (a, b) =>
      HEALTH_RANK[clusterHealth(a)] - HEALTH_RANK[clusterHealth(b)] || a.name.localeCompare(b.name)
  );
}

/** How many clusters are in each health, for the panel's one-line summary. */
export function healthCounts(fleet: ClusterRow[]): Record<ClusterHealth, number> {
  const out: Record<ClusterHealth, number> = {
    error: 0,
    offline: 0,
    degraded: 0,
    never_seen: 0,
    connected: 0,
  };
  for (const c of fleet) out[clusterHealth(c)] += 1;
  return out;
}

// ---------------------------------------------------------------- KPIs

export type KpiKey = "deploys" | "runs" | "success" | "p95" | "spend";

export interface KpiFigure {
  key: KpiKey;
  label: string;
  /** Formatted, or null while unknown (drawn as a dash). */
  value: string | null;
  /** What the number counts, when the label alone is ambiguous. */
  hint?: string;
  href: string;
}

export interface KpisPanelData {
  windowDays: number;
  figures: KpiFigure[];
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
}

export function formatDuration(seconds: number | null | undefined): string | null {
  if (seconds == null || !Number.isFinite(seconds)) return null;
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return s === 0 ? `${m}m` : `${m}m ${s}s`;
}

export function formatMoney(cents: number, currency: string): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    maximumFractionDigits: 0,
  }).format(cents / 100);
}

/**
 * The strip's figures, each only when its source is in (undefined means the
 * person may not see it, so it is left out; null means not loaded yet).
 * Runs counts the window's runs that started in the period; when the window
 * is full and all of it is in the period, the count is a floor ("100+").
 */
export function kpiFigures({
  metrics,
  runs,
  forecast,
  windowDays,
}: {
  metrics?: AstroliftDeploymentMetrics | null;
  runs?: {
    runs: Array<{ startedAt?: string | null; createdAt: string }>;
    capped: boolean;
    now: number;
    loading?: boolean;
  };
  forecast?: AstroliftCostForecast | null;
  windowDays: number;
}): KpiFigure[] {
  const out: KpiFigure[] = [];
  if (metrics !== undefined) {
    out.push({
      key: "deploys",
      label: "Deploys",
      value: metrics ? metrics.total.toLocaleString("en-US") : null,
      href: "/deployments",
    });
  }
  if (runs !== undefined) {
    const since = runs.now - windowDays * 24 * 60 * 60 * 1000;
    const inPeriod = runs.runs.filter(
      (r) => Date.parse(r.startedAt ?? r.createdAt) >= since
    ).length;
    const floor = runs.capped && inPeriod === runs.runs.length;
    out.push({
      key: "runs",
      label: "Runs",
      value:
        runs.runs.length === 0 && runs.loading
          ? null
          : `${inPeriod.toLocaleString("en-US")}${floor ? "+" : ""}`,
      hint: "agent runs",
      href: "/tasks?kind=agent",
    });
  }
  if (metrics !== undefined) {
    out.push({
      key: "success",
      label: "Success",
      value:
        metrics && metrics.total > 0
          ? `${(metrics.successRate * 100).toFixed(1)}%`
          : metrics
            ? "—"
            : null,
      hint: "of deploys",
      href: "/deployments?view=failed",
    });
    out.push({
      key: "p95",
      label: "p95",
      value: metrics ? (formatDuration(metrics.p95DurationSeconds) ?? "—") : null,
      hint: "deploy time",
      href: "/administration/metrics",
    });
  }
  if (forecast !== undefined) {
    out.push({
      key: "spend",
      label: "Spend",
      value: forecast ? formatMoney(forecast.mtdCents, forecast.currency) : null,
      hint: "month to date",
      href: "/administration/metrics",
    });
  }
  return out;
}

// ---------------------------------------------------------------- spend & quota

/** The organization's budget: the quota spend is drawn against. */
export function orgBudget(budgets: AstroliftBudget[]): AstroliftBudget | null {
  return budgets.find((b) => b.scopeKind === "ORG") ?? null;
}

export interface SpendQuotaData {
  forecast: AstroliftCostForecast | null;
  budget: AstroliftBudget | null;
  loading?: boolean;
  /** The forecast failed: nothing to draw. */
  error?: string | null;
  /** Only the budget failed: spend still draws, the bar says why it has no quota. */
  budgetError?: string | null;
  onRetry?: () => void;
}

export type MeterTone = "ok" | "warn" | "error";

export interface SpendMeter {
  /** Spend so far as a share of the budget, 0 to 1 (clamped for drawing). */
  spent: number;
  /** The projected month end as a share of the budget, 0 to 1 (clamped), or null. */
  projected: number | null;
  /** The unclamped share, for the percentage. */
  ratio: number;
  /** Over the budget's first alert threshold warns; over the budget is an error. */
  tone: MeterTone;
}

/** Spend against the budget, or null when there is no budget to draw it against. */
export function spendMeter(
  forecast: AstroliftCostForecast | null,
  budget: AstroliftBudget | null
): SpendMeter | null {
  if (!budget || budget.amountCents <= 0) return null;
  const spentCents = forecast?.mtdCents ?? budget.currentSpendCents;
  const ratio = spentCents / budget.amountCents;
  const warnAt = Math.min(...(budget.alertsAtPct.length ? budget.alertsAtPct : [80])) / 100;
  const clamp = (n: number) => Math.max(0, Math.min(1, n));
  return {
    spent: clamp(ratio),
    projected: forecast ? clamp(forecast.projectedMonthlyCents / budget.amountCents) : null,
    ratio,
    tone: ratio >= 1 ? "error" : ratio >= warnAt ? "warn" : "ok",
  };
}

// ---------------------------------------------------------------- platform activity

/**
 * A failed platform run's reason: the failure's `message` (first line), or
 * its first string value. Null for a run that did not fail.
 */
export function platformRunReason(run: { status: string; failure: unknown }): string | null {
  if (run.status !== "failed" && run.status !== "timed_out") return null;
  const f = run.failure;
  const pick = (v: unknown) => (typeof v === "string" && v.trim() ? v : null);
  const text =
    pick(f) ??
    (f && typeof f === "object"
      ? (pick((f as Record<string, unknown>).message) ??
        Object.values(f as Record<string, unknown>)
          .map(pick)
          .find(Boolean) ??
        null)
      : null);
  const first = text?.split("\n")[0]?.trim();
  return first || (run.status === "timed_out" ? "Timed out." : "No reason was recorded.");
}
